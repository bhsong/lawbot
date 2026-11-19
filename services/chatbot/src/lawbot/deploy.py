# Web app deployment profiles pair a retrieval corpus with one answer model.
import os
from dataclasses import dataclass
from pathlib import Path

from lawbot import paths

MAX_SEQ = 2304  # 법률 조문 인코딩 최대 토큰 (v4 최장 2,088토큰을 256 단위로 올림, v4 노트북 규칙)


@dataclass(frozen=True)
class ModelSpec:
    key: str  # Stable identifier in request logs and feedback records.
    label: str  # Display name in the web app.
    model: str  # Model ID sent in the chat completions request.


@dataclass(frozen=True)
class Profile:
    name: str  # 'v4' / 'v5' (요청 로그·피드백 기록에 남김)
    title: str  # 웹앱 제목
    articles: Path  # 조문 CSV
    emb_model: str  # 검색 임베딩 모델 id (모델 서버 /v1/embeddings)
    max_seq: int  # 조문 인코딩 최대 토큰
    system_file: Path  # System prompt for the selected corpus profile.
    model: ModelSpec


def _model(name, default, env):
    return (env.get(name) or '').strip() or default


def _positive_int(name, default, env):
    """양의 정수 설정 (없으면 default)"""
    raw = (env.get(name) or '').strip() or str(default)
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f'{name}는 1 이상의 정수: {raw!r}')
    return int(raw)


def _path(name, default, env):
    """환경 변수가 있으면 그 경로 (조문 CSV·system 파일). 상대 경로는 레포 최상위 기준 절대 경로로 바꾼다"""
    p = Path(env.get(name) or default).expanduser()
    return p if p.is_absolute() else paths.ROOT / p


def profile_name(env=None) -> str:
    env = os.environ if env is None else env
    name = (env.get('LAWBOT_PROFILE') or '').strip() or 'v4'
    if name not in ('v4', 'v5'):
        raise ValueError(f'LAWBOT_PROFILE은 v4 또는 v5여야 합니다: {name!r}')
    return name


def article_paths(name: str, env=None) -> tuple[Path, Path]:
    """Return the law-only and combined CSV paths for the selected profile."""
    env = os.environ if env is None else env
    data = paths.DATA / name
    prefix = f'LAWBOT_{name.upper()}'
    return (
        _path(f'{prefix}_ARTICLES', data / f'articles_{name}.csv', env),
        _path(f'{prefix}_DECREE_ARTICLES', data / f'law_articles_{name}_decree.csv', env),
    )


def get_profile(env=None) -> Profile:
    """Select the retrieval corpus and the Pro system prompt; default to v4."""
    env = os.environ if env is None else env
    name = profile_name(env)
    law, combined = article_paths(name, env)
    v5 = name == 'v5'
    pro_system = _path('LAWBOT_PRO_SYSTEM', paths.V5_SYSTEM_CONCISE if v5 else paths.V4_SYSTEM_CONCISE, env)
    pro = ModelSpec('pro', 'Lawbot Pro', _model('LAWBOT_PRO_MODEL', 'qwen3.8-27b-4bit', env))
    return Profile(
        name,
        'Lawbot',
        combined if decree_enabled(env) else law,
        _model('LAWBOT_EMB_MODEL', 'lawbot-emb-v6' if v5 else 'lawbot-emb-v4', env),
        _positive_int('LAWBOT_MAX_SEQ', 4352 if v5 else MAX_SEQ, env),
        pro_system,
        pro,
    )


@dataclass(frozen=True)
class RerankerConfig:
    model: str  # 리랭커 모델 id (모델 서버 /v1/rerank, Qwen3-Reranker-4B)
    candidates: int  # 리랭커에 넘길 임베딩 상위 후보 수 (v4 실험 10). 최종 조문 수는 service.TOP_K


