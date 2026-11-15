"""Load an explicit calibration for the read-only semantic shadow path."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tokenizers import Tokenizer

from lawbot.db.connection import repo_path
from lawbot.exact_cache import canonical, digest
from lawbot_common.api import MAX_DOCUMENTS, MAX_SEQ_LIMIT

QUERY_PROMPT_RESERVE = 512
RERANK_MAX_LENGTH = 2560
# Keep the three separately tokenized sections identical to the Qwen3 reranker contract.
RERANK_PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
RERANK_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


@dataclass(frozen=True)
class RerankTokens:
    tokenizer: Tokenizer
    max_middle: int

    @classmethod
    def load(cls, path: Path, expected_hash: str):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError('LAWBOT_SEMANTIC_RERANK_TOKENIZER를 읽을 수 없습니다.') from exc
        if sha256(raw).hexdigest() != expected_hash:
            raise ValueError('리랭커 tokenizer SHA-256이 calibration과 일치해야 합니다.')
        try:
            tokenizer = Tokenizer.from_str(raw.decode('utf-8'))
        except Exception as exc:  # The Rust decoder exposes invalid tokenizer JSON as a base Exception.
            raise ValueError('리랭커 tokenizer.json을 해석할 수 없습니다.') from exc
        tokenizer.no_truncation()
        tokenizer.no_padding()
        wrappers = tokenizer.encode_batch([RERANK_PREFIX, RERANK_SUFFIX], add_special_tokens=False)
        return cls(tokenizer, RERANK_MAX_LENGTH - sum(len(part.ids) for part in wrappers))

    def fits(self, instruction: str, question: str, document: str) -> bool:
        middle = f'Instruct: {instruction}\nQuery: {question}\nDocument: {document}'
        return len(self.tokenizer.encode(middle, add_special_tokens=False).ids) <= self.max_middle


def embedding_fingerprint(model: str, revision: str, max_seq: int) -> str:
    return digest(canonical([model, revision, 'query', max_seq, 'mps-fp16-f32-l2', QUERY_PROMPT_RESERVE]))


class Calibration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, str_min_length=1, str_strip_whitespace=True)

    calibration_id: str
    embedding_model: str
    embedding_revision: str
    reranker_model: str
    reranker_revision: str
    reranker_tokenizer_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    index_revision: str
    query_max_seq: int = Field(gt=QUERY_PROMPT_RESERVE, le=MAX_SEQ_LIMIT)
    top_k: int = Field(ge=1, le=MAX_DOCUMENTS)
    parent_instruction: str
    question_instruction: str
    parent_threshold: float = Field(ge=0, le=1)
    question_threshold: float = Field(ge=0, le=1)
    min_answer_margin: float = Field(ge=0, le=1)
    shadow_timeout_s: float = Field(default=2.0, gt=0, le=30)

    def matches_revisions(self, available) -> bool:
        revisions = {(row.get('type'), row['id']): row.get('revision') for row in available}
        return (
            revisions.get(('embedding', self.embedding_model)) == self.embedding_revision
            and revisions.get(('rerank', self.reranker_model)) == self.reranker_revision
        )

    @property
    def embedding_fingerprint(self) -> str:
        return embedding_fingerprint(self.embedding_model, self.embedding_revision, self.query_max_seq)

    @property
    def reranker_fingerprint(self) -> str:
        return digest(
            canonical(
                [
                    self.reranker_model,
                    self.reranker_revision,
                    'pointwise-f32-softmax-yes',
                    self.reranker_tokenizer_sha256,
                    RERANK_MAX_LENGTH,
                    RERANK_PREFIX,
                    RERANK_SUFFIX,
                    self.parent_instruction,
                    self.question_instruction,
                ]
            )
        )


@dataclass(frozen=True)
class ShadowSettings:
    calibration: Calibration
    index_path: Path | None
    rerank_tokens: RerankTokens


def load_shadow(env) -> ShadowSettings | None:
    mode = env.get('LAWBOT_SEMANTIC_CACHE_MODE', 'off').strip()
    if mode == 'off':
        return None
    if mode != 'shadow':
        raise ValueError('LAWBOT_SEMANTIC_CACHE_MODE는 off 또는 shadow여야 합니다.')
    path = env.get('LAWBOT_SEMANTIC_CALIBRATION', '').strip()
    if not path:
        raise ValueError('shadow 모드에는 LAWBOT_SEMANTIC_CALIBRATION 파일이 필요합니다.')
    try:
        calibration = Calibration.model_validate_json(repo_path(path).read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise ValueError('LAWBOT_SEMANTIC_CALIBRATION 파일을 읽을 수 없거나 검증에 실패했습니다.') from exc
    if calibration.parent_instruction == calibration.question_instruction:
        raise ValueError('LAWBOT_SEMANTIC_CALIBRATION의 두 리랭크 instruction은 달라야 합니다.')
    index = env.get('LAWBOT_SEMANTIC_INDEX', '').strip()
    tokenizer = env.get('LAWBOT_SEMANTIC_RERANK_TOKENIZER', '').strip()
    if not tokenizer:
        raise ValueError('shadow 모드에는 LAWBOT_SEMANTIC_RERANK_TOKENIZER 파일이 필요합니다.')
    tokens = RerankTokens.load(repo_path(tokenizer), calibration.reranker_tokenizer_sha256)
    return ShadowSettings(calibration, repo_path(index) if index else None, tokens)
