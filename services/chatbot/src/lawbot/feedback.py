# src/lawbot/feedback.py  →  답변 피드백(👍·깃발) 저장 (웹앱 쪽, B6)
# 답변이 끝난 턴의 입력 전체(질문·참고 조문·system·답변 원문·판정·측정값)를 메모리에 두었다가,
# 사용자가 그 답에 버튼을 누르면 LAWBOT_FEEDBACK_DIR/feedback_YYYY-MM.jsonl에 한 줄을 덧붙인다
# 질문 원문은 이 파일에만, 버튼을 누른 답에만 남는다 (요청 로그·콘솔 로그에는 남기지 않음)
# 이 모듈은 gradio·모델 라이브러리(mlx·torch)를 import하지 않는다 (tests/test_feedback.py가 검사)
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

DEFAULT_DIR = ROOT / 'feedback'  # .gitignore
REASONS = {'검색 오류': 'retrieval', '답변 오류': 'answer', '불필요한 거절': 'false_refusal', '기타': 'other'}
LIKE = 'Like'  # Gradio Chatbot이 👍 아이콘으로 그리는 문자열
OPTIONS = [LIKE, *REASONS]  # 'Dislike'는 넣지 않는다 (👎 없음). 나머지는 깃발 아래 선택지
TURNS_PER_SESSION = 50  # 세션마다 최근 턴만 둔다 (메모리 상한)
MAX_SESSIONS = 200  # 오래된 세션은 LRU로 버림
DEDUP_S = 2.0  # 이유 클릭 한 번이 이벤트 두 번으로 들어온다: 같은 값이 이 안에 다시 오면 저장 안 함
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


def enabled():
    """LAWBOT_FEEDBACK: '0'(기본)·'1'만"""
    value = (os.environ.get('LAWBOT_FEEDBACK') or '0').strip()
    if value not in ('0', '1'):
        raise ValueError(f'LAWBOT_FEEDBACK은 0 또는 1: {value!r}')
    return value == '1'


def feedback_dir():
    """LAWBOT_FEEDBACK_DIR (상대 경로는 레포 최상위 기준), 없으면 feedback/"""
    value = (os.environ.get('LAWBOT_FEEDBACK_DIR') or '').strip()
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


def from_env():
    """LAWBOT_FEEDBACK=1이면 Feedback (저장 폴더를 모델 로딩 전에 확인), 아니면 None"""
    if not enabled():
        return None
    d = feedback_dir()
    check_private(d)
    return Feedback(d)


# ---- 턴 기록 ----
def turn_id(session, n, request_id):
    """세션 + assistant 순번 + 요청 id의 해시. 재시도로 같은 순번에 새 답이 나오면 다른 turn_id"""
    return hashlib.sha256(f'{session}:{n}:{request_id}'.encode()).hexdigest()[:16]


def assistant_index(history, index):
    """Chatbot 메시지 위치 → 몇 번째 assistant 메시지인지 (0부터). assistant 메시지가 아니면 None"""
    if isinstance(index, (list, tuple)):  # 2차원 형식(튜플 index)이면 첫 값이 메시지 위치
        index = index[0]
    if not isinstance(index, int) or not 0 <= index < len(history) or role(history[index]) != 'assistant':
        return None
    return sum(role(m) == 'assistant' for m in history[:index])


def role(m):
    return m.get('role') if isinstance(m, dict) else getattr(m, 'role', None)


def text_of(value):
    """Chatbot 메시지 값(문자열, {'type': 'text', 'text': …}, 그 목록)에서 글만 이어 붙인다"""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in ('text', 'content', 'value'):
            if k in value:
                return text_of(value[k])
        return ''
    if isinstance(value, (list, tuple)):
        return ''.join(text_of(v) for v in value)
    return text_of(getattr(value, 'content', None))


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
    m = manifest.load(app.profile.articles)
    return {
        'question': question,
        'hits': [{k: jsonable(h[k]) for k in HIT_FIELDS if k in h} for h in hits],
        'system': model_system(app, model_key, fmt),
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
    }


class Feedback:
    """웹앱 하나의 피드백 저장소: 메모리의 턴 기록 + JSONL 파일 (Gradio 이벤트 함수는 app.py가 이것을 부른다)"""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.turns = OrderedDict()  # session → OrderedDict(assistant 순번 → (turn_id, 기록))
        self.recent = {}  # (turn_id, liked) → 마지막으로 받은 시각
        self.lock = threading.Lock()

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

    def lookup(self, session, n):
        with self.lock:
            return self.turns.get(session, {}).get(n)

    def label(self, liked):
        """Gradio liked 값 → (label, reason). True: 👍, 이유 문자열: 깃발, ''·None·False: 해제. 모르는 값은 None"""
        if liked is True:
            return 'like', None
        if liked in (None, False, ''):  # 프론트엔드는 선택을 풀면 ''를 보낸다 (Gradio 6.30.0)
            return 'cleared', None
        if isinstance(liked, str) and liked in REASONS:
            return 'flag', REASONS[liked]
        return None

    def duplicate(self, tid, liked, now):
        with self.lock:
            last = self.recent.get((tid, liked))
            self.recent[(tid, liked)] = now
            if len(self.recent) > 1000:
                cutoff = now - DEDUP_S
                self.recent = {k: t for k, t in self.recent.items() if t >= cutoff}
            return last is not None and now - last < DEDUP_S

    def on_like(self, session, history, index, value, liked, now=None):
        """버튼 이벤트 하나. 저장한 줄(dict)을 돌려주고, 저장하지 않으면 None (이유는 콘솔에 turn_id만)"""
        now = time.monotonic() if now is None else now
        lab = self.label(liked)
        if lab is None:
            print(f'[lawbot] 피드백: 알 수 없는 선택 {liked!r} → 저장 안 함')
            return None
        n = assistant_index(history or [], index)
        found = None if n is None else self.lookup(session, n)
        if found is None:  # 앱 재시작·대화가 바뀜: 화면 글만으로는 저장하지 않는다 (참고 조문이 없음)
            print(f'[lawbot] 피드백: 기록이 없는 답변 (turn {"?" if n is None else n}) → 저장 안 함')
            return None
        tid, record = found
        if text_of(value).strip() != record['answer_shown'].strip():  # 엉뚱한 답에 라벨이 붙지 않게
            print(f'[lawbot] 피드백: 화면 글이 기록과 다름 (turn_id {tid}) → 저장 안 함')
            return None
        if self.duplicate(tid, liked, now):
            return None
        row = {
            'feedback_id': uuid.uuid4().hex,
            'turn_id': tid,
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'label': lab[0],
            'reason': lab[1],
        } | record
        self.write(row)
        return row

    def path(self):
        return self.folder / f'feedback_{time.strftime("%Y-%m")}.jsonl'

    def write(self, row):
        """한 줄 덧붙이기 (파일 잠금, 권한 600). 실패는 답변을 막지 않고 콘솔에 경고만"""
        path = self.path()
        line = (json.dumps(row, ensure_ascii=False, default=str) + '\n').encode('utf-8')
        try:
            self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                while line:
                    line = line[os.write(fd, line) :]
            finally:
                os.close(fd)  # 닫으면 잠금도 풀린다
        except OSError as e:
            print(f'[lawbot] 경고: 피드백을 쓰지 못했습니다 ({path}: {type(e).__name__})')
