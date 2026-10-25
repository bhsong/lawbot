# src/lawbot/deploy.py  →  웹앱 배포 설정: 검색 프로필(v1/v4)과 답변 모델(Fast/Pro) 레지스트리
# 모델 라이브러리를 import하지 않는다 (check_deploy·테스트가 모델 없이 읽음)
import os
from dataclasses import dataclass
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

def _path(env, default):
    """환경 변수가 있으면 그 경로. 상대 경로는 레포 최상위 기준 절대 경로로 바꾼다
    (mlx_lm.load는 없는 상대 경로를 Hugging Face 저장소 이름으로 보고 내려받으려 함)"""
    p = Path(os.environ.get(env) or default).expanduser()
    return p if p.is_absolute() else paths.ROOT / p

def get_profile(name=None):
    name = name or os.environ.get('LAWBOT_PROFILE', 'v4')     # 기본 v4. v4 자산이 없으면 LAWBOT_PROFILE=v1
    # Fast(4B LoRA)는 학습한 4섹션 그대로, Pro(14B)는 간결형 system으로 처음부터 짧게 생성 (14B QLoRA도 간결형으로 학습)
    if name == 'v1':         # 이전 동작: 근로기준법 130조문, v1 검색·4B. Pro는 튜닝 전 14B로 속도·메모리 시험
        return Profile('v1', '근로기준법 챗봇', paths.ARTICLES, _path('LAWBOT_EMB', paths.EMB), 512, 'v1', None, (
            ModelSpec('fast', 'Lawbot Fast', '4B · 빠른 응답, 4항목 답변', _path('LAWBOT_FAST_PATH', paths.LLM_4BIT), 'full'),
            ModelSpec('pro', 'Lawbot Pro', '14B · 큰 모델, 간결한 답변, 응답이 더 느림',
                      _path('LAWBOT_PRO_PATH', paths.PRO_BASE), 'concise',
                      _path('LAWBOT_PRO_SYSTEM', paths.SYSTEM_V1_CONCISE))))
    if name == 'v4':         # 7개 법률 415조문, v4 검색 (MAX_SEQ 2304). 경로는 paths.py의 미확인 자리표시
        return Profile('v4', '노동법 챗봇', _path('LAWBOT_V4_ARTICLES', paths.V4_ARTICLES),
                       _path('LAWBOT_V4_EMB', paths.V4_EMB), 2304, 'v4', _path('LAWBOT_V4_SYSTEM', paths.V4_SYSTEM), (
            ModelSpec('fast', 'Lawbot Fast', '4B · 빠른 응답, 4항목 답변', _path('LAWBOT_FAST_PATH', paths.V4_FAST), 'full'),
            ModelSpec('pro', 'Lawbot Pro', '14B · 큰 모델, 간결한 답변, 응답이 더 느림',
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

def early_stop():
    """간결형에서 【설명】이 시작되면 생성을 멈출지. val 실험(eval_concise)으로 확인하기 전에는 끈다"""
    return os.environ.get('LAWBOT_EARLY_STOP', '0') == '1'
