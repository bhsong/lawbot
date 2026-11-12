# src/lawbot/feedback.py  →  답변 피드백(👍·깃발) 저장 (웹앱 쪽, B6)
# 답변이 끝난 턴의 입력 전체(질문·참고 조문·system·답변 원문·판정·측정값)를 메모리에 두었다가,
# Persist selected turns to the configured SQLite DB or legacy JSONL file.
# Keep question text out of request and console logs.
# 이 모듈은 웹 서버(fastapi)·모델 라이브러리(mlx·torch)를 import하지 않는다 (tests/test_feedback.py가 검사)
import fcntl
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from lawbot import manifest, security
from lawbot.paths import ROOT
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')

DEFAULT_DIR = ROOT / 'feedback'  # .gitignore
REASONS = {'검색 오류': 'retrieval', '답변 오류': 'answer', '불필요한 거절': 'false_refusal', '기타': 'other'}
TURNS_PER_SESSION = 50  # 세션마다 최근 턴만 둔다 (메모리 상한)
MAX_SESSIONS = 200  # 오래된 세션은 LRU로 버림
DEDUP_S = 2.0  # 같은 답변에 같은 버튼이 이 안에 다시 오면(두 번 누름) 저장 안 함
HIT_FIELDS = (
    'law',
    'article_id',
    'title',
    'doc',
    'score',
    'rerank_score',
    'emb_rank',
    'kind',
    'parent_law',
)  # kind·parent_law: 하위법령 hit만
TIMING_FIELDS = (
    'search',
    'rerank',
    'decree_search',
    'decree_rerank',
    'load',
    'ttft',
    'first_text',
    'total',
    'gen_tokens',
    'finish',
)


def enabled(env=None):
    """LAWBOT_FEEDBACK: '0'(기본)·'1'만"""
    env = os.environ if env is None else env
    value = (env.get('LAWBOT_FEEDBACK') or '0').strip()
    if value not in ('0', '1'):
        raise ValueError(f'LAWBOT_FEEDBACK은 0 또는 1: {value!r}')
    return value == '1'


def feedback_dir(env=None):
    """LAWBOT_FEEDBACK_DIR (상대 경로는 레포 최상위 기준), 없으면 feedback/"""
    env = os.environ if env is None else env
    value = (env.get('LAWBOT_FEEDBACK_DIR') or '').strip()
    if not value:
        return DEFAULT_DIR
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


def check_private(path, what='LAWBOT_FEEDBACK_DIR', folder=True):
    """레포 안의 경로면 .gitignore에 걸리는지 확인한다 (공개 레포: 질문 원문이 든 파일은 커밋되면 안 됨)
    레포 밖이면 그대로 통과. 걸리지 않거나 확인할 수 없으면 RuntimeError. folder=False면 path는 파일"""
    path = Path(path).resolve()
    root = ROOT.resolve()
    if path != root and root not in path.parents:
        return
    probe = (
        path / 'feedback_check.jsonl' if folder else path
    )  # 아직 없는 폴더는 'feedback/' 같은 패턴에 안 걸려 안의 파일로 묻는다
    try:
        r = subprocess.run(
            ['git', 'check-ignore', '-q', '--no-index', str(probe)], cwd=root, capture_output=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(
            f'{what}가 .gitignore에 걸리는지 확인하지 못했습니다 ({type(e).__name__}). '
            '레포 밖의 절대 경로를 지정하세요.'
        ) from e
    if r.returncode != 0:  # 1: 무시되지 않음, 128: git 오류 (레포가 아님 등)
        raise RuntimeError(
            f'{what}가 레포 안에서 .gitignore에 걸리지 않는 경로입니다: {path}. '
            '질문 원문이 커밋될 수 있어 실행을 거부합니다. 레포 밖의 절대 경로나 feedback/를 쓰세요.'
        )


_commit = None


def commit():
    """레포의 git rev-parse HEAD (처음 한 번만). 알 수 없으면 None"""
    global _commit
    if _commit is None:
        try:
            r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=10)
            _commit = r.stdout.strip() if r.returncode == 0 else ''
        except OSError, subprocess.SubprocessError:
            _commit = ''
    return _commit or None


