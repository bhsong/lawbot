# lawbot/reranker.py  →  임베딩 상위 후보를 모델 서버의 리랭커(Qwen3-Reranker-4B, /v1/rerank)로 다시 정렬
# 채점(프롬프트·토큰 자르기·P(yes))은 모델 서버가 실험 노트북과 같게 한다. 여기서는 작업 설명(TASK)을 보내고
# 점수 내림차순으로 정렬한다 (동점은 임베딩 순위: 하위법령 필터와 같은 rank_order)
from lawbot.decree import rank_order

# lawbot_v4_reranker.ipynb 셀 3의 작업 설명을 글자 그대로 (바꾸면 실험 결과와 비교할 수 없음)
TASK = 'Given a Korean labor-law question, retrieve the statutory article that directly provides the legal basis for answering it.'
CANDIDATES = 10


class Reranker:
    def __init__(self, client, model, candidates=CANDIDATES):
        self.client, self.model, self.candidates = client, model, candidates

    def score(self, question, docs):
        """문서마다 P(yes). 0~1 밖(NaN 등)이면 정렬 결과를 믿을 수 없으므로 오류 (서버도 검사함)"""
        scores = self.client.rerank(self.model, question, docs, TASK)
        bad = [i for i, s in enumerate(scores) if not (isinstance(s, (int, float)) and 0.0 <= s <= 1.0)]
        if bad:
            raise RuntimeError(f'리랭커 점수가 올바르지 않습니다 (후보 {bad}): {[scores[i] for i in bad]}')
        return scores

    def rerank(self, question: str, candidates: list[dict], top_k: int = 3) -> list[dict]:
        """Retriever.search 결과(dict 목록, 임베딩 순) → 리랭킹 상위 top_k개
        각 조문 dict는 복사해 그대로 두고 rerank_score(P(yes))·emb_rank(임베딩 순위, 1부터)만 더한다"""
        if not isinstance(question, str) or not question.strip():
            raise ValueError('질문이 비어 있습니다')
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError(f'top_k는 1 이상의 정수: {top_k!r}')
        if not candidates or not all(isinstance(c, dict) and isinstance(c.get('doc'), str) for c in candidates):
            raise ValueError('후보는 doc 문자열을 가진 검색 결과 dict 목록이어야 합니다')
        scores = self.score(question, [c['doc'] for c in candidates])
        return [candidates[i] | {'rerank_score': scores[i], 'emb_rank': i + 1} for i in rank_order(scores)[:top_k]]
