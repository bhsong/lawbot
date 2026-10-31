# src/lawbot/deploy.py  →  웹앱 배포 설정: 검색 프로필(v1/v4)과 답변 모델(Fast/Pro) 레지스트리
# 모델 라이브러리를 import하지 않는다 (check_deploy·테스트가 모델 없이 읽음)
import os
from dataclasses import dataclass, replace
from pathlib import Path
from lawbot import paths

@dataclass(frozen=True)
class ModelSpec:
    key: str                 # UI·API 값 ('fast' / 'pro')
    label: str               # 화면 표시명
    info: str                # 크기·속도 설명 (정확도 비교는 쓰지 않음: 실측 전)
    path: Path               # 로컬 MLX 폴더 (절대 경로)
    fmt: str                 # 기본 답변 형식: 'full'(4섹션 원문) / 'concise'(결론 + 근거)
    system_file: Path | None = None   # 이 모델만 쓰는 system (None이면 프로필 system)

@dataclass(frozen=True)
class Profile:
    name: str                # 'v1' / 'v4'
    title: str               # 웹앱 제목
    articles: Path           # 조문 CSV
    emb: Path                # 검색 임베딩 폴더
    max_seq: int             # 조문 인코딩 최대 토큰
    doc_style: str           # 검색 문서·참고 조문 형식 (retriever.DOC_STYLES, llm.build_messages)
    system_file: Path | None # None이면 llm.SYSTEM (v1 학습 system). 모델별 system_file이 있으면 그쪽 우선
    models: tuple            # (Fast, Pro). 첫 번째가 기본 선택
    disabled: tuple = ()     # LAWBOT_MODELS에서 뺀 모델: 화면에 '사용 안 함'으로만 표시하고 올리지 않음

def _path(env, default):
    """환경 변수가 있으면 그 경로. 상대 경로는 레포 최상위 기준 절대 경로로 바꾼다
    (mlx_lm.load는 없는 상대 경로를 Hugging Face 저장소 이름으로 보고 내려받으려 함)"""
    p = Path(os.environ.get(env) or default).expanduser()
    return p if p.is_absolute() else paths.ROOT / p

def get_profile(name=None):
    """프로필 + LAWBOT_MODELS로 고른 답변 모델 (예: 'pro'면 Pro만, 'fast,pro'면 둘 다. 첫 번째가 기본 선택)
    고르지 않은 모델은 disabled로 옮겨 화면에 비활성으로 남긴다"""
    p = _profile(name or os.environ.get('LAWBOT_PROFILE', 'v4'))   # 기본 v4. v4 자산이 없으면 LAWBOT_PROFILE=v1
    keys = [k.strip() for k in os.environ.get('LAWBOT_MODELS', '').split(',') if k.strip()]
    if not keys:
        return p
    specs = {m.key: m for m in p.models}
    bad = [k for k in keys if k not in specs]
    if bad or len(set(keys)) != len(keys):
        raise ValueError(f"LAWBOT_MODELS는 {', '.join(specs)} 중에서 쉼표로: {os.environ['LAWBOT_MODELS']!r}")
    return replace(p, models=tuple(specs[k] for k in keys),
                   disabled=tuple(m for m in p.models if m.key not in keys))