def from_env(env=None, *, db: Path | None = None):
    """Build the enabled feedback sink, using the already validated DB when present."""
    env = os.environ if env is None else env
    if db is not None and (env.get('LAWBOT_FEEDBACK_DIR') or '').strip():
        raise ValueError(
            'LAWBOT_DB를 쓰려면 LAWBOT_FEEDBACK_DIR를 비우세요. '
            '기존 JSONL은 uv run python -m lawbot_tools.migrate_csv_to_sqlite feedback으로 옮기세요.'
        )
    if not enabled(env):
        return None
    if db is not None:
        return Feedback(db=db)
    d = feedback_dir(env)
    check_private(d)
    return Feedback(d)


# ---- 턴 기록 ----
def turn_id(session, n, request_id):
    """세션 + assistant 순번 + 요청 id의 해시. 재시도로 같은 순번에 새 답이 나오면 다른 turn_id"""
    return hashlib.sha256(f'{session}:{n}:{request_id}'.encode()).hexdigest()[:16]


def model_system(app, model_key, fmt):
    """이번 요청에 쓴 system 전문: format_system(화면 형식) → 모델·프로필 system 파일
    service.load_app의 factory와 같은 순서"""
    from lawbot import service

    return service.format_system(app, model_key, fmt) or service.system_text(app.profile, app.models.specs[model_key])


def cite_details(app, raw, hits):
    """B1 판정: [{law, article_id, verdict}] (판정에 실패하면 None)"""
    try:
        from lawbot import citation

        return [
            {'law': law, 'article_id': no, 'verdict': v}
            for law, no, v, _ in citation.check(citation.citations(raw), hits, citation.article_keys(app.retriever))
        ]
    except Exception:
        return None


def jsonable(v):
    """numpy 값 → 파이썬 값 (검색 결과의 점수·순위)"""
    return v.item() if hasattr(v, 'item') else v


def decree_settings(app, model_key):
    """이 답에 하위법령 첨부가 켜졌으면 {tau, k}, 아니면 None (service.uses_decrees와 같은 판단)"""
    dc = getattr(app, 'decree', None)
    return {'tau': dc.tau, 'k': dc.k} if dc is not None and model_key in dc.models else None


def turn_record(app, question, hits, raw, shown, model_key, fmt, st):
    """저장할 한 턴 (label·reason·feedback_id·ts·turn_id는 저장할 때 붙인다)"""
    release_id = getattr(app, 'corpus_release_id', None)
    m = manifest.for_app(app)
    system = st.get('_system')
    if system is None:
        system = model_system(app, model_key, fmt)
    articles_sha = getattr(app.retriever, 'articles_sha256', None)
    if release_id:
        articles_sha = app.articles_sha256
    elif not articles_sha:
        articles_sha = manifest.sha256(app.profile.articles)
    return {
        'question': question,
        'hits': [{k: jsonable(h[k]) for k in HIT_FIELDS if k in h} for h in hits],
        'system': system,
        'answer_raw': raw,
        'answer_shown': shown,
        'model': model_key,
        'fmt': fmt,
        'refused': st.get('refused'),
        'n_cited': st.get('n_cited'),
        'citations': cite_details(app, raw, hits),
        'timing': {k: st.get(k) for k in TIMING_FIELDS},
        'decree': decree_settings(app, model_key),
        'n_decree': st.get('n_decree'),
        'instance': security.instance_name(),
        'mode': getattr(app, 'llm_mode', 'api'),
        'profile': app.profile.name,
        'dataset_version': m.get('dataset_version') if m else None,
        'commit': commit(),
        'request_id': st.get('request_id'),
        'articles_sha256': articles_sha,
        'emb_model': app.profile.emb_model,
        'rerank_model': getattr(app.reranker, 'model', None),
        'chat_model': app.models.specs[model_key].model,
        'system_sha256': hashlib.sha256(system.encode()).hexdigest(),
        'corpus_release_id': release_id,
    }


class FeedbackWriteError(RuntimeError):
    """The configured feedback sink could not persist the record."""


