"""legalize-kr(법령)·admrule-kr(행정규칙) git 저장소 수집기 (원 계획서 5.4 A, 1·3·5·7번).

- 커서 커밋부터 HEAD까지 대상 파일을 건드린 커밋마다 파일 하나에 이벤트 하나를 만든다 (커밋 하나 = 개정 하나).
- 날짜 필터는 쓰지 않는다: legalize-kr은 커밋 날짜가 공포일이라 오늘 들어온 커밋이 과거 날짜일 수 있다.
- 커서 커밋이 HEAD의 조상이 아니면(강제 푸시로 이력 재구성) HEAD 내용을 items 테이블의 blob과 비교한다.
- 머리말 첨부 목록은 링크를 빼고 비교한다 (개정마다 모든 첨부 링크가 바뀜, docs/plans/monitor-collector.md 2.1).
"""

import difflib
import logging
from dataclasses import asdict, dataclass, field

from law_monitor import articles, frontmatter
from law_monitor.collectors.base import CollectResult, RunContext
from law_monitor.config import Config
from law_monitor.events import make_event_id
from law_monitor.gitrepo import ZERO, CommitInfo, GitError, GitRepo
from law_monitor.state import ItemState, State

log = logging.getLogger('law_monitor.legalize_git')


@dataclass(frozen=True)
class Version:
    path: str
    blob: str
    meta: dict
    body: str
    text: str


class LawMatcher:
    """legalize-kr kr/<법령명>/<법률|시행령|시행규칙...>.md. 같은 법령인지는 경로로 판단한다."""

    def __init__(self, config: Config):
        self.config = config

    def pathspecs(self) -> list[str] | None:
        return [f'kr/{law.name}/' for law in self.config.laws] or None

    def prefilter(self, path: str) -> bool:
        parts = path.split('/')
        return (
            len(parts) == 3
            and parts[0] == 'kr'
            and parts[2].endswith('.md')
            and self.config.law_category(parts[1]) is not None
        )

    def categories(self, v: Version) -> list[int]:
        cat = self.config.law_category(v.path.split('/')[1])
        return [cat] if cat else []

    def item_id(self, v: Version) -> str:
        return v.path

    def target(self, v: Version) -> dict:
        m = v.meta
        return {'law_name': m.get('제목'), 'item_id': v.path, 'law_id': m.get('법령ID'), 'status': m.get('상태')}

    def dates(self, v: Version) -> dict:
        return {'promulgation': v.meta.get('공포일자'), 'effective': v.meta.get('시행일자')}

    def change_type(self, old: Version | None, new: Version | None) -> str:
        if old is None:
            return 'law_new'
        if new is None or (new.meta.get('상태') == '폐지' and old.meta.get('상태') != '폐지'):
            return 'law_repealed'
        return 'law_amended'


class AdmruleMatcher:
    """admrule-kr {기관경로}/{종류}/{행정규칙명}/본문.md. 같은 규칙인지는 행정규칙ID로 판단한다 (부처 이름이 바뀌면 경로가 바뀜)."""

    def __init__(self, config: Config):
        self.config = config

    def pathspecs(self) -> list[str] | None:
        return None  # 경로가 미리 정해지지 않음 → prefilter + 머리말

    def prefilter(self, path: str) -> bool:
        """내용을 받기 전에 경로만으로 거른다: 부처 폴더가 경로에 있고 폴더 이름(=행정규칙명)이 규칙에 맞는 것."""
        parts = path.split('/')
        if len(parts) < 3 or parts[-1] != '본문.md':
            return False
        folder = parts[-2]
        names = {folder, folder.rsplit('_', 1)[0]}  # 이름이 겹치면 _발령번호가 붙음
        agencies = self.config.admrule_agencies & set(parts[:-2])
        return any(self.config.admrule_categories(a, n) for a in agencies for n in names)

    def categories(self, v: Version) -> list[int]:
        return self.config.admrule_categories(str(v.meta.get('소관부처명') or ''), str(v.meta.get('행정규칙명') or ''))

    def item_id(self, v: Version) -> str:
        return str(v.meta.get('행정규칙ID') or v.meta.get('행정규칙일련번호') or v.path)

    def target(self, v: Version) -> dict:
        return {'law_name': v.meta.get('행정규칙명'), 'item_id': self.item_id(v), 'agency': v.meta.get('소관부처명')}

    def dates(self, v: Version) -> dict:
        return {'promulgation': v.meta.get('발령일자'), 'effective': v.meta.get('시행일자')}

    def change_type(self, old: Version | None, new: Version | None) -> str:
        return 'admrule_new' if old is None else 'admrule_amended'  # 삭제는 amended + removed (계획 8절)