def _profile(name):
    # Fast(4B LoRA)는 학습한 4섹션 그대로, Pro는 간결형 system으로 처음부터 짧게 생성
    if name == 'v1':         # 이전 동작: 근로기준법 130조문, v1 검색·4B. Pro는 튜닝 전 14B로 속도·메모리 시험
        return Profile('v1', '근로기준법 챗봇', paths.ARTICLES, _path('LAWBOT_EMB', paths.EMB), 512, 'v1', None, (
            ModelSpec('fast', 'Lawbot Fast', '4B · 빠른 응답, 4항목 답변', _path('LAWBOT_FAST_PATH', paths.LLM_4BIT), 'full'),
            ModelSpec('pro', 'Lawbot Pro', '14B · 큰 모델, 간결한 답변, 응답이 더 느림',
                      _path('LAWBOT_PRO_PATH', paths.PRO_BASE), 'concise',
                      _path('LAWBOT_PRO_SYSTEM', paths.SYSTEM_V1_CONCISE))))
    if name == 'v4':         # 7개 법률 415조문, v4 검색 (MAX_SEQ 2304). Pro는 튜닝 전 Qwen3.8-27B 4비트
        # 하위법령을 켜면(LAWBOT_DECREE_ENABLED=1) 법률 415 + 시행령·시행규칙 397 = 812조문 CSV. 법률 415행은 같음
        articles = (_path('LAWBOT_V4_DECREE_ARTICLES', paths.V4_DECREE_ARTICLES) if decree_enabled()
                    else _path('LAWBOT_V4_ARTICLES', paths.V4_ARTICLES))
        return Profile('v4', '노동법 챗봇', articles,
                       _path('LAWBOT_V4_EMB', paths.V4_EMB), 2304, 'v4', _path('LAWBOT_V4_SYSTEM', paths.V4_SYSTEM), (
            ModelSpec('fast', 'Lawbot Fast', '4B · 빠른 응답, 4항목 답변', _path('LAWBOT_FAST_PATH', paths.V4_FAST), 'full'),
            ModelSpec('pro', 'Lawbot Pro', '27B · 큰 모델(튜닝 전), 간결한 답변, 응답이 더 느림',
                      _path('LAWBOT_PRO_PATH', paths.V4_PRO), 'concise',
                      _path('LAWBOT_PRO_SYSTEM', paths.V4_SYSTEM_CONCISE))))
    raise ValueError(f'알 수 없는 LAWBOT_PROFILE: {name!r} (v1 또는 v4)')

def max_resident():
    """동시에 메모리에 올려 두는 답변 모델 수. 2: Fast·Pro 함께 상주, 1: 선택한 모델만 (전환 때 다시 로딩)"""
    n = int(os.environ.get('LAWBOT_MAX_RESIDENT', '2'))
    if n not in (1, 2):
        raise ValueError(f'LAWBOT_MAX_RESIDENT는 1 또는 2: {n}')
    return n

@dataclass(frozen=True)
class RerankerConfig:
    path: Path               # Qwen3-Reranker-4B MLX 폴더 (절대 경로)
    candidates: int          # 리랭커에 넘길 임베딩 상위 후보 수 (v4 실험 10). 최종 조문 수는 service.TOP_K

def reranker_path():
    return _path('LAWBOT_RERANKER_MODEL_PATH', paths.V4_RERANKER)

def reranker(profile):
    """LAWBOT_RERANKER_ENABLED=1이면 리랭커 설정, 아니면 None (기본 꺼짐: 임베딩 상위 3개를 그대로 씀)
    v4 실험(법령명이 붙은 조문 형식)으로만 검증했으므로 v1 프로필에서 켜면 거부한다"""
    flag = os.environ.get('LAWBOT_RERANKER_ENABLED', '0')
    if flag not in ('0', '1'):
        raise ValueError(f'LAWBOT_RERANKER_ENABLED는 0 또는 1: {flag!r}')
    if flag == '0':
        return None
    if profile.doc_style != 'v4':
        raise ValueError(f'리랭커는 v4 프로필에서만 쓸 수 있습니다 (현재 {profile.name}). '
                         'LAWBOT_RERANKER_ENABLED=0으로 끄거나 LAWBOT_PROFILE=v4로 실행하세요.')
    n = int(os.environ.get('LAWBOT_RERANKER_CANDIDATES', '10'))
    return RerankerConfig(reranker_path(), n)