class Feedback:
    """Keep recent turns in memory and persist feedback to one configured sink."""

    def __init__(self, folder=DEFAULT_DIR, *, db: Path | None = None):
        self.folder = Path(folder)
        self.db = db
        self.turns = OrderedDict()  # session → OrderedDict(assistant 순번 → (turn_id, 기록))
        self.recent = {}  # (turn_id, liked) → 마지막으로 받은 시각
        self.counts = OrderedDict()  # session → 다음 순번
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()

    def remember(self, session, n, record):
        """답변이 끝난 턴을 둔다. 같은 순번(재시도·실행 취소 뒤 새 답)은 새 기록으로 바꾼다"""
        if session is None:
            return
        with self.lock:
            turns = self.turns.setdefault(session, OrderedDict())
            self.turns.move_to_end(session)
            turns[n] = (turn_id(session, n, record['request_id']), record)
            turns.move_to_end(n)
            while len(turns) > TURNS_PER_SESSION:
                turns.popitem(last=False)
            while len(self.turns) > MAX_SESSIONS:
                self.turns.popitem(last=False)

    def save_row(self, tid, record, lab, key, now):
        """Persist a record once per deduplication window and return it if saved."""
        with self.write_lock:
            last = self.recent.get((tid, key))
            row = None
            if last is None or now - last >= DEDUP_S:
                row = {
                    'feedback_id': uuid.uuid4().hex,
                    'turn_id': tid,
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'label': lab[0],
                    'reason': lab[1],
                } | record
                self.write(row)
            self.recent[(tid, key)] = now
            if len(self.recent) > 1000:
                cutoff = now - DEDUP_S
                self.recent = {k: t for k, t in self.recent.items() if t >= cutoff}
            return row

    def remember_turn(self, session, record):
        """세션의 다음 순번에 끝난 턴을 두고 turn_id를 돌려준다"""
        with self.lock:
            n = self.counts.pop(session, 0)
            self.counts[session] = n + 1
            while len(self.counts) > MAX_SESSIONS:
                self.counts.popitem(last=False)
        self.remember(session, n, record)
        return turn_id(session, n, record['request_id'])

    def save(self, session, tid, label, reason=None, now=None):
        """화면의 버튼 하나. label: like·flag·cleared, flag면 reason은 REASONS의 값(retrieval 등)
        저장한 줄 (같은 버튼이 DEDUP_S 안에 다시 오면 None). 모르는 label·reason은 ValueError, 이 세션에 없는 turn_id는 LookupError"""
        if label not in ('like', 'flag', 'cleared') or (reason not in REASONS.values() if label == 'flag' else reason):
            raise ValueError(f'알 수 없는 피드백: {label!r}, {reason!r}')
        with self.lock:
            found = next((t for t in self.turns.get(session, {}).values() if t[0] == tid), None)
        if found is None:  # 앱 재시작·다른 세션의 답: 화면 글만으로는 저장하지 않는다 (참고 조문이 없음)
            raise LookupError('기록이 없는 답변')
        now = time.monotonic() if now is None else now
        return self.save_row(tid, found[1], (label, reason), f'{label}:{reason}', now)

    def path(self):
        return self.folder / f'feedback_{time.strftime("%Y-%m")}.jsonl'

    def write(self, row):
        """Append to the configured sink or raise FeedbackWriteError."""
        if self.db is not None:
            from lawbot import feedback_store

            try:
                feedback_store.write(self.db, row)
            except (OSError, RuntimeError, feedback_store.DatabaseError) as exc:
                log.warning('경고: 피드백을 쓰지 못했습니다 (%s)', type(exc).__name__)
                raise FeedbackWriteError from exc
            return
        path = self.path()
        line = (json.dumps(row, ensure_ascii=False, default=str) + '\n').encode('utf-8')
        try:
            self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                size = os.fstat(fd).st_size
                try:
                    remaining = memoryview(line)
                    while remaining:
                        written = os.write(fd, remaining)
                        if written == 0:
                            raise OSError('Feedback append made no progress')
                        remaining = remaining[written:]
                except OSError:
                    if len(remaining) < len(line):
                        os.ftruncate(fd, size)
                    raise
            finally:
                os.close(fd)  # 닫으면 잠금도 풀린다
        except OSError as exc:
            log.warning('경고: 피드백을 쓰지 못했습니다 (%s)', type(exc).__name__)
            raise FeedbackWriteError from exc