class LegalizeGitCollector:
    def __init__(self, name: str, repo_key: str, matcher: LawMatcher | AdmruleMatcher):
        self.name, self.repo_key, self.matcher = name, repo_key, matcher

    def has_baseline(self, state: State) -> bool:
        return state.cursor(self.repo_key) is not None

    def _repo(self, ctx: RunContext) -> GitRepo:
        repo = GitRepo(ctx.paths.repos / self.repo_key)
        repo.ensure(ctx.config.repos[self.repo_key])
        return repo

    def collect(self, ctx: RunContext) -> CollectResult:
        repo = self._repo(ctx)
        head = repo.head()
        if ctx.mode == 'bootstrap':
            return self._baseline(ctx, repo, head)
        cursor = ctx.state.cursor(self.repo_key)
        if cursor == head:
            return CollectResult(cursors={self.repo_key: head})
        build = _Builder(self, ctx, repo, ctx.state.items(self.name))
        if cursor and repo.is_ancestor(cursor, head):
            for sha in repo.rev_list(cursor, head, self.matcher.pathspecs()):
                build.commit(sha)
        else:
            log.warning(
                '%s: 커서 커밋 %s이 지금 저장소 이력에 없음 (강제 푸시로 이력 재구성?). 내용 비교로 대신함',
                self.repo_key,
                (cursor or '')[:12],
            )
            build.compare_with_head(head)
        result = build.result()
        result.cursors = {self.repo_key: head}
        return result

    def replay(self, ctx: RunContext, since: str, until: str) -> tuple[CollectResult, str, str]:
        """since..until 구간을 다시 만든다. 상태(커서·items)는 읽지도 바꾸지도 않는다.

        since·until은 'abc^'·'HEAD' 같은 이름도 되고, 실제 커밋 해시도 함께 돌려준다 (결과 폴더 이름용).
        """
        repo = self._repo(ctx)
        since_sha, until_sha = repo.resolve(since), repo.resolve(until)
        build = _Builder(self, ctx, repo, {}, replay=True)
        for sha in repo.rev_list(since_sha, until_sha, self.matcher.pathspecs()):
            build.commit(sha)
        result = build.result()
        result.items, result.removed = [], []
        return result, since_sha, until_sha

    def _baseline(self, ctx: RunContext, repo: GitRepo, head: str) -> CollectResult:
        build = _Builder(self, ctx, repo, {})
        found = build.scan(head)
        for v in found.values():
            build.save(v)
        items = [ItemState(self.name, key, v.blob, v.blob, v.path) for key, v in found.items()]
        log.info('%s: 기준점 %s, 대상 파일 %d개', self.repo_key, head[:12], len(items))
        return CollectResult(cursors={self.repo_key: head}, items=items, replace_sources=[self.name])


