"""요약 모델 클라이언트: 운영 모델 서버(OpenAI 호환 /v1/chat/completions)를 httpx로 부른다.

주소·토큰은 웹앱과 같은 LAWBOT_MODEL_SERVER·LAWBOT_MODEL_TOKEN (환경변수 → services/monitor/.env → 레포 .env).
모델 id LAW_MONITOR_SUMMARY_MODEL은 환경변수·services/monitor/.env에서만 (레포 .env는 LAWBOT_*만 허용).
토큰은 Authorization 헤더에만 넣고 오류·로그에는 남기지 않는다.
"""

from dataclasses import dataclass

from law_monitor import secrets
from law_monitor.utils.http import HttpClient, HttpError

DEFAULT_MODEL = 'qwen3.8-27b-4bit'


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    url: str
    token: str
    model: str


@dataclass(frozen=True)
class Completion:
    text: str
    usage: dict
    timings: dict
    finish_reason: str | None


def load_config() -> LLMConfig | None:
    url = secrets.get('LAWBOT_MODEL_SERVER', root=True)
    token = secrets.get('LAWBOT_MODEL_TOKEN', root=True)
    if not url or not token:
        return None
    return LLMConfig(url.rstrip('/'), token, secrets.get('LAW_MONITOR_SUMMARY_MODEL') or DEFAULT_MODEL)


class ChatClient:
    def __init__(self, config: LLMConfig, http: HttpClient | None = None):
        self.config = config
        # 27B가 긴 프롬프트를 처리하는 시간 + 챗봇 요청 뒤에 줄 서는 시간
        self.http = http or HttpClient(timeout=300, retries=3, min_interval=0, backoff=5)  # 시도 3번 = 재시도 2회

    @property
    def model(self) -> str:
        return self.config.model

    def complete(self, system: str, user: str, *, max_tokens: int = 800) -> Completion:
        body = {
            'model': self.config.model,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'temperature': 0,
            'max_tokens': max_tokens,
            'stream': False,
            'chat_template_kwargs': {'enable_thinking': False},
        }
        try:
            resp = self.http.post(
                f'{self.config.url}/v1/chat/completions',
                json=body,
                headers={'Authorization': f'Bearer {self.config.token}'},
            )
            data = resp.json()
            choice = data['choices'][0]
            text = (choice.get('message') or {}).get('content') or ''
        except HttpError as e:
            raise LLMError(f'모델 서버 요청 실패: {e}') from None
        except ValueError, KeyError, IndexError, TypeError:
            raise LLMError('모델 서버 응답 형식이 아닙니다') from None
        if not text.strip():
            raise LLMError('모델이 빈 요약을 돌려줬습니다')
        return Completion(text.strip(), data.get('usage') or {}, data.get('timings') or {}, choice.get('finish_reason'))
