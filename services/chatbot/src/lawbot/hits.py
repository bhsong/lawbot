# lawbot/hits.py  →  웹앱 안에서 주고받는 dict의 모양 (TypedDict: 실행 중에는 그냥 dict, 타입 검사만)
# 검색 결과(Hit)는 피드백 JSON·평가 CSV에 그대로 저장되므로 dict로 둔다 (dataclass 전환은 다음에 따로 시도)
from typing import NotRequired, TypedDict


class Hit(TypedDict):
    """검색 결과 조문 하나 (retriever.search → reranker.rerank → decree.attach → llm.build_prompt·화면)"""

    law: str  # '근로기준법', 하위법령은 '근로기준법 시행령'
    article_id: str  # '제26조'
    title: str
    doc: str  # 검색 문서 문자열 '근로기준법 제26조(해고의 예고)\n본문'
    score: float  # 임베딩 코사인 유사도
    rerank_score: NotRequired[float]  # 리랭커 P(yes) (리랭커를 켰을 때)
    emb_rank: NotRequired[int]  # 리랭킹 전 임베딩 순위 (1부터)
    kind: NotRequired[str]  # 하위법령만: '시행령'·'시행규칙'
    parent_law: NotRequired[str]  # 하위법령만: '근로기준법'


class Timing(TypedDict, total=False):
    """service.ask의 측정값 st (초·토큰·판정). 요청 로그·화면 측정 줄·피드백 기록이 읽는다"""

    model: str
    request_id: str
    load: float | None
    search: float
    rerank: float
    decree_search: float
    decree_rerank: float
    decree_candidates: list[Hit]
    n_decree: int
    ttft: float
    first_text: float
    total: float
    tps: float | None
    prompt_tokens: int | None
    prompt_tps: float | None
    gen_tokens: int | None
    peak_gb: float | None
    finish: str | None
    refused: bool
    n_cited: int
    _stage: str  # 오류가 난 단계 판정용 (내부)
