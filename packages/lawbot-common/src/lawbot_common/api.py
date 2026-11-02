# lawbot_common/api.py  →  웹앱 ↔ 모델 서버 HTTP 계약 (OpenAI 호환 + 확장 필드). 두 쪽이 이 모듈 하나를 같이 쓴다
#   요청 모델: 모델 서버가 검증(모르는 필드는 400), 웹앱이 본문을 만든다
#   응답 모델: 모델 서버가 응답을 만들고, 웹앱이 검증한다
#   오류 type: 응답 error.type 값 (웹앱이 안내 문구·요청 로그 유형을 고르는 기준)
# 경로·필드 이름·값은 OpenAI·Cohere 형식 그대로다 (docs/operations.md "모델 서버 (OpenAI 호환 API)")
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- 경로 ----
CHAT_PATH, EMBEDDINGS_PATH, RERANK_PATH = '/v1/chat/completions', '/v1/embeddings', '/v1/rerank'
MODELS_PATH, HEALTH_PATH = '/v1/models', '/health'

# ---- 오류 type (error.type) ----
INVALID_REQUEST = 'invalid_request_error'  # 400·404·413
AUTHENTICATION = 'authentication_error'  # 401 토큰 불일치
MODEL_LOAD = 'model_load_error'  # 500 chat 모델 폴더 없음·형식 오류 (code는 원래 예외 이름)
SERVER_NOT_READY = 'server_not_ready'  # 503 시작할 때 모델을 불러오는 중
SERVER_BUSY = 'server_busy'  # 503 생성 대기열이 가득 참 (LAWBOT_MAX_QUEUED)
SERVER_ERROR = 'server_error'  # 500 그 밖의 모델 연산 오류 (code는 예외 이름)
MODEL_NOT_FOUND = 'model_not_found'  # 404의 code

# ---- 요청 한도 ----
MAX_TOKENS_LIMIT = 32768
MAX_INPUTS = 4096  # embeddings input 개수
MAX_DOCUMENTS = 1000  # rerank documents 개수
MAX_SEQ_LIMIT = 32768


# ---- 요청 (모르는 필드는 400: 조용히 무시하지 않음) ----
class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class TextPart(Strict):
    type: Literal['text']
    text: str


class Message(Strict):
    role: Literal['system', 'user', 'assistant']
    content: str | list[TextPart]
    name: str | None = None


class StreamOptions(Strict):
    include_usage: bool = False


class TemplateKwargs(Strict):
    enable_thinking: bool = False


class ChatRequest(Strict):
    """웹앱은 openai SDK로 보낸다 (이 모델은 서버 검증용)"""

    model: str
    messages: list[Message] = Field(min_length=1)
    max_tokens: int | None = Field(None, ge=1, le=MAX_TOKENS_LIMIT)
    max_completion_tokens: int | None = Field(None, ge=1, le=MAX_TOKENS_LIMIT)
    temperature: float | None = Field(None, ge=0, le=2)  # 없으면 0 (탐욕적 생성). OpenAI 기본값(1)과 다름
    top_p: float | None = Field(None, gt=0, le=1)
    stream: bool = False
    stream_options: StreamOptions | None = None
    n: int | None = Field(None, ge=1, le=1)
    chat_template_kwargs: TemplateKwargs | None = None  # 기본 생각 끔
    user: str | None = None  # 받기만 하고 쓰지 않음


class EmbeddingRequest(Strict):
    model: str
    input: str | list[str] = Field(min_length=1, max_length=MAX_INPUTS)
    encoding_format: Literal['float', 'base64'] = 'float'  # base64: float32 little-endian 그대로
    input_type: Literal['query', 'document'] = 'document'  # 확장: query면 질문 프롬프트
    max_seq_length: int | None = Field(None, ge=1, le=MAX_SEQ_LIMIT)  # 확장
    user: str | None = None


class RerankRequest(Strict):
    model: str
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    top_n: int | None = Field(None, ge=1)
    instruction: str | None = None  # 확장: 작업 설명
    return_documents: bool = False


# ---- 응답 ----
class Response(BaseModel):
    model_config = ConfigDict(extra='allow')  # 받는 쪽은 모르는 필드를 허용 (OpenAI 응답과 같이)


class EmbeddingItem(Response):
    object: Literal['embedding'] = 'embedding'
    index: int
    embedding: str | list[float]  # base64 문자열 또는 float 목록


class Usage(Response):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingResponse(Response):
    object: Literal['list'] = 'list'
    model: str | None = None
    data: list[EmbeddingItem]
    usage: Usage = Usage()


class RerankDocument(Response):
    text: str


class RerankResult(Response):
    index: int
    relevance_score: float
    document: RerankDocument | None = None  # return_documents일 때만


class RerankResponse(Response):
    id: str | None = None
    model: str | None = None
    results: list[RerankResult]


class ModelCard(Response):
    id: str
    object: Literal['model'] = 'model'
    created: int = 0
    owned_by: str = 'lawbot'
    type: Literal['chat', 'embedding', 'rerank']  # 확장
    loaded: bool  # 확장: 지금 올라 있는지


class ModelsResponse(Response):
    object: Literal['list'] = 'list'
    data: list[ModelCard]


class HealthResponse(Response):
    ready: bool


class ErrorDetail(Response):
    message: str
    type: str | None = None
    param: str | None = None
    code: str | None = None


class ErrorResponse(Response):
    error: ErrorDetail


def error_body(message, kind, code=None):
    """서버 오류 응답 본문 (OpenAI 형식)"""
    return ErrorResponse(error=ErrorDetail(message=message, type=kind, code=code)).model_dump()