@dataclass
class _Builder:
    collector: LegalizeGitCollector
    ctx: RunContext
    repo: GitRepo
    current: dict[str, ItemState]
    replay: bool = False
    events: dict[str, dict] = field(default_factory=dict)
    diffs: dict[str, str] = field(default_factory=dict)
    touched: set[str] = field(default_factory=set)

    @property
    def m(self):
        return self.collector.matcher

    def version(self, path: str | None, blob: str) -> Version | None:
        if not path or blob == ZERO:
            return None
        text = self.repo.blob(blob)
        meta, body = frontmatter.split(text)
        return Version(path, blob, meta, body, text)

    def save(self, v: Version) -> str:
        """파일 내용 사본 data/files/<blob>.md (강제 푸시 뒤 비교·이벤트 old/new 파일)."""
        path = self.ctx.paths.files / f'{v.blob}.md'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(v.text, encoding='utf-8')
        return self.ctx.paths.rel(path)

    def from_item(self, item: ItemState) -> Version | None:
        snapshot = self.ctx.paths.files / f'{item.version_key}.md'
        try:
            text = snapshot.read_text(encoding='utf-8') if snapshot.exists() else self.repo.blob(item.version_key)
        except GitError:
            log.warning('%s: 이전 내용을 찾을 수 없음 (%s)', item.item_id, item.version_key[:12])
            return None
        meta, body = frontmatter.split(text)
        return Version(item.file_path, item.version_key, meta, body, text)

    def scan(self, rev: str) -> dict[str, Version]:
        out = {}
        for path, blob in self.repo.ls_tree(rev):
            if self.m.prefilter(path) and (v := self.version(path, blob)) and self.m.categories(v):
                out[self.m.item_id(v)] = v
        return out

    def commit(self, sha: str) -> None:
        groups: dict[str, list[Version | None]] = {}
        for ch in self.repo.changes(sha, self.m.pathspecs()):
            if not any(self.m.prefilter(p) for p in (ch.old_path, ch.new_path) if p):
                continue
            old, new = self.version(ch.old_path, ch.old_blob), self.version(ch.new_path, ch.new_blob)
            old = old if old and self.m.categories(old) else None
            new = new if new and self.m.categories(new) else None  # 대상에서 빠지면 제거로 본다
            if old is None and new is None:
                continue
            pair = groups.setdefault(self.m.item_id(new or old), [None, None])
            pair[0] = pair[0] or old  # 같은 커밋에서 삭제 + 추가(같은 ID) → 한 변경으로
            pair[1] = new or pair[1]
        if groups:
            info = self.repo.info(sha)
            for key, (old, new) in groups.items():
                self.emit(key, old, new, info)

    def compare_with_head(self, head: str) -> None:
        found = self.scan(head)
        for key, new in found.items():
            item = self.current.get(key)
            if item is None or item.version_key != new.blob:
                self.emit(key, None, new, None)
        for key in [k for k in self.current if k not in found]:
            self.emit(key, None, None, None)

    def emit(self, key: str, old: Version | None, new: Version | None, commit: CommitInfo | None) -> None:
        if old is None and key in self.current:
            old = self.from_item(self.current[key])
        if old is None and new is None:
            self._forget(key)
            return
        if old and new:
            same = old.body == new.body and not frontmatter.meta_changes(old.meta, new.meta)
            if old.blob == new.blob or (same and frontmatter.is_empty(_attachments(old, new))):
                self._track(key, new)  # 경로 이동·링크만 바뀜·현행 스냅샷 재커밋: 이벤트 없음
                return

        ref = new or old
        cats = self.m.categories(ref)
        version_key = new.blob if new else f'removed:{old.blob}'
        event_id = make_event_id(self.collector.name, key, version_key)
        old_body, new_body = (old.body if old else ''), (new.body if new else '')
        diff_text = '\n'.join(
            difflib.unified_diff(
                old_body.splitlines(),
                new_body.splitlines(),
                fromfile=f'a/{old.path}' if old else '/dev/null',
                tofile=f'b/{new.path}' if new else '/dev/null',
                lineterm='',
            )
        )
        event = {
            'event_id': event_id,
            'detected_at': self.ctx.now().isoformat(timespec='seconds'),
            'category': cats[0] if cats else None,
            'categories': cats,
            'source': self.collector.name,
            'change_type': self.m.change_type(old, new),
            'removed': new is None,
            'target': self.m.target(ref),
            'dates': self.m.dates(ref),
            'old': self._side(old),
            'new': self._side(new),
            'diff': {'kind': 'unified_text', 'path': None, 'chunks': articles.chunk_diffs(old_body, new_body)},
            'meta_changes': frontmatter.meta_changes(old.meta, new.meta) if old and new else {},
            'attachments': _attachments(old, new),
            'context': {
                'amendment_text': None,  # 법제처 API(PR 2)가 채움
                'amendment_reason': None,
                'source_url': ref.meta.get('출처'),
                'repo': self.collector.repo_key,
                'commit': asdict(commit) if commit else None,
                'addenda_added': articles.added_addenda(old_body, new_body) if old and new else [],
                'fallback': None if commit else 'content_compare',
            },
        }
        if self.replay:
            event['replay'] = True
        self.events[event_id] = event
        if diff_text:
            self.diffs[event_id] = diff_text
        if new:
            self._track(key, new)
        else:
            self._forget(key)

    def _side(self, v: Version | None) -> dict | None:
        return None if v is None else {'version_key': v.blob, 'file': self.save(v), 'path': v.path}

    def _track(self, key: str, v: Version) -> None:
        self.save(v)
        self.current[key] = ItemState(self.collector.name, key, v.blob, v.blob, v.path)
        self.touched.add(key)

    def _forget(self, key: str) -> None:
        self.current.pop(key, None)
        self.touched.add(key)

    def result(self) -> CollectResult:
        name = self.collector.name
        return CollectResult(
            events=list(self.events.values()),
            diffs=self.diffs,
            items=[self.current[k] for k in sorted(self.touched) if k in self.current],
            removed=[(name, k) for k in sorted(self.touched) if k not in self.current],
        )


def _attachments(old: Version | None, new: Version | None) -> dict:
    return frontmatter.attachment_changes(
        old.meta.get('첨부파일') if old else [], new.meta.get('첨부파일') if new else []
    )


def default_collectors(config: Config) -> list[LegalizeGitCollector]:
    return [
        LegalizeGitCollector('legalize_law', 'legalize-kr', LawMatcher(config)),
        LegalizeGitCollector('legalize_admrule', 'admrule-kr', AdmruleMatcher(config)),
    ]