# ---- 시행령·시행규칙(하위법령) 첨부 (docs/plans/decree-attach.md) ----
# 법률 검색(임베딩 10 → 리랭커 → 3개)은 그대로 두고, 하위법령 397개를 따로 임베딩 10 → 같은 리랭커 → 필터 → 최대 k개 첨부
# 필터(val로 고른 same1+other0.9): 법률 1위와 같은 법의 하위법령은 그대로, 다른 법은 리랭커 점수 ≥ τ일 때만 (lawbot.decree)
DECREE_CANDIDATES = 10   # 하위법령 임베딩 후보 수 (실험과 같음)
DECREE_MAX_SEQ = 3072    # 하위법령 인코딩 최대 토큰 (최장 3,017토큰 · 실험과 같음). 법률은 프로필 max_seq 그대로
DECREE_TAU, DECREE_K, DECREE_MODELS = 0.9, 2, ('pro',)

@dataclass(frozen=True)
class DecreeConfig:
    tau: float               # 다른 법 하위법령이 통과할 리랭커 점수
    k: int                   # 최대 첨부 수
    models: tuple            # 첨부를 받는 답변 모델 key. Fast(참고 조문 3개로 학습, 첨부 미평가)는 기본 제외
    candidates: int = DECREE_CANDIDATES
    max_seq: int = DECREE_MAX_SEQ

def decree_enabled():
    flag = os.environ.get('LAWBOT_DECREE_ENABLED', '0')
    if flag not in ('0', '1'):
        raise ValueError(f'LAWBOT_DECREE_ENABLED는 0 또는 1: {flag!r}')
    return flag == '1'

def decree(profile, require_reranker=True):
    """LAWBOT_DECREE_ENABLED=1이면 하위법령 첨부 설정, 아니면 None (기본 꺼짐: 법률 3개만)
    리랭커 점수로 거르므로 리랭커가 꺼져 있으면 거부. v4 실험으로만 검증했으므로 v1 프로필도 거부
    require_reranker=False: 원격 모드 웹앱 (리랭커는 모델 서버 설정. 서버가 켜짐을 /info로 알려 줌)"""
    if not decree_enabled():
        return None
    if profile.doc_style != 'v4':
        raise ValueError(f'시행령·시행규칙 첨부는 v4 프로필에서만 쓸 수 있습니다 (현재 {profile.name}). '
                         'LAWBOT_DECREE_ENABLED=0으로 끄거나 LAWBOT_PROFILE=v4로 실행하세요.')
    if require_reranker and reranker(profile) is None:
        raise ValueError('시행령·시행규칙 첨부는 리랭커 점수로 거르므로 리랭커가 필요합니다. '
                         'LAWBOT_RERANKER_ENABLED=1로 켜거나 LAWBOT_DECREE_ENABLED=0으로 끄세요.')
    raw = os.environ.get('LAWBOT_DECREE_TAU') or str(DECREE_TAU)
    try:
        tau = float(raw)
    except ValueError:
        raise ValueError(f'LAWBOT_DECREE_TAU는 0~1 숫자: {raw!r}') from None
    if not 0.0 <= tau <= 1.0:
        raise ValueError(f'LAWBOT_DECREE_TAU는 0~1 숫자: {raw!r}')
    raw = os.environ.get('LAWBOT_DECREE_K') or str(DECREE_K)
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f'LAWBOT_DECREE_K는 1 이상의 정수: {raw!r}')
    value = os.environ.get('LAWBOT_DECREE_MODELS')
    keys = tuple(k.strip() for k in (value if value is not None else ','.join(DECREE_MODELS)).split(',') if k.strip())
    known = [m.key for m in profile.models + profile.disabled]
    bad = [k for k in keys if k not in known]
    if bad or len(set(keys)) != len(keys):
        raise ValueError(f"LAWBOT_DECREE_MODELS는 {', '.join(known)} 중에서 쉼표로: {value!r}")
    return DecreeConfig(tau, int(raw), keys)

def early_stop():
    """간결형에서 【설명】이 시작되면 생성을 멈출지. val 실험(eval_concise)으로 확인하기 전에는 끈다"""
    return os.environ.get('LAWBOT_EARLY_STOP', '0') == '1'
