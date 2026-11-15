"""Load an explicit calibration for the read-only semantic shadow path."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lawbot.db.connection import repo_path
from lawbot.exact_cache import canonical, digest
from lawbot_common.api import MAX_DOCUMENTS, MAX_SEQ_LIMIT

QUERY_PROMPT_RESERVE = 512
# Qwen3 uses byte-level BPE. Leave room for the server's prompt wrappers and special tokens.
RERANK_INPUT_BYTES = 2304


class Calibration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, str_min_length=1, str_strip_whitespace=True)

    calibration_id: str
    embedding_model: str
    embedding_revision: str
    reranker_model: str
    reranker_revision: str
    index_revision: str
    query_max_seq: int = Field(gt=QUERY_PROMPT_RESERVE, le=MAX_SEQ_LIMIT)
    top_k: int = Field(ge=1, le=MAX_DOCUMENTS)
    parent_instruction: str
    question_instruction: str
    parent_threshold: float = Field(ge=0, le=1)
    question_threshold: float = Field(ge=0, le=1)
    min_answer_margin: float = Field(ge=0, le=1)

    @property
    def embedding_fingerprint(self) -> str:
        return digest(
            canonical(
                [
                    self.embedding_model,
                    self.embedding_revision,
                    'query',
                    self.query_max_seq,
                    'mps-fp16-f32-l2',
                    QUERY_PROMPT_RESERVE,
                ]
            )
        )

    @property
    def reranker_fingerprint(self) -> str:
        return digest(
            canonical(
                [
                    self.reranker_model,
                    self.reranker_revision,
                    'pointwise-f32-softmax-yes',
                    RERANK_INPUT_BYTES,
                    self.parent_instruction,
                    self.question_instruction,
                ]
            )
        )


@dataclass(frozen=True)
class ShadowSettings:
    calibration: Calibration
    index_path: Path | None


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
    return ShadowSettings(calibration, repo_path(index) if index else None)
