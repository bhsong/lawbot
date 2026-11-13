"""Select ERP reference items from monitor's bare git clones with one rule per source kind.

Reads only blobs already present locally: lazy fetching is disabled, so a partial clone never
contacts its remote or writes objects. A missing blob is reported as a source error.
"""

import datetime
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lawbot_common.erp_rules import IDENTIFIER_PATTERN

ADMRULE_REPO = 'admrule-kr'
LAW_REPO = 'legalize-kr'
EXCERPT_LIMIT = 4000
GIT_ENV = {**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0'}


class SourceError(RuntimeError):
    """A configured source could not be read from the local clone."""


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    category: str
    admrule: dict | None = None
    form: dict | None = None
    article: dict | None = None


@dataclass(frozen=True)
class Found:
    """One selected document; `data` is the public record and `full_text` the untruncated body for hashing."""

    source: Source
    ref: str
    repo: str
    commit: str
    data: dict = field(default_factory=dict)
    full_text: str = ''


def load_sources(path: Path) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    sources = [Source(**entry) for entry in raw['sources']]
    seen = set()
    for source in sources:
        if not isinstance(source.key, str) or not re.fullmatch(IDENTIFIER_PATTERN, source.key):
            raise ValueError(f'invalid ERP source key: {source.key!r}')
        if source.key in seen:
            raise ValueError(f'duplicate ERP source key: {source.key}')
        seen.add(source.key)
        if sum(rule is not None for rule in (source.admrule, source.form, source.article)) != 1:
            raise ValueError(f'{source.key}: exactly one of admrule, form or article is required')
    return sources


class Repo:
    """Read-only view of a bare clone, pinned to the HEAD commit seen when opened."""

    def __init__(self, git_dir: Path):
        self.git_dir = git_dir
        self.commit = self._git('rev-parse', 'HEAD').strip()

    def _git(self, *args: str, stdin: str | None = None) -> str:
        try:
            result = subprocess.run(
                ['git', '--git-dir', str(self.git_dir), *args],
                input=stdin,
                env=GIT_ENV,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SourceError(f'git {args[0]} failed in {self.git_dir.name}: {exc.stderr.strip()}') from exc
        return result.stdout

    def paths(self) -> list[str]:
        return self._git('ls-tree', '-r', '-z', '--name-only', self.commit).split('\0')[:-1]

    def read(self, path: str) -> str:
        return self._git('cat-file', 'blob', f'{self.commit}:{path}')


def split_document(text: str) -> tuple[dict, str]:
    if not text.startswith('---\n'):
        raise SourceError('missing front matter')
    head, _, body = text[4:].partition('\n---\n')
    meta = yaml.safe_load(head) or {}
    return {key: iso(value) for key, value in meta.items()}, body


def iso(value):
    return value.isoformat() if isinstance(value, datetime.date) else value


def clean(text: str) -> str:
    """Drop markdown emphasis/escapes and indentation so the text reads like the official text."""
    text = re.sub(r'\\(.)', r'\1', text.replace('**', ''))
    lines = [line.strip() for line in text.strip().splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))


def attachments(meta: dict) -> list[dict]:
    return [{'title': entry['제목'], 'pdfUrl': entry['PDF링크']} for entry in meta.get('첨부파일') or []]


def find_admrules(source: Source, repo: Repo) -> list[Found]:
    rule = source.admrule or {}
    pattern = re.compile(rule['name'])
    found = []
    for path in repo.paths():
        parts = path.split('/')
        if parts[0] != rule['agency'] or parts[-1] != '본문.md' or not pattern.search(parts[-2]):
            continue
        meta, body = split_document(repo.read(path))
        if meta.get('소관부처명') != rule['agency'] or not pattern.search(str(meta.get('행정규칙명'))):
            continue
        title = str(meta['행정규칙명'])
        text = clean(re.sub(rf'^#\s+{re.escape(title)}\s*\n', '', body.strip()))
        data = record(
            source,
            kind='admrule',
            title=title,
            agency=meta['소관부처명'],
            issuedNo=meta.get('발령번호'),
            issuedOn=meta.get('발령일자'),
            effectiveFrom=meta.get('시행일자'),
            excerpt=text[:EXCERPT_LIMIT],
            sourceUrl=meta.get('출처'),
            attachments=attachments(meta),
        )
        found.append(Found(source, f'admrule:{meta["행정규칙ID"]}', ADMRULE_REPO, repo.commit, data, text))
    if not found:
        raise SourceError(f'no administrative rule of {rule["agency"]} matches {rule["name"]}')
    return found


def find_form(source: Source, repo: Repo) -> list[Found]:
    rule = source.form or {}
    meta, _ = split_document(repo.read(rule['file']))
    for entry in meta.get('첨부파일') or []:
        if (entry['별표구분'], entry['별표번호'], entry['별표가지번호']) == (
            rule['kind'],
            rule['number'],
            rule['branch'],
        ):
            label = f'{rule["kind"]} {int(rule["number"])}' + (
                f'의{int(rule["branch"])}' if int(rule['branch']) else ''
            )
            data = record(
                source,
                kind='form',
                title=entry['제목'],
                lawName=meta['제목'],
                article=label,
                asOf=meta.get('시행일자'),
                sourceUrl=meta.get('출처'),
                attachments=[{'title': entry['제목'], 'pdfUrl': entry['PDF링크']}],
            )
            ref = f'form:{rule["file"]}#{rule["kind"]}{rule["number"]}-{rule["branch"]}'
            return [Found(source, ref, LAW_REPO, repo.commit, data)]
    raise SourceError(f'{rule["kind"]} {rule["number"]}-{rule["branch"]} is not listed in {rule["file"]}')


def find_article(source: Source, repo: Repo) -> list[Found]:
    rule = source.article or {}
    meta, body = split_document(repo.read(rule['file']))
    heading = re.search(rf'^#####\s+{re.escape(rule["article"])}\s*\((.+?)\)\s*$', body, re.M)
    if heading is None:
        raise SourceError(f'{rule["article"]} not found in {rule["file"]}')
    following = re.search(r'^#{1,5}\s', body[heading.end() :], re.M)
    text = clean(body[heading.end() : heading.end() + following.start() if following else None])
    data = record(
        source,
        kind='article',
        title=f'{meta["제목"]} {rule["article"]}({heading.group(1)})',
        lawName=meta['제목'],
        article=rule['article'],
        asOf=meta.get('시행일자'),
        excerpt=text[:EXCERPT_LIMIT],
        sourceUrl=meta.get('출처'),
    )
    return [Found(source, f'article:{rule["file"]}#{rule["article"]}', LAW_REPO, repo.commit, data, text)]


def record(source: Source, **values) -> dict:
    data = {
        'sourceKey': source.key,
        'sourceTitle': source.title,
        'category': source.category,
        'kind': None,
        'title': None,
        'agency': None,
        'lawName': None,
        'article': None,
        'issuedNo': None,
        'issuedOn': None,
        'effectiveFrom': None,
        'asOf': None,
        'excerpt': None,
        'sourceUrl': None,
        'attachments': [],
    }
    data.update(values)
    return data


def collect(sources: list[Source], repos: Path) -> tuple[list[Found], list[str]]:
    """Return every selected document and one error line per source that could not be read."""
    opened: dict[str, Repo] = {}
    found: list[Found] = []
    errors: list[str] = []
    for source in sources:
        name = ADMRULE_REPO if source.admrule else LAW_REPO
        finder = find_admrules if source.admrule else find_form if source.form else find_article
        try:
            if name not in opened:
                opened[name] = Repo(repos / name)
            found.extend(finder(source, opened[name]))
        except (SourceError, KeyError, yaml.YAMLError) as exc:
            errors.append(f'{source.key}: {exc}')
    return found, errors
