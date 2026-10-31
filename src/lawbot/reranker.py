# src/lawbot/reranker.py  →  임베딩 상위 후보를 Qwen3-Reranker-4B(튜닝 없음, MLX 4비트)로 다시 정렬
# 채점은 v4 리랭커 실험 노트북(lawbot_v4_reranker.ipynb, Colab L4 · transformers fp16)과 같게 한다:
#   PREFIX + 토큰화('Instruct: …\nQuery: …\nDocument: …')를 MAX_LENGTH에 맞게 뒤에서 자름 + SUFFIX
#   → 마지막 위치의 no·yes 로짓을 float32 softmax → P(yes). 점수 내림차순, 동점은 임베딩 순위
# 노트북은 4B를 batch 1로 돌렸으므로(패딩 없음) 여기서도 후보마다 한 번씩 계산한다
from pathlib import Path
import mlx.core as mx
from mlx_lm import load
from lawbot.decree import rank_order   # 점수 내림차순, 동점은 임베딩 순서 (하위법령 필터와 같은 함수)

# 아래 세 문자열과 MAX_LENGTH는 노트북 셀 3을 글자 그대로 옮긴 것 (바꾸면 실험 결과와 비교할 수 없음)
TASK = 'Given a Korean labor-law question, retrieve the statutory article that directly provides the legal basis for answering it.'
MAX_LENGTH = 2560
PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
CANDIDATES = 10

def pair_text(question, doc):
    return f'Instruct: {TASK}\nQuery: {question}\nDocument: {doc}'

def yes_probability(no_yes):
    """노트북과 같이 float32 softmax([no, yes])[1]. 차이가 크면 1.0으로 포화해 동점이 생길 수 있다"""
    return mx.softmax(no_yes.astype(mx.float32))[1].item()


class Reranker:
    def __init__(self, path, candidates=CANDIDATES, max_length=MAX_LENGTH):
        path = Path(path)
        if not path.is_dir():            # mlx_lm.load는 없는 경로를 Hugging Face 저장소 이름으로 보고 내려받으려 함
            raise FileNotFoundError(f'리랭커 모델 폴더 없음: {path}')
        self.path, self.candidates, self.max_length = path, candidates, max_length
        self.model, self.tok = load(str(path))
        if getattr(self.model, 'model_type', None) != 'qwen3':
            raise ValueError(f'Qwen3 모델이 아닙니다: {path} ({getattr(self.model, "model_type", None)})')
        self.prefix = self.tok.encode(PREFIX, add_special_tokens=False)
        self.suffix = self.tok.encode(SUFFIX, add_special_tokens=False)
        self.max_mid = max_length - len(self.prefix) - len(self.suffix)
        yes, no = self.tok.convert_tokens_to_ids('yes'), self.tok.convert_tokens_to_ids('no')
        if not (isinstance(yes, int) and isinstance(no, int) and yes != no):
            raise ValueError(f'yes/no 토큰을 찾을 수 없습니다: yes={yes!r}, no={no!r}')
        self.no_yes = mx.array([no, yes])

    def encode(self, question, doc):
        """노트북 encode_pairs와 같은 토큰 열 (가운데 부분만 오른쪽에서 잘라 문서 뒷부분이 잘림)"""
        mid = self.tok.encode(pair_text(question, doc), add_special_tokens=False)
        return self.prefix + mid[:self.max_mid] + self.suffix

    def _no_yes_logits(self, ids):
        """마지막 위치의 [no, yes] 로짓. Model.__call__은 모든 위치에 lm_head를 곱하므로(2560 × 15만 어휘)
        같은 계산을 마지막 위치에만 한다 (mlx_lm qwen3.Model.__call__과 같은 분기)"""
        m = self.model
        h = m.model(mx.array([ids]))[:, -1:, :]
        logits = m.model.embed_tokens.as_linear(h) if m.args.tie_word_embeddings else m.lm_head(h)
        return logits[0, -1][self.no_yes]

    def score(self, question, docs):
        """조문마다 P(yes). NaN·무한대는 정렬 결과를 믿을 수 없으므로 오류"""
        scores = [yes_probability(self._no_yes_logits(self.encode(question, d))) for d in docs]
        bad = [i for i, s in enumerate(scores) if not 0.0 <= s <= 1.0]
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
        return [candidates[i] | {'rerank_score': scores[i], 'emb_rank': i + 1}
                for i in rank_order(scores)[:top_k]]
