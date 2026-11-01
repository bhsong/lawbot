# src/lawbot/decree.py  →  시행령·시행규칙(하위법령) 첨부 필터 (웹앱·모델 서버·평가 공통, 모델 라이브러리 import 없음)
# 규칙은 val 실험에서 고른 same1+other0.9와 같다 (tests/test_decree.py가 저장된 점수·참고 조문으로 대조)
#   1. 하위법령 후보(임베딩 상위 10개)를 리랭커 점수 내림차순으로. 동점은 임베딩 순위가 앞선 것 먼저 (reranker.rank_order)
#   2. parent_law가 법률 리랭커 1위 조문의 법과 같으면 통과, 다른 법이면 점수 ≥ τ일 때만 통과
#   3. 통과한 것 중 앞에서 k개. 하나도 없으면 첨부하지 않는다 (프롬프트는 법률 3개만일 때와 글자 단위로 같음)
LAW_KIND = '법률'


def rank_order(scores):
    """점수 내림차순 순서. 같은 점수는 입력(임베딩) 순서 유지 (reranker.rank_order가 이 함수를 씀)"""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def is_decree(h):
    """검색 결과 dict가 하위법령인지 (법률 hit에는 kind가 없다: 하위법령을 끈 웹앱과 같은 dict)"""
    return h.get('kind', LAW_KIND) != LAW_KIND


def attach(candidates, top1_law, tau, k):
    """리랭킹된 하위법령 후보 dict(rerank_score·emb_rank·parent_law) → 첨부할 dict 목록 (최대 k개, 점수순)
    top1_law: 법률 리랭커 1위 조문의 법령명 (법률 hit의 law)"""
    order = sorted(candidates, key=lambda c: (-c['rerank_score'], c['emb_rank']))
    return [c for c in order if c['parent_law'] == top1_law or c['rerank_score'] >= tau][:k]
