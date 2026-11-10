# tests/conftest.py  →  챗봇 테스트 공통 설정
# 실제 모델 서버·네트워크·비밀번호 없이 돈다: load_app이 쓰는 모델 서버 클라이언트는 기본으로 가짜 (fakes.FakeServerClient)
# 실제 모델 테스트는 모델 서버 쪽 (services/model-server/tests, -m models)
import socket

import pytest

ENV_KEYS = (
    'LAWBOT_USER',
    'LAWBOT_PW',
    'LAWBOT_PROFILE',
    'LAWBOT_EMB_MODEL',
    'LAWBOT_FAST_MODEL',
    'LAWBOT_PRO_MODEL',
    'LAWBOT_RERANK_MODEL',
    'LAWBOT_LLM_BASE_URL',
    'LAWBOT_LLM_API_KEY',
    'LAWBOT_LLM_ALLOW_EXTERNAL',
    'LAWBOT_PRO_SYSTEM',
    'LAWBOT_V4_ARTICLES',
    'LAWBOT_V4_SYSTEM',
    'LAWBOT_RERANKER_ENABLED',
    'LAWBOT_RERANKER_CANDIDATES',
    'LAWBOT_DECREE_ENABLED',
    'LAWBOT_DECREE_TAU',
    'LAWBOT_DECREE_K',
    'LAWBOT_DECREE_MODELS',
    'LAWBOT_V4_DECREE_ARTICLES',
    'LAWBOT_V5_ARTICLES',
    'LAWBOT_V5_DECREE_ARTICLES',
    'LAWBOT_MODELS',
    'LAWBOT_ENV_FILE',
    'LAWBOT_PORT',
    'LAWBOT_CHAT_CONCURRENCY',
    'LAWBOT_INSTANCE',
    'LAWBOT_MODEL_SERVER',
    'LAWBOT_MODEL_SERVER_PORT',
    'LAWBOT_MODEL_TOKEN',
    'LAWBOT_MODEL_SERVER_WAIT',
    'LAWBOT_FEEDBACK',
    'LAWBOT_FEEDBACK_DIR',
    'LAWBOT_DB',
    'LAWBOT_CORPUS',
    'LAWBOT_EXACT_CACHE_DB',
    'LAWBOT_EXACT_CACHE_BUNDLE',
    'LAWBOT_ADMIN_USER',
    'LAWBOT_ADMIN_PW_HASH',
    'LAWBOT_CALENDAR_API_URL',
    'LAWBOT_CALENDAR_ADMIN_TOKEN',
)


LOCAL_MARKERS = ('integration', 'e2e_stub', 'e2e_live')  # 127.0.0.1의 테스트 서버에 접속하는 테스트


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """item.rep_setup·rep_call·rep_teardown: e2e 픽스처가 실패한 테스트만 진단 산출물을 남기는 데 씀"""
    rep = yield
    setattr(item, f'rep_{rep.when}', rep)
    return rep


@pytest.fixture(autouse=True)
def env(request, monkeypatch):
    """개발자 셸의 인증 환경 변수를 지우고, 외부 네트워크 접속을 막는다
    (integration·e2e 테스트는 127.0.0.1의 테스트 서버 접속만 허용)"""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    from lawbot.api_client import REQUEST_ID

    REQUEST_ID.set(None)  # 앞 테스트의 질문 요청 id가 남지 않게
    monkeypatch.setenv('LAWBOT_EMBED_CACHE', '0')  # 레포의 cache/에 쓰지 않음 (캐시 테스트는 직접 지정)
    monkeypatch.setenv('LAWBOT_ENV_FILE', '')  # 개발자의 .env를 읽지 않음 (envfile 테스트는 직접 지정)
    monkeypatch.setenv(
        'LAWBOT_REQUEST_LOG', ''
    )  # 레포의 logs/requests.jsonl에 쓰지 않음 (요청 로그 테스트는 직접 지정)
    if request.node.get_closest_marker('models') is None:
        local_ok = any(request.node.get_closest_marker(m) for m in LOCAL_MARKERS)
        connect, create_connection = socket.socket.connect, socket.create_connection

        def check(address):
            if not (local_ok and isinstance(address, tuple) and address[0] == '127.0.0.1'):
                raise AssertionError(f'테스트 중 외부 네트워크 접속 시도: {address!r}')

        def guarded_connect(sock, address):
            check(address)
            return connect(sock, address)

        def guarded_create_connection(address, *a, **kw):
            check(address)
            return create_connection(address, *a, **kw)

        monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
        monkeypatch.setattr(socket, 'create_connection', guarded_create_connection)
    return monkeypatch


def free_port():
    """지금 비어 있는 127.0.0.1 포트 (테스트에 고정 포트 번호를 쓰지 않음)"""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def set_auth(env, user='tester', pw='test-only-pw'):  # 테스트 전용 가짜 값
    env.setenv('LAWBOT_USER', user)
    env.setenv('LAWBOT_PW', pw)


ADMIN_USER, ADMIN_PW = 'admin-fixture', 'admin-fixture-only'


@pytest.fixture(scope='session')
def admin_hash():
    import base64
    import hashlib

    salt = bytes(range(16))
    digest = hashlib.scrypt(ADMIN_PW.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    salt64, digest64 = (base64.b64encode(value).decode() for value in (salt, digest))
    return f'scrypt$16384$8$1${salt64}${digest64}'


TEST_TOKEN = 'test-only-model-token-0123'  # 테스트 전용 가짜 값


@pytest.fixture(autouse=True)
def model_server(env):
    """load_app이 접속하는 모델 서버와 생성 API를 가짜로 (fakes.FakeServerClient·FakeOpenAI)
    기본: 프로필에 필요한 모델이 모두 있고 올라 있음. 테스트가 FakeServerClient.available·loaded를 바꿔 쓴다"""
    import lawbot.service as service
    from fakes import FakeOpenAI, FakeServerClient

    env.setenv('LAWBOT_MODEL_SERVER', 'http://127.0.0.1:65535')  # 의미 없는 경계값 (가짜라 접속하지 않음)
    env.setenv('LAWBOT_MODEL_TOKEN', TEST_TOKEN)
    FakeServerClient.reset()
    env.setattr(service, 'ModelServerClient', FakeServerClient)
    env.setattr(service, 'OpenAI', FakeOpenAI)
    return FakeServerClient


# ---- 브라우저 E2E (e2e_stub·e2e_live) ----
# pytest-playwright 플러그인은 세션 내내 이벤트 루프를 열어 두어 뒤 테스트의 asyncio.run()을 깨뜨린다 (2026-11-02 확인)
# 그래서 브라우저는 모듈 범위로 직접 열고, 그 모듈이 끝나면 닫는다
@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """테스트마다 쿠키가 비어 있는 새 브라우저 컨텍스트"""
    ctx = browser.new_context(locale='ko-KR')
    yield ctx.new_page()
    ctx.close()
