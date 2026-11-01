"""법제처 Open API 클라이언트 (근거: docs/evals/2026-11-01-monitor-spike.md).

- 법령 본문: DRF/lawService.do?target=law&MST=<법령MST>&type=JSON → 개정문·제개정이유·기본정보·별표단위(별표내용 텍스트)
- 행정규칙 상세: DRF/lawService.do?target=admrul&ID=<행정규칙일련번호>&type=JSON → 같은 구조 + 첨부파일
- 첨부 파일: LSW/flDownload.do?flSeq=… (OC 없이 받음)
한 실행 안에서 같은 MST·일련번호는 한 번만 받는다 (본문이 수 MB).
"""

from law_monitor.utils.http import HttpClient, HttpError

BASE = 'https://www.law.go.kr'
SERVICE = f'{BASE}/DRF/lawService.do'
MAGIC = {'pdf': (b'%PDF',), 'hwp': (b'\xd0\xcf\x11\xe0', b'PK\x03\x04')}  # HWP 5(OLE)·HWPX(zip)


class LawGoError(RuntimeError):
    pass


class LawGoClient:
    def __init__(self, oc: str, http: HttpClient | None = None):
        self.oc = oc
        self.http = http or HttpClient()
        self._cache: dict[tuple[str, str], dict] = {}

    def law_body(self, mst: str) -> dict:
        """법령 본문 응답 전체 ({'법령': {...}})."""
        return self._service('law', 'MST', str(mst), required='기본정보')

    def admrule_detail(self, serial: str) -> dict:
        """행정규칙 상세 응답 전체 ({'AdmRulService': {...}})."""
        return self._service('admrul', 'ID', str(serial), required='행정규칙기본정보')

    def _service(self, target: str, key: str, value: str, *, required: str) -> dict:
        cache_key = (target, value)
        if cache_key not in self._cache:
            params = {'OC': self.oc, 'target': target, key: value, 'type': 'JSON'}
            try:
                resp = self.http.get(SERVICE, params)
                data = resp.json()
            except HttpError as e:
                raise LawGoError(f'법제처 {target} {key}={value} 조회 실패: {e}') from None
            except ValueError:
                raise LawGoError(f'법제처 {target} {key}={value}: JSON이 아닌 응답') from None
            root = data.get(next(iter(data))) if isinstance(data, dict) and len(data) == 1 else None
            if not isinstance(root, dict) or required not in root:
                raise LawGoError(f'법제처 {target} {key}={value}: 알 수 없는 응답 ({str(data)[:120]})')
            self._cache[cache_key] = data
        return self._cache[cache_key]

    def download(self, link: str, kind: str) -> bytes:
        """kind: 'pdf' | 'hwp'. 내용이 그 형식이 아니면(오류 HTML 등) 실패."""
        url = link if link.startswith('http') else f'{BASE}{link}'
        try:
            data = self.http.get(url).content
        except HttpError as e:
            raise LawGoError(f'첨부 파일 받기 실패: {e}') from None
        if not data.startswith(MAGIC[kind]):
            raise LawGoError(f'받은 파일이 {kind.upper()}가 아닙니다 ({url}, 앞부분 {data[:16]!r})')
        return data


def root(body: dict | None) -> dict:
    return body[next(iter(body))] if body else {}


def joined(value) -> str | None:
    """개정문내용·제개정이유내용처럼 중첩된 줄 목록을 한 문자열로."""
    lines = list(flat(value))
    return '\n'.join(lines).strip() or None


def flat(value):
    if value is None:
        return
    if isinstance(value, list):
        for v in value:
            yield from flat(v)
    else:
        yield str(value)
