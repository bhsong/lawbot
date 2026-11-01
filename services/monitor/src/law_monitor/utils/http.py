"""공통 HTTP 클라이언트 (원 계획서 5.6): 타임아웃 30초, 최대 3회 재시도(지수 백오프), 요청 사이 0.5초 이상.

오류 메시지에는 인증키를 넣지 않는다: URL의 MASK_PARAMS 값은 ***로 바꿔 적는다.
"""

import time
from collections.abc import Callable

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

MASK_PARAMS = ('OC', 'serviceKey', 'ServiceKey')
USER_AGENT = 'law-monitor/0.1 (+https://github.com/bhsong/lawbot)'


class HttpError(RuntimeError):
    pass


class _Retryable(HttpError):
    pass


def masked_url(url: str, params: dict | None = None) -> str:
    safe = {k: ('***' if k in MASK_PARAMS else v) for k, v in (params or {}).items()}
    full = httpx.URL(url, params=safe) if safe else httpx.URL(url)
    for key in MASK_PARAMS:
        if key in full.params:
            full = full.copy_set_param(key, '***')
    return str(full).replace('%2A%2A%2A', '***')  # 인코딩된 *** 를 읽기 쉽게


class HttpClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        min_interval: float = 0.5,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=True, headers={'User-Agent': USER_AGENT}
        )
        self.retries, self.min_interval, self.backoff = retries, min_interval, backoff
        self.sleep, self.clock = sleep, clock
        self._last = None

    def _wait_turn(self) -> None:
        if self._last is not None:
            gap = self.min_interval - (self.clock() - self._last)
            if gap > 0:
                self.sleep(gap)
        self._last = self.clock()

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        shown = masked_url(url, params)

        def once() -> httpx.Response:
            self._wait_turn()
            try:
                resp = self.client.get(url, params=params)
            except httpx.TransportError as e:
                raise _Retryable(f'{shown}: {type(e).__name__}') from None
            if resp.status_code >= 500:
                raise _Retryable(f'{shown}: HTTP {resp.status_code}')
            if resp.status_code >= 400:
                raise HttpError(f'{shown}: HTTP {resp.status_code}')
            return resp

        retrying = Retrying(
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=self.backoff, max=10),
            retry=retry_if_exception_type(_Retryable),
            sleep=self.sleep,
            reraise=True,
        )
        return retrying(once)