def reranker(profile, env=None):
    """LAWBOT_RERANKER_ENABLED=1이면 리랭커 설정, 아니면 None (기본 꺼짐: 임베딩 상위 3개를 그대로 씀)"""
    env = os.environ if env is None else env
    flag = env.get('LAWBOT_RERANKER_ENABLED', '0')
    if flag not in ('0', '1'):
        raise ValueError(f'LAWBOT_RERANKER_ENABLED는 0 또는 1: {flag!r}')
    if flag == '0':
        return None
    raw = env.get('LAWBOT_RERANKER_CANDIDATES') or '10'
    if not raw.strip().isdigit() or int(raw) < 1:
        raise ValueError(f'LAWBOT_RERANKER_CANDIDATES는 1 이상의 정수: {raw!r}')
    return RerankerConfig(_model('LAWBOT_RERANK_MODEL', 'qwen3-reranker-4b', env), int(raw))


# ---- 시행령·시행규칙(하위법령) 첨부 (docs/archives/decree-attach.md) ----
# 법률 검색(임베딩 10 → 리랭커 → 3개)은 그대로 두고, 하위법령 397개를 따로 임베딩 10 → 같은 리랭커 → 필터 → 최대 k개 첨부
# 필터(val로 고른 same1+other0.9): 법률 1위와 같은 법의 하위법령은 그대로, 다른 법은 리랭커 점수 ≥ τ일 때만 (lawbot.decree)
DECREE_CANDIDATES = 10  # 하위법령 임베딩 후보 수 (실험과 같음)
DECREE_MAX_SEQ = 3072  # 하위법령 인코딩 최대 토큰 (최장 3,017토큰 · 실험과 같음). 법률은 프로필 max_seq 그대로
# 다른 조문 CSV는 LAWBOT_MAX_SEQ·LAWBOT_DECREE_MAX_SEQ (lawbot-max-seq가 같은 규칙으로 계산, v5: 4,352 · 5,120)
DECREE_TAU, DECREE_K = 0.9, 2


@dataclass(frozen=True)
class DecreeConfig:
    tau: float  # 다른 법 하위법령이 통과할 리랭커 점수
    k: int  # 최대 첨부 수
    candidates: int = DECREE_CANDIDATES
    max_seq: int = DECREE_MAX_SEQ


def decree_max_seq(env=None):
    """하위법령 인코딩 최대 토큰 (LAWBOT_DECREE_MAX_SEQ, 없으면 DECREE_MAX_SEQ). 웹앱과 캐시(lawbot-reindex)가 같이 쓴다"""
    env = os.environ if env is None else env
    default = 5120 if profile_name(env) == 'v5' else DECREE_MAX_SEQ
    return _positive_int('LAWBOT_DECREE_MAX_SEQ', default, env)


def decree_enabled(env=None):
    env = os.environ if env is None else env
    flag = env.get('LAWBOT_DECREE_ENABLED', '0')
    if flag not in ('0', '1'):
        raise ValueError(f'LAWBOT_DECREE_ENABLED는 0 또는 1: {flag!r}')
    return flag == '1'


def decree(profile, env=None):
    """LAWBOT_DECREE_ENABLED=1이면 하위법령 첨부 설정, 아니면 None (기본 꺼짐: 법률 3개만)
    리랭커 점수로 거르므로 리랭커가 꺼져 있으면 거부"""
    env = os.environ if env is None else env
    if not decree_enabled(env):
        return None
    if reranker(profile, env) is None:
        raise ValueError(
            '시행령·시행규칙 첨부는 리랭커 점수로 거르므로 리랭커가 필요합니다. '
            'LAWBOT_RERANKER_ENABLED=1로 켜거나 LAWBOT_DECREE_ENABLED=0으로 끄세요.'
        )
    raw = env.get('LAWBOT_DECREE_TAU') or str(DECREE_TAU)
    try:
        tau = float(raw)
    except ValueError:
        raise ValueError(f'LAWBOT_DECREE_TAU는 0~1 숫자: {raw!r}') from None
    if not 0.0 <= tau <= 1.0:
        raise ValueError(f'LAWBOT_DECREE_TAU는 0~1 숫자: {raw!r}')
    raw = env.get('LAWBOT_DECREE_K') or str(DECREE_K)
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f'LAWBOT_DECREE_K는 1 이상의 정수: {raw!r}')
    return DecreeConfig(tau, int(raw), max_seq=decree_max_seq(env))
