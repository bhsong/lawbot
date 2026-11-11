"""Legalize KR 원문과 기준일로 조문 CSV 두 개를 만든다 (계획: docs/archives/article-generator.md, docs/archives/v5-tax-scope.md)

    git clone --bare --filter=blob:none https://github.com/legalize-kr/legalize-kr ../legalize-kr
    uv run lawbot-build-articles --repo ../legalize-kr --asof 2026-10-20 --out-dir data/v4 [--check] [--scope 목록.csv]

출력 (UTF-8 BOM, 행 끝 LF)
- articles_<버전>.csv: 공통 범위의 전체 법률과 --scope로 선택한 조문. 버전은 --version (기본 v4)
- law_articles_<버전>_decree.csv: 법률 + 시행령·시행규칙 (행 순서는 docs())
- --check: 파일을 쓰지 않고 out-dir의 기존 파일과 바이트 단위로 비교 (다르면 종료 코드 1)
- --scope: CSV(law·kind·article_no)에 있는 법령은 목록 조문과 부칙 특례만 전체 법령 문서 뒤에 배치
- --scope 생략: 고정 v4 매니페스트에 있는 법령의 전체 문서만 생성

규칙 (모든 문서에 같다)
- 파일: kr/<법령명 띄어쓰기 없이>/ 안에서 기준일 버전의 머리말 `제목`이 문서명이고 폐지 커밋이 아닌 파일
- 조합: 파일 이력(커밋 날짜 = 공포일)에서 공포일 ≤ 기준일인 버전을 오래된 것부터 보며, 직전 버전과 달라진 조문
  (신설·삭제 포함)과 장 제목에 그 개정의 시행일을 붙이고 시행일 ≤ 기준일인 변경만 반영한다. 시행일은 머리말
  `시행일자`, 부칙 제1조 '다만'에 적힌 조문·장은 그 날짜 (조문 일부만 적혀 있어도 조문 전체, 규모별이면 가장 이른 날)
- 시작: 기준일에 시행 중인 마지막 전부개정(제정) 버전부터. 그 앞 버전은 본문이 이어지지 않는다
- 타법개정 부칙('부칙(정부조직법) <…>')의 조문은 그 법의 조문이라 머리말 시행일자만 쓴다
- 시행 전 개정이 남은 조문을 그 뒤 개정이 다시 바꾸면 조문 단위로 나눌 수 없다: 최신 본문 끝에
  `[주의: YYYY.M.D. 시행 예정인 개정 내용이 함께 들어 있음]`
- 부칙 특례: 부칙 조문이 '제N조…에도 불구하고'로 본칙(범위 목록 문서는 범위 안)을 대신하고, 기한('…까지')이
  기준일 이후이며, 그 부칙이 시행 중이면 `부칙<제N호>제M조` 행으로 넣는다 (chapter = 부칙 머리)
- 원문 형식 변경(공백, 조문 끝 `[시행일]` 안내 삭제)은 개정으로 보지 않는다
- effective_date: 그 조문을 마지막으로 바꾼 개정의 시행일
- 유효기간: 만료된 부칙 개정규정은 조문 번호와 제목이 같으면 deleted=1로 둔다. 항은 개정 본문과 같으면
  `[실효: YYYY.M.D.까지 유효]`를 붙인다. 처리 범위 밖 문장은 보고한다.
- 본문: 굵게 표시·`\\.` 이스케이프·들여쓰기·빈 줄 제거. 별표·서식은 링크만 있어 넣지 않음
- 삭제: 제목이 없고 본문이 비었거나 '삭제'로 시작하면 deleted=1
- chapter: 장(과 절) 제목, 개정 표시 제외
- 연결: 본문의 조문 참조. 같은 문서 → related_ids(자기 자신 제외), 상위 문서('법 제N조', 시행규칙은 '영 제N조' 포함과
  그 뒤 접속어로만 이어지는 '제N조' 나열) → parent_ids, child_ids는 그 역방향. 없는 조문·삭제 조문으로는 잇지 않는다.
  '부칙 제N조'와 다른 법령 조문은 넣지 않는다
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cached_property
from itertools import batched
from pathlib import Path

import pandas as pd

from lawbot import manifest
from lawbot.paths import DATA, V4_ARTICLES
from lawbot_common import law_scope

TAX_SCOPE = DATA / 'v5' / 'scope_tax.csv'
KINDS = ['법률', '시행령', '시행규칙']
LAW_COLS = ['law', 'article_id', 'article_no', 'title', 'chapter', 'deleted', 'text', 'effective_date', 'related_ids']
DECREE_COLS = [*LAW_COLS, 'kind', 'parent_law', 'parent_ids', 'child_ids']


def csv_names(version):
    """(법률 CSV, 법률+하위법령 CSV) 파일 이름"""
    return f'articles_{version}.csv', f'law_articles_{version}_decree.csv'


LAW_CSV, DECREE_CSV = csv_names('v4')  # 자동 갱신(data/live)과 v4가 쓰는 이름

ART = r'제(\d+)조(?:의(\d+))?'
HEAD = re.compile(r'^##### 제(\d+)조(?:의(\d+))?(?:\s*\((.*)\))?\s*$')
SECTION = re.compile(r'^(#{2,4}) (제\d+(?:장|절)(?:의\d+)?.*?)\s*(?:<(?:개정|신설)[^<>]*>)?\s*$')


# ── git ──────────────────────────────────────────────────────────────


BLOB_BATCH_SIZE = 128


class Repo:
    """legalize-kr 저장소 읽기. 파일 이력은 git log 한 번으로, 본문은 cat-file --batch로 (파일·버전마다 부르면 느림)"""

    def __init__(self, path):
        self.path = Path(path)
        self.history: dict[str, list[tuple[str, str, str]]] = {}  # 경로 → [(sha, 공포일, 제목)] 최신순
        self._blobs: dict[tuple[str, str], str] = {}
        self._oids: dict[tuple[str, str], str] = {}

    def _command(self, *args):
        return ['git', '-C', str(self.path), '-c', 'core.quotepath=off', *args]

    def git(self, *args):
        return subprocess.run(
            self._command(*args),
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def head(self):
        return self.git('rev-parse', 'HEAD').strip()

    def load_history(self, paths):
        out = self.git('log', '--format=@%H %ad %s', '--date=short', '--name-only', 'HEAD', '--', *paths)
        cur = None
        for line in out.splitlines():
            if line.startswith('@'):
                sha, date, subject = line[1:].split(' ', 2)
                cur = (sha, date, subject)
            elif line.strip() and cur:
                self.history.setdefault(line.strip(), []).append(cur)

    def show(self, sha, path):
        return self.show_many([(sha, path)])[0]

    def _config(self, *args):
        try:
            return self.git('config', *args)
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 1:
                raise
            return ''

    @cached_property
    def _promisor_remote(self):
        config = self._config('--type=bool', '--get-regexp', r'^remote\..*\.promisor$')
        entries = dict(line.split(maxsplit=1) for line in config.splitlines())
        remotes = [
            key.removeprefix('remote.').removesuffix('.promisor') for key, value in entries.items() if value == 'true'
        ]
        remote = 'origin' if 'origin' in remotes else next(iter(remotes), None)
        if remote is None:
            remote = self._config('--get', 'extensions.partialClone').strip() or None
        if remote is not None:
            version = re.search(r'git version (\d+)\.(\d+)', self.git('--version'))
            if version is None or tuple(map(int, version.groups())) < (2, 44):
                raise RuntimeError('부분 클론의 blob 사전 수신에는 Git 2.44 이상이 필요합니다.')
        return remote

    def _cat_file(self, mode, inputs):
        return subprocess.run(
            self._command('cat-file', mode),
            input=''.join(f'{item}\n' for item in inputs).encode(),
            env=os.environ | {'GIT_NO_LAZY_FETCH': '1'},
            check=True,
            capture_output=True,
        ).stdout

    def prefetch(self, items):
        """Fetch only locally missing blobs from the promisor in a single request."""
        remote = self._promisor_remote
        if remote is None:
            return
        refs = [item for item in dict.fromkeys(items) if item not in self._oids and item not in self._blobs]
        if not refs:
            return
        env = os.environ | {'GIT_NO_LAZY_FETCH': '1'}
        oids = []
        for batch in batched(refs, BLOB_BATCH_SIZE, strict=False):
            specs = [f'{sha}:{path}' for sha, path in batch]
            try:
                result = subprocess.run(
                    self._command('rev-parse', '--revs-only', '--end-of-options', *specs, '--'),
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                resolved = len(exc.stdout.splitlines())
                if exc.returncode == 128 and resolved < len(batch):
                    sha, path = batch[resolved]
                    raise FileNotFoundError(f'{sha[:10]}:{path}') from exc
                raise
            oids.extend(result.stdout.splitlines())
        unique = list(dict.fromkeys(oids))
        types = self._cat_file('--batch-check=%(objecttype)', unique).splitlines()
        missing = [oid for oid, kind in zip(unique, types, strict=True) if kind.endswith(b' missing')]
        if missing:
            try:
                subprocess.run(
                    self._command(
                        '-c',
                        'fetch.negotiationAlgorithm=noop',
                        'fetch',
                        remote,
                        '--no-tags',
                        '--no-write-fetch-head',
                        '--recurse-submodules=no',
                        '--filter=blob:none',
                        '--stdin',
                    ),
                    input='\n'.join(missing) + '\n',
                    env=env | {'GIT_TERMINAL_PROMPT': '0'},
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f'blob 사전 수신 실패 ({remote}, {len(missing)}개): {exc.stderr.strip()}') from exc
        self._oids.update(zip(refs, oids, strict=True))

    def show_many(self, items):
        items = list(items)
        missing = [i for i in dict.fromkeys(items) if i not in self._blobs]
        if not missing:
            return [self._blobs[i] for i in items]
        self.prefetch(missing)
        for batch in batched(missing, BLOB_BATCH_SIZE, strict=False):
            out = self._cat_file('--batch', (self._oids.get((sha, path), f'{sha}:{path}') for sha, path in batch))
            pos = 0
            for key in batch:
                header_end = out.index(b'\n', pos)
                header = out[pos:header_end].split()
                if header[-1] == b'missing':
                    raise FileNotFoundError(f'{key[0][:10]}:{key[1]}')
                size = int(header[2])
                self._blobs[key] = out[header_end + 1 : header_end + 1 + size].decode('utf-8')
                pos = header_end + 1 + size + 1
        return [self._blobs[i] for i in items]


# ── 원문 파싱 ────────────────────────────────────────────────────────


def front(md):
    m = re.match(r'---\n(.*?)\n---\n', md, re.S)
    if not m:
        raise ValueError('머리말(YAML)이 없습니다')
    out = {}
    for line in m.group(1).splitlines():
        if line[:1] in (' ', '-'):  # 첨부파일 등 하위 항목
            continue
        k, _, v = line.partition(':')
        if v.strip():
            out[k.strip()] = v.strip().strip("'")
    return out


def clean(lines):
    out = []
    for s in lines:
        s = s.replace('**', '').replace('\\.', '.').strip()
        if s:
            out.append(s)
    return '\n'.join(out)


def split_addenda(md):
    body = md.split('\n---\n', 1)[1]
    parts = re.split(r'^## 부칙', body, maxsplit=1, flags=re.M)
    return parts[0], (parts[1] if len(parts) > 1 else '')


def parse(md):
    """본문 조문 목록 [{no, title, text, chapter}]. chapter: 장(과 절) 제목, 개정 표시 제외"""
    body, _ = split_addenda(md)
    arts, cur, chapter, section = [], None, None, None
    for line in body.splitlines():
        m = HEAD.match(line)
        if m:
            cur = {
                'no': f'제{m[1]}조' + (f'의{m[2]}' if m[2] else ''),
                'title': (m[3] or '').strip().strip('()').strip() or None,
                'lines': [],
                'chapter': ' '.join(x for x in (chapter, section) if x) or None,
            }
            arts.append(cur)
            continue
        s = SECTION.match(line)
        if s:
            if '장' in s[2].split()[0]:
                chapter, section = s[2], None
            else:
                section = s[2]
            cur = None
        elif cur is not None and not line.startswith('#'):
            cur['lines'].append(line)
    for a in arts:
        a['text'] = clean(a.pop('lines')) or None
    return arts


# ── 부칙 시행일 ──────────────────────────────────────────────────────


def months_later(start: dt.date, months: int) -> dt.date:
    """start(기산일)의 months개월 뒤 같은 날. 그런 날이 없으면 그다음 달 1일 (민법 제160조: 그 달 말일에 기간 만료)"""
    y, m = divmod(start.month - 1 + months, 12)
    y, m = start.year + y, m + 1
    if start.day <= calendar.monthrange(y, m)[1]:
        return dt.date(y, m, start.day)
    return dt.date(y + (m == 12), m % 12 + 1, 1)


DATE_EXPR = re.compile(
    r'공포\s*한\s*날|공포\s*후\s*(?=\d)(?:(\d+)\s*년\s*)?(?:(\d+)\s*개월)?이\s*경과한\s*날'
    r'|(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일'
)


def date_expr(m: re.Match, promulgated: str) -> str:
    """부칙의 날짜 표현 → YYYY-MM-DD. '공포 후 N년 M개월이 경과한 날': 공포 다음 날부터 세어(초일 불산입) 그만큼 뒤
    (2026-04-07 + 8개월 → 2026-12-08, 2019-04-30 + 6개월 → 2019-11-01)"""
    p = dt.date.fromisoformat(promulgated)
    if m[1] or m[2]:
        months = int(m[1] or 0) * 12 + int(m[2] or 0)
        return months_later(p + dt.timedelta(days=1), months).isoformat()
    if m[3]:
        return dt.date(int(m[3]), int(m[4]), int(m[5])).isoformat()
    return p.isoformat()


def own_addendum(md, number, with_owner=False):
    """이 버전의 부칙 본문 (`부칙… <제{공포번호}호,…>` 머리 아래). 없으면 None (부칙 없는 타법개정 등).
    with_owner: (본문, 머리의 법령 이름). '부칙(정부조직법) <…>'처럼 타법개정이면 그 법 이름, 이 법의 개정이면 None"""
    _, addenda = split_addenda(md)
    number = str(int(number))
    heads = list(re.finditer(r'^부칙\s*(?:\(([^)]*)\))?[^\n<]*<제(\d+)호,[^>]*>', addenda, re.M))
    for i, h in enumerate(heads):
        if str(int(h[2])) == number:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(addenda)
            text = addenda[h.end() : end]
            return (text, h[1]) if with_owner else text
    return (None, None) if with_owner else None


def addendum_of(md, number, latest=None):
    """(부칙 본문, 타법개정이면 그 법 이름): 기준일 버전 파일(latest)에 있으면 그것(나중 개정 반영본), 없으면 그 버전의 것"""
    found = own_addendum(latest, number, with_owner=True) if latest else (None, None)
    return found if found[0] is not None else own_addendum(md, number, with_owner=True)


@dataclass
class Schedule:
    """한 개정의 시행일: main(머리말 시행일자) + 부칙 '다만'에 적힌 조문·장별 날짜"""

    main: str
    articles: dict[str, str] = field(default_factory=dict)  # '제13조' → 날짜
    chapters: dict[str, str] = field(default_factory=dict)  # '제11장' → 날짜

    def article(self, no):
        return self.articles.get(no, self.main)

    def chapter(self, title):
        key = (title or '').split(' ')[0]
        return self.chapters.get(key, self.main)


AFTER_ADDENDUM = re.compile(r'부칙\s*$')
# '대통령령 제32516호 소득세법 시행령 일부개정령(… 포함한다) 제216조의4': 그 개정법률(·개정령)이 정한 조문.
# 이름이 이 문서면 이 문서 조문(앞 개정이 고친 조문을 이번 개정이 다시 고침), 다른 문서면 본문 조문이 아님
EARLIER_AMENDMENT = re.compile(r'제\d+호\s+([^,()]*?)\s*(?:일부|전부)?(?:개정|폐지)(?:법률|령)\s*(?:\([^()]*\))?\s*$')
ANNEX = re.compile(r'별표|서식')
UNKNOWN = '9999-12-31'  # 부칙에 날짜가 없는 시행일 ('동법의 시행일', '대통령령으로 정하는 날')
# '…의 개정규정 중 "…"에 관한 부분', '… 중 제59조제2항의 개정규정과 관련한 부분': 대상 조문을 한정하는 말이라 대상이 아님
QUALIFIER = re.compile(r'\s*중\s+.*?(?:에\s*관한|[과와]\s*관련한|[과와]\s*관련된)\s*부분')
QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
# 시행일 조항을 나누는 곳: 단서, 연결 어미, 문장 끝, 항
BOUNDARY = re.compile(
    r'(?=다만\s*,)|(?<=시행하고,)|(?<=시행하며,)|(?<=시행하되,)|(?<=시행한다\.)|(?<=같다\.)|(?=[②-⑳])'
)


def target_list(text, order, own=None):
    """'제13조, 제11장의 제목, 제103조부터 제105조까지(내지), 제110조제1호 및 …' → ([(조문, 일부만인지)], 장 목록, 앞 개정의 조문인지).
    일부: 조문 뒤에 항·호·목이 붙은 것 ('제110조제1호', '제116조제2항제1호ㆍ제4호'). own: 이 문서 이름(머리말 제목)"""
    arts, chapters, earlier = [], [], False
    rng = rf'(\s*(?:부터|내지)\s*{ART}(?:\s*까지)?)?'
    for m in re.finditer(rf'{ART}((?:제\d+항|제\d+호|제\d+목|[ㆍ·]제?\d+(?:항|호))*){rng}', text):
        before = text[: m.start()]
        if AFTER_ADDENDUM.search(before):  # '부칙 제16조제21항': 부칙의 조문이라 본문 조문이 아님
            continue
        e = EARLIER_AMENDMENT.search(before)
        if e:
            if squash(e[1]) != squash(own or ''):
                continue
            earlier = True
        a = f'제{m[1]}조' + (f'의{m[2]}' if m[2] else '')
        if m[4]:
            b = f'제{m[5]}조' + (f'의{m[6]}' if m[6] else '')
            if a not in order or b not in order:
                raise ValueError(f'부칙 범위의 조문이 본문에 없습니다: {a}~{b}')
            arts.extend((x, False) for x in order[order.index(a) : order.index(b) + 1])
        else:
            arts.append((a, bool(m[3])))
    for m in re.finditer(r'(?<![\d조])(제\d+장(?:의\d+)?)', text):
        chapters.append(m[1])
    return arts, chapters, earlier


AMEND_NOTE = re.compile(r'\s*<(?:개정|신설)[^<>]*>')


def effective_clause(addendum):
    """부칙에서 시행일 조항만: '제1조(시행일)'이 있으면 그 조 전체(항 포함), 없으면 '①(시행일)' 항, 둘 다 없으면
    제2조·② 앞까지.
    적용례·경과조치(②(적용례) …, 제2조 …)의 '개정규정'은 시행일이 아니라 넣지 않는다.
    나중 법률이 부칙을 고친 표시('<개정 2021.12.8, …>')는 문장이 아니라 뺀다"""
    m = re.search(r'제1조\s*\(시행일[^)]*\)\s*(.*?)(?=\n\s*\n\s*제2조|\Z)', addendum, re.S)
    if not m:
        m = re.search(r'①\s*\(시행일[^)]*\)\s*(.*?)(?=[②-⑳]|\Z)', addendum, re.S)
    if not m:  # 제목 없는 한 문단짜리 부칙: 제2조·②(다른 법령의 개정 등) 앞까지
        m = re.match(r'\s*(.*?)(?=\n\s*\n\s*(?:제2조|[②-⑳])|\Z)', addendum, re.S)
    text = AMEND_NOTE.sub('', m[1] if m else '')
    return re.sub(r'\s+', ' ', text).strip()


def clauses(first):
    """시행일 조항 → [[문장, 딸린 번호 목록]]. 번호 목록(1. … 2. …)은 앞에서 '다음 각 호'라고 한 문장의 것"""
    out: list[list[str]] = []
    owner = None
    for part in (x.strip() for x in BOUNDARY.split(first)):
        if not part:
            continue
        if re.match(r'\d+\.\s', part) and owner is not None:
            owner[1] += ' ' + part
            continue
        out.append([part, ''])
        if re.search(r'다음\s*각\s*호', part):
            owner = out[-1]
    return out


def each_item(items):
    """'1. … 2. …' → 번호를 뗀 호 목록"""
    return [re.sub(r'^\d+\.\s*', '', x) for x in re.split(r'(?:^|\s)(?=\d+\.\s)', items.strip()) if x.strip()]


def place(sched: Schedule, placed: set, sentence, items, fm, order, named=False):
    """'○의 개정규정은 ○부터 시행' 한 문장(과 딸린 번호 목록): 날짜 앞에서 대상 조문·장, 뒤에서 날짜(여럿이면 가장 이른 날).
    named: '다음 각 호의 개정규정은 해당 호에서 정하는 날부터'의 호라 '개정규정'이라는 말이 없어도 읽는다"""
    if not named and '개정규정' not in sentence:
        return
    d = DATE_EXPR.search(sentence)
    cut = min(i for i in (d.start() if d else -1, sentence.find('다음'), len(sentence)) if i >= 0)
    targets = QUOTED.sub('', QUALIFIER.sub('', sentence[:cut]))
    arts, chapters, earlier = target_list(targets, order, fm.get('제목'))
    dates = [date_expr(x, fm['공포일자']) for x in DATE_EXPR.finditer(sentence[cut:] + ' ' + items)]
    if (
        earlier and not dates
    ):  # 앞 개정의 조문인데 날짜가 없음('○○법 폐지법률의 시행일'): 그 앞 개정 버전이 시행 전으로 둔다
        return
    if not arts and not chapters:
        # '다만, 다음 각 호의 개정규정은 해당 호에서 정하는 날부터': 호마다 대상과 날짜
        if items and re.search(r'다음\s*각\s*호', sentence):
            for item in each_item(items):
                place(sched, placed, item, '', fm, order, named='개정규정' in sentence)
        elif ANNEX.search(targets):  # 별표·서식만: 데이터에 없으므로 그 날짜는 읽은 것으로 친다
            placed.update(dates)
        return
    date = min(dates) if dates else UNKNOWN
    placed.update(dates)
    for a, part in arts:  # 조문 일부만이면 나머지는 본 시행일: 조문 전체가 시행되는 날
        sched.articles[a] = max(sched.articles.get(a, ''), max(date, sched.main) if part else date)
    for c in chapters:
        sched.chapters[c] = max(sched.chapters.get(c, ''), date)


def schedule(md, fm, order, asof=None, latest=None) -> Schedule:
    """이 버전(개정)의 시행일: 머리말 `시행일자` + 부칙 시행일 조항에서 '○의 개정규정은 ○부터 시행'한다는 조문·장별 날짜.
    사업장 규모별(다음 각 호)이면 가장 이른 날. 조문 일부(항·호)만 적혔으면 그 날과 본 시행일 중 늦은 날,
    한 조문에 날짜가 여럿이면 가장 늦은 날 (조문 전체가 시행되는 날).
    asof: 주면 시행일 조항에 기준일 뒤 날짜가 있는데 어느 조문의 것인지 못 읽었을 때 실패.
    latest: 기준일 버전 파일. 나중 법률이 옛 부칙의 시행일을 고치므로(소득세법 제17757호) 부칙은 여기서 읽는다"""
    sched = Schedule(main=fm['시행일자'])
    text, owner = addendum_of(md, fm['공포번호'], latest)
    if not text or owner:  # 타법개정 부칙의 조문은 그 법의 조문: 이 법에는 머리말 시행일자
        return sched
    first = effective_clause(text)
    placed: set[str] = set()  # 대상 조문을 읽은 문장의 날짜
    for sentence, items in clauses(first):
        if '개정규정' not in sentence:
            if '개정규정' not in items:
                continue
            # '다음 각 호의 부분은 각 호에서 정한 날부터': 호마다 '○의 개정규정 …: 날짜'
            for item in each_item(items):
                place(sched, placed, item, '', fm, order)
            continue
        place(sched, placed, sentence, items, fm, order)
    if asof:
        known = {sched.main, *placed}
        late = {date_expr(d, fm['공포일자']) for d in DATE_EXPR.finditer(first)} - known
        if any(d > asof for d in late):
            raise ValueError(
                f'제{fm["공포번호"]}호 부칙 시행일 {sorted(late)}이 어느 조문의 것인지 읽지 못했습니다: {first[:200]}'
            )
    return sched


NOTE_ITEM = re.compile(r'^(?:\d+\.|[가-하]\.)\s')


def strip_notes(text):
    """조문 끝 `[시행일] …` 안내 문단(규모별 시행일 등)과 그 아래 번호 줄을 뺀다. 원문이 나중에 지우기도 하는 안내라 개정이 아님"""
    out, skipping = [], False
    for line in (text or '').split('\n'):
        if line.startswith('[시행일]'):
            skipping = True
            continue
        if skipping and NOTE_ITEM.match(line):
            continue
        skipping = False
        out.append(line)
    return '\n'.join(out)


def squash(s):
    """공백과 `[시행일]` 안내를 뺀 본문: 이것이 같으면 개정이 아니라 원문 형식 변경으로 본다"""
    return re.sub(r'\s+', '', strip_notes(s))


# 타법개정 부칙: '…시행일이 도래하지 아니한 법률을 개정한 부분은 각각 해당 법률의 시행일부터 시행한다' (공백 뺀 본문에서 찾음)
FOLLOWS_PENDING = re.compile(
    r'시행일이도래하지(?:아니한|않은)(?:법률|법령|대통령령|부령)[^.]*?개정한부분은각각해당(?:법률|법령|대통령령|부령)의시행일부터'
)
FULL_REVISION = re.compile(r'\((?:전부개정|제정|폐지제정)\)$')


def _full_revisions(history):
    return (i for i in reversed(range(len(history))) if FULL_REVISION.search(history[i][2]))


def _effective_histories(repo: Repo, paths, asof):
    """Check one full-revision candidate per document at a time, newest first."""
    histories = {path: [h for h in reversed(repo.history[path]) if h[1] <= asof] for path in dict.fromkeys(paths)}
    waiting = {path: _full_revisions(hist) for path, hist in histories.items()}
    while waiting:
        candidates = [(path, i) for path, revisions in waiting.items() if (i := next(revisions, None)) is not None]
        items = [(histories[path][i][0], path) for path, i in candidates]
        next_round = {}
        for (path, i), md in zip(candidates, repo.show_many(items), strict=True):
            if front(md)['시행일자'] <= asof:
                histories[path] = histories[path][i:]
            else:
                next_round[path] = waiting[path]
        waiting = next_round
    return histories


def in_force(repo: Repo, path, asof):
    """기준일에 시행 중인 조문 [{no, title, text, chapter, eff}]과 버전별 반영 기록

    eff: 그 조문(제목·본문)을 마지막으로 바꾼 개정의 시행일. 공백만 바뀐 변경(원문 형식 정리)은 개정으로 보지 않는다.
    시행 전 개정이 남은 조문에 그 뒤 시행된 개정이 내용을 바꾸면 조문 단위로 나눌 수 없다: 최신 본문을 쓰고 끝에
    시행 전 내용이 섞였다고 적는다(mixed, 2026-11-03 사용자 결정). 다만 지금 삭제된 조문을 시행 전 개정이 다시 만들고 있고
    그것을 '시행일이 도래하지 아니한 법률을 개정한 부분은 해당 법률의 시행일부터'인 타법개정이 고치면 함께 시행 전으로 둔다"""
    hist = _effective_histories(repo, [path], asof)[path]
    return _compose_history(path, asof, hist, repo.show_many([(sha, path) for sha, _, _ in hist]))


def _compose_history(path, asof, hist, mds):
    state: dict[str, dict] = {}
    prev: dict[str, dict] = {}
    pending: dict[str, list[str]] = {}  # 시행 전 개정이 남은 조문 → 그 시행일들
    expiries: list[tuple[str, list[int], str, dict]] = []
    used = []
    order: list[str] = []
    for (sha, _, _), md in zip(hist, mds):
        fm = front(md)
        arts = {a['no']: a for a in parse(md)}
        cur_order = list(arts)
        sched = schedule(md, fm, cur_order, asof, latest=mds[-1])
        addendum, owner = addendum_of(md, fm['공포번호'], mds[-1])
        expiry_rules, expiry_unhandled = expiry_clauses(addendum or '')
        expiries.extend(
            (no, paras, until, arts[no])
            for no, paras, until in expiry_rules
            if no in arts and sched.article(no) <= asof
        )
        follows = bool(owner and FOLLOWS_PENDING.search(re.sub(r'\s+', '', addendum or '')))
        applied = 0
        waiting: list[tuple[str, str]] = []  # 시행 전이라 반영하지 않은 (조문, 시행일)
        mixed: list[tuple[str, list[str]]] = []  # 시행 전 개정이 남은 채로 다시 바뀐 (조문, 시행 전 날짜)
        for no in dict.fromkeys(list(prev) + cur_order):
            old, new = prev.get(no), arts.get(no)
            if old == new:
                continue
            if new is None:  # 조문이 파일에서 빠짐
                if sched.article(no) > asof:
                    raise ValueError(f'{path} {no}: 시행 전에 조문이 빠지는 경우는 처리하지 않습니다')
                state.pop(no, None)
                continue
            cur = state.setdefault(no, {'no': no, 'title': None, 'text': None, 'chapter': None, 'eff': None})
            if old is None or (old['title'], old['text']) != (new['title'], new['text']):
                real = old is None or (old['title'], squash(old['text'])) != (new['title'], squash(new['text']))
                date = sched.article(no)
                if not real:  # 형식만 바뀜: 시행 전 개정이 없을 때만 따라감
                    if no not in pending:
                        cur.update(title=new['title'], text=new['text'])
                elif date == UNKNOWN:  # 부칙에 날짜가 없음: 뒤 개정이 다시 바꾸지 않으면 끝에서 실패
                    cur.update(title=new['title'], text=new['text'], eff=sched.main, unknown=fm.get('공포번호'))
                    applied += 1
                elif date <= asof:
                    cur.pop('unknown', None)
                    deleted_now = cur['title'] is None and (cur['text'] or '').startswith('삭제')
                    if (
                        no in pending and follows and deleted_now
                    ):  # 시행 전 개정이 다시 만드는 조문을 고친 타법개정: 함께 시행
                        pending[no].append(max(pending[no]))
                        waiting.append((no, max(pending[no])))
                        continue
                    if no in pending:
                        cur['mixed'] = sorted(set(pending[no]))
                        mixed.append((no, cur['mixed']))
                    cur.update(title=new['title'], text=new['text'], eff=date)
                    applied += 1
                else:
                    pending.setdefault(no, []).append(date)
                    waiting.append((no, date))
            if old is None or old['chapter'] != new['chapter']:
                if sched.chapter(new['chapter']) <= asof:
                    cur['chapter'] = new['chapter']
            if cur['eff'] is None:  # 시행 전 신설 조문
                state.pop(no)
        prev = arts
        order = cur_order
        used.append(
            {
                'commit': sha[:10],
                '공포번호': fm.get('공포번호'),
                '시행일자': fm.get('시행일자'),
                '반영': applied,
                '시행 전': len(waiting),
                'pending': waiting,
                'mixed': mixed,
                'expiry_unhandled': expiry_unhandled,
            }
        )
    unknown = {no: a['unknown'] for no, a in state.items() if a.get('unknown')}
    if unknown:
        raise ValueError(f'{path}: 시행일을 부칙에서 날짜로 읽을 수 없는 개정이 지금 본문입니다 {unknown}')
    known = [no for no in order if no in state] + [no for no in state if no not in order]
    arts = [state[no] for no in known]
    expire(arts, expiries, asof, path)
    for a in arts:
        if a.get('mixed'):
            dates = ', '.join(dotted(d) for d in a['mixed'])
            a['text'] = f'{a["text"]}\n[주의: {dates} 시행 예정인 개정 내용이 함께 들어 있음]'
    return arts, used


ADDENDUM_HEAD = re.compile(r'^부칙\s*(?:\(([^)]*)\))?[^\n<]*<제(\d+)호,[^>]*>', re.M)
ADDENDUM_ART = re.compile(r'^제(\d+)조(?:의(\d+))?\s*\(([^)]*)\)', re.M)
OVERRIDE = re.compile(
    rf'((?:{ART}(?:제\d+항|제\d+호|제\d+목|[ㆍ·]제?\d+(?:항|호))*\s*(?:,|및|ㆍ|또는)?\s*)+)(?:의\s*개정규정)?\s*에도\s*불구하고'
)
# '「다른 법」 제3조', '법 제3조', '부칙 제2조': 이 문서 본칙이 아님
OTHER_DOC = re.compile(r'(?:」|법|영|규칙|부칙)\s*$')
UNTIL = re.compile(r'(\d{4})년(?:\s*(\d{1,2})월\s*(\d{1,2})일)?(?:분)?까지')


def addendum_overrides(md, used, asof, main_nos):
    """부칙 특례 조문 [{no, title, text, chapter, eff, targets}]: 부칙 조문이 '제N조…(의 개정규정)에도 불구하고'로 이 문서 본칙
    (main_nos 안)을 대신하고, 본문의 기한('…년까지', '…년 …월 …일까지') 중 가장 늦은 날이 기준일 이후이며, 그 부칙의
    개정이 기준일에 시행 중인 것. used: in_force의 버전 기록(조합한 버전의 부칙만 본다). 타법개정 부칙('부칙(정부조직법)')은
    그 법의 조문이라 넣지 않는다"""
    _, addenda = split_addenda(md)
    in_force_numbers = {str(int(u['공포번호'])) for u in used if u['시행일자'] <= asof}
    heads = list(ADDENDUM_HEAD.finditer(addenda))
    out = []
    for i, h in enumerate(heads):
        number = str(int(h[2]))
        if h[1] or number not in in_force_numbers:
            continue
        block = addenda[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(addenda)]
        arts = list(ADDENDUM_ART.finditer(block))
        for j, a in enumerate(arts):
            raw = block[a.end() : arts[j + 1].start() if j + 1 < len(arts) else len(block)]
            text = clean(raw.split('\n'))
            targets = [
                f'제{t[0]}조' + (f'의{t[1]}' if t[1] else '')
                for m in OVERRIDE.finditer(text)
                if not OTHER_DOC.search(text[: m.start()])
                for t in re.findall(ART, m[1])
            ]
            targets = [t for t in dict.fromkeys(targets) if t in main_nos]
            ends = [dt.date(int(y), int(mo or 12), int(d or 31)).isoformat() for y, mo, d in UNTIL.findall(text)]
            if not targets or not ends or max(ends) < asof:
                continue
            no = f'제{a[1]}조' + (f'의{a[2]}' if a[2] else '')
            eff = next(u['시행일자'] for u in used if str(int(u['공포번호'])) == number)
            out.append(
                {
                    'no': f'부칙<제{number}호>{no}',
                    'title': a[3].strip(),
                    'text': text,
                    'chapter': h[0].strip(),
                    'eff': eff,
                    'targets': targets,
                }
            )
    return out


def dotted(date):
    """'2027-01-01' → '2027.1.1.' (원문 날짜 표기)"""
    y, m, d = (int(x) for x in date.split('-'))
    return f'{y}.{m}.{d}.'


EXPIRY_PREDICATE = r'효력을\s*(?:가진다|갖는다)'
AMENDED_PROVISION = r'(.+?)의\s*개정\s*규정은\s*'
EXPIRY_END = rf'까지\s*(?:그\s*)?{EXPIRY_PREDICATE}\.?'
EXPIRY = re.compile(rf'{AMENDED_PROVISION}(\d{{4}})년\s*(\d{{1,2}})월\s*(\d{{1,2}})일\s*{EXPIRY_END}')
EXPIRY_NOTICE = re.compile(rf'{AMENDED_PROVISION}.+?{EXPIRY_END}')
EXPIRY_TARGET = re.compile(r'(제\d+조(?:의\d+)?)(.*?)(?=제\d+조|$)')
EXPIRY_SUFFIX = re.compile(r'(?:\s|[,ㆍ·]|및|또는|와|과|같은\s*조|제\d+항)*')
EXPIRY_COMPLEX_TARGET = re.compile(r'「|부칙\s*제\d+조|(?:부터|내지)\s*제\d+조')
PARA = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'


def expiry_clauses(addendum):
    """Read fixed dates for article and paragraph targets, and retain unhandled statements for review."""
    rules, unhandled = [], []
    headings = list(ADDENDUM_ART.finditer(addendum))
    for i, heading in enumerate(headings):
        apply_rules = '유효기간' in heading[3]
        end = headings[i + 1].start() if i + 1 < len(headings) else len(addendum)
        body = addendum[heading.end() : end]
        if '효력을' not in body:
            continue
        body = AMEND_NOTE.sub('', body).strip()
        no = f'제{heading[1]}조' + (f'의{heading[2]}' if heading[2] else '')
        for paragraph in re.split(rf'(?m)(?=^[ \t]*[{PARA}])', body):
            for sentence in re.split(r'(?<=\.)\s+', ' '.join(paragraph.split())):
                if not re.search(EXPIRY_PREDICATE, sentence):
                    continue
                statement = sentence.lstrip(PARA + ' ')
                if not apply_rules:
                    if EXPIRY_NOTICE.fullmatch(statement):
                        unhandled.append(f'{no} {sentence}')
                    continue
                parsed = EXPIRY.fullmatch(statement)
                targets = expiry_targets(parsed[1]) if parsed else []
                if not targets or any(paras is None for _, paras in targets):
                    unhandled.append(f'{no} {sentence}')
                if parsed and targets:
                    date = dt.date(int(parsed[2]), int(parsed[3]), int(parsed[4])).isoformat()
                    rules.extend((no, paras, date) for no, paras in targets if paras is not None)
    return rules, unhandled


def expiry_targets(text):
    """Match article and paragraph targets without treating a qualified reference as a whole article."""
    targets = re.sub(r'\([^()]*\)', '()', text.strip())
    if not EXPIRY_TARGET.match(targets) or EXPIRY_COMPLEX_TARGET.search(targets):
        return []
    return [
        (no, [int(n) for n in re.findall(r'제(\d+)항', suffix)] if EXPIRY_SUFFIX.fullmatch(suffix) else None)
        for no, suffix in EXPIRY_TARGET.findall(targets)
    ]


def expiry_notices(versions):
    """Format unhandled expiry statements with their source document and amendment number."""
    return [
        f'{v["doc"]} 제{int(v["공포번호"])}호 {statement}'
        for v in versions
        for statement in v.get('expiry_unhandled', ())
    ]


def paragraph_span(lines, number):
    if not 1 <= number <= len(PARA):
        return None
    starts = [i for i, line in enumerate(lines) if line.startswith(PARA[number - 1])]
    if len(starts) != 1:
        return None
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and lines[i][0] in PARA), len(lines))
    return start, end


def expire(arts, expiries, asof, path):
    """Apply elapsed expiry clauses by matching article titles or the text of individual paragraphs."""
    by_no = {a['no']: a for a in arts}
    current_text = {a['no']: a['text'] for a in arts}
    for no, paras, date, amended in expiries:
        if date >= asof or no not in by_no:
            continue
        a = by_no[no]
        if not paras:
            if a.get('title') == amended.get('title'):
                a['expired'] = True
            continue
        lines = (current_text[no] or '').splitlines()
        original = (amended['text'] or '').splitlines()
        marked = (a['text'] or '').splitlines()
        for k in paras:
            declared = paragraph_span(original, k)
            if declared is None:
                raise ValueError(f'{path} {no}: 유효기간이 끝난 제{k}항을 개정 본문에서 찾지 못했습니다')
            if (span := paragraph_span(lines, k)) is None:
                continue
            start, end = span
            if squash('\n'.join(lines[start:end])) == squash('\n'.join(original[slice(*declared)])):
                marked[end - 1] += f' [실효: {dotted(date)}까지 유효]'
        a['text'] = '\n'.join(marked) or None


def commits_since(repo: Repo, ref, paths):
    """ref(매니페스트 source.ref) 뒤에 문서 파일을 바꾼 커밋 [{doc, sha, date(공포일), subject, 공포번호, 시행일자}] 오래된 것부터.
    ref가 없거나 클론에 없으면 None (커밋 날짜가 공포일이라 날짜로는 '그 뒤에 들어온 것'을 알 수 없다)"""
    if not ref:
        return None
    try:
        repo.git('cat-file', '-e', f'{ref}^{{commit}}')
    except subprocess.CalledProcessError:
        return None
    docs = {path: doc for doc, path in paths.items()}
    out = repo.git(
        'log', '--reverse', '--format=@%H %ad %s', '--date=short', '--name-only', f'{ref}..HEAD', '--', *docs
    )
    changes, cur = [], None
    for line in out.splitlines():
        if line.startswith('@'):
            sha, date, subject = line[1:].split(' ', 2)
            cur = (sha, date, subject)
        elif line.strip() in docs and cur:
            changes.append((line.strip(), *cur))
    found = []
    mds = repo.show_many([(sha, path) for path, sha, _, _ in changes])
    for (path, sha, date, subject), md in zip(changes, mds, strict=True):
        fm = front(md)
        found.append(
            {
                'doc': docs[path],
                'sha': sha,
                'date': date,
                'subject': subject,
                '공포번호': str(int(fm.get('공포번호', '0'))),
                '시행일자': fm.get('시행일자'),
            }
        )
    return found


# ── 조문 참조 (사용자 스크립트 그대로) ─────────────────────────────

# 참조 뒤에 이어지는 접속 표현(이 안에서는 앞 참조의 법령을 이어받음)
GLUE = re.compile(
    r'^(?:\([^()]*\)|\s|[,ㆍ·]|및|또는|이나|부터|까지|의|규정|제\d+항|제\d+호|제\d+목|'
    r'같은\s*조|같은\s*항|단서|본문|전단|후단|각\s*호|외의|부분|[가-하]목|\(|\))*$'
)
ANCHOR = re.compile(
    r'(「([^」]+)」|같은\s*법(?:\s*(시행령|시행규칙))?|(?<![가-힣])이\s*(?:법|영|규칙)|(?<![가-힣])영|(?<![가-힣])법)'
    r'\s*(?:\(이하[^()]*\))?\s*$'
)
# 앵커가 없어도 바로 앞 낱말이 다른 법령 이름(약칭)이면 외부 법령으로 본다. 예: '고용산재보험료징수법 시행령 제10조'
OTHER_NAME = re.compile(r'(?<![가-힣])[가-힣]+(?:법|령|규칙)(?:\s+(?:시행령|시행규칙))?\s*$')
SUB = re.compile(r'(?:제\d+항|제\d+호|제\d+목)+')


def refs(text, own, upper, names):
    """본문의 조문 참조를 (문서명, 조문번호, 범위시작) 목록으로.

    own=이 문서, upper={'법':..,'영':..}. '제A조부터 제B조까지'이면 B의 범위시작=A.
    """
    out, prev, prev_end, last_named = [], None, None, None
    for m in re.finditer(ART, text or ''):
        no = f'제{m[1]}조' + (f'의{m[2]}' if m[2] else '')
        before = text[max(0, m.start() - 80) : m.start()]
        a = ANCHOR.search(before)
        doc = None
        if a:
            tok = a[1]
            if a[2]:
                key = re.sub(r'\s+', '', a[2])
                doc = names.get(key, '__external__')
                last_named = doc
            elif tok.startswith('같은'):
                base = last_named or '__external__'
                doc = (
                    base
                    if not a[3] or base == '__external__'
                    else names.get(re.sub(r'\s+', '', f'{base}{a[3]}'), '__external__')
                )
            elif re.fullmatch(r'이\s*(법|영|규칙)', tok):
                doc = own
            elif tok in upper:
                doc = upper[tok]
            else:
                doc = '__external__'  # 법률 본문의 '법 제N조' 등 예상 밖 앵커
        gap = text[prev_end : m.start()] if prev_end is not None else None
        if doc is None and AFTER_ADDENDUM.search(before):  # '…법 부칙 제2조제1항': 다른 법률 부칙의 조문
            doc = '__external__'
        if doc is None:
            if gap is not None and prev and GLUE.match(gap):
                doc = prev[0]
            elif OTHER_NAME.search(before):
                doc = '__external__'
            else:
                doc = own
        start = prev[1] if (prev and prev[0] == doc and gap is not None and re.fullmatch(r'\s*부터\s*', gap)) else None
        out.append((doc, no, start))
        prev, prev_end = (doc, no), m.end()
        sub = SUB.match(text, m.end())
        if sub:
            prev_end = sub.end()
    return out


def expand(doc_refs, order):
    """(문서, 조문, 범위시작) → 조문 ID 목록. 범위는 문서 내 조문 순서로 펼친다."""
    ids = []
    for doc, no, start in doc_refs:
        aid = f'{doc} {no}'
        if start and doc in order and f'{doc} {start}' in order[doc] and aid in order[doc]:
            seq = order[doc]
            i, j = seq.index(f'{doc} {start}'), seq.index(aid)
            ids.extend(seq[i + 1 : j + 1])
        else:
            ids.append(aid)
    return ids


def join(ids):
    ids = list(dict.fromkeys(ids))
    return '|'.join(ids) if ids else None


def doc_name(law, kind):
    return law if kind == '법률' else f'{law} {kind}'


def doc_names(doc_list=None):
    """공백 없앤 이름 → 문서명 (「…」 참조를 문서로)"""
    pairs = [(law, kind) for law, kind, _ in doc_list] if doc_list else doc_order()
    return {re.sub(r'\s+', '', doc_name(law, k)): doc_name(law, k) for law, k in pairs}


def find_files(repo: Repo, asof, doc_list=None):
    """문서명 → 파일 경로. 법령 폴더에서 기준일 버전의 머리말 제목이 문서명이고, 그 버전이 폐지 커밋이 아닌 파일
    (예: 근로기준법 폴더의 법률.md는 1997년 '(폐지)' 커밋으로 끝난 옛 법, 법률(법률).md가 현행. 머리말 상태는 둘 다 '시행')
    생성 대상으로 지정된 문서마다 파일이 하나여야 한다."""
    kinds_of: dict[str, list[str]] = {}
    for law, kind, _ in doc_list or docs(None):
        kinds_of.setdefault(law, []).append(kind)
    scope_folders = {entry.name: entry.folder for entry in law_scope.load_law_scope()}
    folders = {law: f'kr/{scope_folders.get(law, law.replace(" ", ""))}/' for law in kinds_of}
    candidates = {}
    for law, folder in folders.items():
        files = [f for f in repo.git('ls-tree', '--name-only', 'HEAD', folder).splitlines() if f.endswith('.md')]
        if files:
            repo.load_history(files)
        candidates[law] = []
        for path in files:
            latest = next((h for h in repo.history.get(path, []) if h[1] <= asof), None)
            if latest and not latest[2].endswith('(폐지)'):
                candidates[law].append((latest[0], path))
    items = list(dict.fromkeys(item for matches in candidates.values() for item in matches))
    titles = {
        item: front(md).get('제목', '').replace(' ', '') for item, md in zip(items, repo.show_many(items), strict=True)
    }
    paths = {}
    for law, kinds in kinds_of.items():
        for kind in kinds:
            doc = doc_name(law, kind)
            found = [path for sha, path in candidates[law] if titles[(sha, path)] == doc.replace(' ', '')]
            if len(found) != 1:
                raise ValueError(f'{doc}: 시행 중인 파일이 하나가 아닙니다 ({found or "없음"}, 폴더 {folders[law]})')
            paths[doc] = found[0]
    return paths


# ── 조문 행 ──────────────────────────────────────────────────────────


def load_scope(path):
    """범위 목록 CSV(law·kind·article_no 열) → {(법령, 종류): {조문번호}}. 조문 일부만 넣는 문서"""
    df = pd.read_csv(path, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    scope: dict[tuple[str, str], set[str]] = {}
    for law, kind, no in df[['law', 'kind', 'article_no']].itertuples(index=False):
        scope.setdefault((law, kind), set()).add(no)
    if Path(path).resolve() == TAX_SCOPE.resolve():
        registered = {entry.name for entry in law_scope.load_law_scope()}
        if missing := {law for law, _ in scope} - registered:
            raise ValueError(f'조문 범위 목록의 법령이 공통 범위에 없습니다: {", ".join(sorted(missing))}')
    return scope


def v4_laws():
    """Keep the default v4 corpus independent of subsequent v5 scope changes."""
    baseline = manifest.load(V4_ARTICLES)
    if not baseline or not baseline.get('articles_by_law'):
        raise ValueError('v4 매니페스트에 법령 범위가 없습니다.')
    return baseline['articles_by_law'].keys()


def doc_order(scope=None):
    """전체 법률은 공통 범위 순서, 선언된 하위법령은 법령 이름 가나다순으로 시행령 → 시행규칙.
    순서를 바꾸지 않는 이유: 조문 임베딩(같은 batch에 든 문서에 따라 fp16 값이 달라짐)과 저장된 실험 산출물
    (data/decree_exp, 후보 번호 = 행 순번)이 이 순서에 묶여 있다"""
    entries = law_scope.load_law_scope()
    if scope is None:
        included = set(v4_laws())
        if missing := included - {entry.name for entry in entries}:
            raise ValueError(f'v4 매니페스트의 법령이 공통 범위에 없습니다: {", ".join(sorted(missing))}')
    else:
        partial = {law for law, _ in scope}
        included = {entry.name for entry in entries} - partial
    full = {entry.name: entry.decrees for entry in entries if entry.name in included}
    return [(law, '법률') for law in full] + [
        (law, kind) for law in sorted(full) for kind in KINDS[1:] if kind in full[law]
    ]


def docs(scope):
    """[(법령, 종류, 범위)] 만들 문서와 행 순서. 전체 법령 문서 뒤에 범위 목록 문서를 붙인다.
    범위 목록의 법률은 목록 순서, 하위법령은 법령 이름순으로 시행령 → 시행규칙 순서다."""
    out = [(law, kind, None) for law, kind in doc_order(scope)]
    if not scope:
        return out
    laws = list(dict.fromkeys(law for law, _ in scope))
    order = [(law, '법률') for law in laws] + [(law, k) for law in sorted(laws) for k in KINDS if k != '법률']
    return out + [(law, kind, scope[(law, kind)]) for law, kind in order if (law, kind) in scope]


def build_rows(repo: Repo, paths, asof, doc_list=None):
    """조문 행과 버전 기록. 문서마다 본칙 조문 뒤에 그 조문을 대신하는 부칙 특례 조문(addendum_overrides).
    범위가 있는 문서는 범위 안 조문과 그 조문의 부칙 특례만"""
    rows, versions = [], []
    doc_list = list(doc_list or docs(None))
    histories = _effective_histories(repo, (paths[doc_name(law, kind)] for law, kind, _ in doc_list), asof)
    repo.prefetch((sha, path) for path, hist in histories.items() for sha, _, _ in hist)
    for law, kind, scope in doc_list:
        doc = doc_name(law, kind)
        path = paths[doc]
        hist = histories[path]
        mds = repo.show_many([(sha, path) for sha, _, _ in hist])
        arts, used = _compose_history(path, asof, hist, mds)
        if scope is not None:
            missing = scope - {a['no'] for a in arts}
            if missing:
                raise ValueError(f'{doc}: 범위 목록의 조문이 없습니다 {sorted(missing)}')
            arts = [a for a in arts if a['no'] in scope]
        arts += addendum_overrides(mds[-1], used, asof, {a['no'] for a in arts})
        for a in arts:
            text = a['text']
            deleted = a.get('expired') or (a['title'] is None and (text is None or text.startswith('삭제')))
            rows.append(
                {
                    'law': doc,
                    'article_id': f'{doc} {a["no"]}',
                    'article_no': a['no'],
                    'title': a['title'],
                    'chapter': a['chapter'],
                    'deleted': int(bool(deleted)),
                    'text': text,
                    'effective_date': a['eff'],
                    'kind': kind,
                    'parent_law': law,
                }
            )
        versions += [{'doc': doc, 'kind': kind, 'path': paths[doc], **u} for u in used]
    if len({r['article_id'] for r in rows}) != len(rows):
        raise ValueError('조문 ID가 겹칩니다')
    return rows, versions


def link(rows, names=None):
    """related_ids·parent_ids·child_ids. 통계: 없는 조문 참조, 다른 법령 조문 참조(넣지 않음) 수"""
    names = names or doc_names()
    order: dict[str, list[str]] = {}
    for r in rows:
        order.setdefault(r['law'], []).append(r['article_id'])
    known = {r['article_id'] for r in rows}
    alive = {r['article_id'] for r in rows if not r['deleted']}
    dangling, cross = [], 0
    for r in rows:
        law = r['parent_law']
        upper = {'법률': {}, '시행령': {'법': law}, '시행규칙': {'법': law, '영': f'{law} 시행령'}}[r['kind']]
        same, up = [], []
        for aid in expand([x for x in refs(r['text'], r['law'], upper, names) if x[0] != '__external__'], order):
            doc = aid.rsplit(' ', 1)[0]
            if aid not in known:
                dangling.append((r['article_id'], aid))
            elif aid not in alive:
                continue
            elif doc == r['law']:
                if aid != r['article_id']:
                    same.append(aid)
            elif doc in upper.values():
                up.append(aid)
            else:
                cross += 1  # 다른 노동법(예: 근로기준법 시행령 → 「남녀고용평등법」)
        r['related_ids'], r['parent_ids'] = join(same), join(up)
    child: dict[str, list[str]] = {}
    for r in rows:
        if not r['deleted']:
            for p in (r['parent_ids'] or '').split('|'):
                if p:
                    child.setdefault(p, []).append(r['article_id'])
    for r in rows:
        r['child_ids'] = join(child.get(r['article_id'], []))
    return {'dangling': dangling, 'cross': cross}


# ── 실행 ─────────────────────────────────────────────────────────────


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator='\n')
    return ('\ufeff' + buf.getvalue()).encode('utf-8')


def build(repo_path, asof, scope=None, version='v4'):
    """{파일 이름: 바이트}, 버전 목록, 통계. 쓰지 않는다. scope: load_scope() 결과 (조문 일부만 넣는 문서). version: 파일 이름"""
    repo = Repo(repo_path)
    doc_list = docs(scope)
    paths = find_files(repo, asof, doc_list)
    rows, versions = build_rows(repo, paths, asof, doc_list)
    stats = link(rows, doc_names(doc_list))
    df = pd.DataFrame(rows, columns=DECREE_COLS)
    law = df[df['kind'] == '법률']
    law_csv, decree_csv = csv_names(version)
    files = {law_csv: to_csv_bytes(law[LAW_COLS]), decree_csv: to_csv_bytes(df)}
    return files, versions, {**stats, 'head': repo.head(), 'law_rows': len(law), 'decree_rows': len(df), 'paths': paths}


def first_diff(a: bytes, b: bytes):
    la, lb = a.split(b'\n'), b.split(b'\n')
    for i, (x, y) in enumerate(zip(la, lb), 1):
        if x != y:
            return i, x.decode('utf-8', 'replace')[:200], y.decode('utf-8', 'replace')[:200]
    return min(len(la), len(lb)) + 1, '(끝)', '(끝)'


def main(argv=None):
    p = argparse.ArgumentParser(description='Legalize KR → 조문 CSV (법률, 법률+하위법령)')
    p.add_argument('--repo', required=True, help='legalize-kr 저장소 (부분 클론은 Git 2.44 이상 필요)')
    p.add_argument('--asof', required=True, help='기준일 YYYY-MM-DD: 이 날 시행 중인 조문')
    p.add_argument('--out-dir', required=True, type=Path)
    p.add_argument('--check', action='store_true', help='쓰지 않고 out-dir의 기존 파일과 바이트 비교')
    p.add_argument(
        '--scope', type=Path, help='범위 목록 CSV (law·kind·article_no): 목록 법령의 선택 조문을 전체 법령 문서 뒤에'
    )
    p.add_argument('--version', default='v4', help='데이터 버전: 파일 이름 articles_<버전>.csv (기본 v4)')
    a = p.parse_args(argv)
    dt.date.fromisoformat(a.asof)

    files, versions, stats = build(a.repo, a.asof, load_scope(a.scope) if a.scope else None, a.version)
    print(f'[원문] legalize-kr HEAD {stats["head"]} · 기준일 {a.asof}')
    print(f'[조문] 법률 {stats["law_rows"]}행 · 법률+하위법령 {stats["decree_rows"]}행')
    print(f'[연계] 없는 조문 참조 {len(stats["dangling"])}건 · 다른 법령 참조 {stats["cross"]}건 제외')
    last: dict[str, dict] = {}
    for v in versions:
        last[v['doc']] = v
        if v['시행 전']:
            print(
                f'[시행 전] {v["doc"]} {v["commit"]} 제{v["공포번호"]}호 (시행 {v["시행일자"]}) 조문 {v["시행 전"]}개 제외'
            )
        for no, dates in v['mixed']:
            print(
                f'[시행 전 포함] {v["doc"]} {no}: 제{v["공포번호"]}호가 시행 전({", ".join(dates)}) 개정이 남은 본문을 다시 바꿈'
            )
    for notice in expiry_notices(versions):
        print(f'[유효기간 판정 보류] {notice}')
    for doc, v in last.items():
        print(f'[버전] {doc} 마지막 {v["commit"]} 제{v["공포번호"]}호 · {stats["paths"][doc]}')

    if a.check:
        bad = 0
        for fname, data in files.items():
            old = (a.out_dir / fname).read_bytes() if (a.out_dir / fname).is_file() else b''
            if old == data:
                print(f'[일치] {fname} ({len(data):,} bytes)')
            else:
                bad += 1
                line, mine, theirs = first_diff(data, old)
                print(f'[다름] {fname} {line}번째 줄\n  생성: {mine}\n  기존: {theirs}')
        return 1 if bad else 0

    a.out_dir.mkdir(parents=True, exist_ok=True)
    for fname, data in files.items():
        (a.out_dir / fname).write_bytes(data)
        print(f'[저장] {a.out_dir / fname}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
