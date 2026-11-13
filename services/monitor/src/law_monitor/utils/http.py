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

    def get(
        self, url: str, params: dict | None = None, *, validate_url: Callable[[httpx.URL], None] | None = None
    ) -> httpx.Response:
        return self.request('GET', url, params=params, validate_url=validate_url)

    def post(self, url: str, *, json: dict, headers: dict | None = None) -> httpx.Response:
        """헤더(토큰)는 오류 메시지에 넣지 않는다."""
        return self.request('POST', url, json=json, headers=headers)

    def _send_checked(self, request: httpx.Request, validate_url: Callable[[httpx.URL], None]) -> httpx.Response:
        """Check the actual URL before each send, including redirect targets."""
        for _ in range(self.client.max_redirects + 1):
            validate_url(request.url)
            self._wait_turn()
            response = self.client.send(request, follow_redirects=False)
            if response.next_request is None:
                return response
            request = response.next_request
        raise HttpError('리다이렉트 한도 초과')

    def request(
        self,
        method: str,
        url: str,
        *,
        params=None,
        json=None,
        headers=None,
        validate_url: Callable[[httpx.URL], None] | None = None,
    ) -> httpx.Response:
        shown = masked_url(url, params)

        def once() -> httpx.Response:
            try:
                if validate_url is None:
                    self._wait_turn()
                    resp = self.client.request(method, url, params=params, json=json, headers=headers)
                else:
                    request = self.client.build_request(method, url, params=params, json=json, headers=headers)
                    resp = self._send_checked(request, validate_url)
            except httpx.InvalidURL:
                raise HttpError('잘못된 요청 URL') from None
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
