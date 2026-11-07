# tests/test_settings.py  →  설정 객체 (lawbot.settings): 시작할 때 한 번 읽어 모두 검증, 틀린 값은 한꺼번에
import pytest

from conftest import TEST_TOKEN
from lawbot import settings
from lawbot.db import connection, migrate
from lawbot.paths import ROOT

BASE = {
    'LAWBOT_USER': 'tester',
    'LAWBOT_PW': 'test-only-pw',
    'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:65535',
    'LAWBOT_MODEL_TOKEN': TEST_TOKEN,
    'LAWBOT_PORT': '27000',
    'LAWBOT_RERANKER_ENABLED': '1',
    'LAWBOT_DECREE_ENABLED': '1',
    'LAWBOT_MODELS': 'pro',
}


def test_load_reads_given_mapping_only(env):
    env.setenv('LAWBOT_PORT', 'x')  # 셸 환경이 틀려도 넘긴 값만 읽는다
    s = settings.load(BASE, web=True)
    assert s.port == 27000 and s.auth == ('tester', 'test-only-pw') and s.model_server == 'http://127.0.0.1:65535'
    assert s.profile.name == 'v4' and [m.key for m in s.profile.models] == ['pro']
    assert s.reranker.candidates == 10 and s.decree.tau == 0.9 and s.decree.models == ('pro',)
    assert (s.llm_url, s.llm_external) == ('http://127.0.0.1:65535/v1', False)
    assert (s.server_wait, s.read_timeout, s.chat_concurrency, s.instance) == (300.0, 300.0, 1, None)
    assert s.feedback is None and s.request_log is not None


def test_all_errors_at_once_without_values(env):
    bad = BASE | {
        'LAWBOT_PORT': 'x',
        'LAWBOT_DECREE_TAU': '2',
        'LAWBOT_MODEL_READ_TIMEOUT': '0',
        'LAWBOT_CHAT_CONCURRENCY': '99',
        'LAWBOT_PW': '',
    }
    with pytest.raises(settings.SettingsError) as e:
        settings.load(bad, web=True)
    msg = str(e.value)
    for name in (
        'LAWBOT_PORT',
        'LAWBOT_DECREE_TAU',
        'LAWBOT_MODEL_READ_TIMEOUT',
        'LAWBOT_CHAT_CONCURRENCY',
        'LAWBOT_PW',
    ):
        assert name in msg
    assert '설정 오류 5개' in msg and TEST_TOKEN not in msg
    assert isinstance(e.value, ValueError) and isinstance(e.value, RuntimeError)  # 기존 호출부의 except와 맞음


def test_web_only_values_not_required_for_tools(env):
    tool = {k: v for k, v in BASE.items() if k not in ('LAWBOT_USER', 'LAWBOT_PW', 'LAWBOT_PORT')}
    s = settings.load(tool)  # 평가·점검 명령: 로그인·포트·동시 처리 수는 보지 않음
    assert s.auth is None and s.port is None


@pytest.mark.parametrize('extra', [{}, {'LAWBOT_DB': '  '}])
def test_db_path_is_optional(extra):
    assert settings.load(BASE | extra, web=True).db is None


def test_db_path_accepts_ignored_relative_path():
    path = 'data/db/lawbot.db'
    assert settings.load(BASE | {'LAWBOT_DB': path}).db == ROOT / path


def test_db_path_accepts_new_external_file(tmp_path):
    path = tmp_path / 'not-created.db'
    assert settings.load(BASE | {'LAWBOT_DB': str(path)}).db == path
    assert not path.exists()


def test_db_path_rejects_unignored_repo_file():
    with pytest.raises(settings.SettingsError, match=r'LAWBOT_DB.*\.gitignore'):
        settings.load(BASE | {'LAWBOT_DB': 'data/not-ignored.db'}, web=True)


@pytest.mark.parametrize('problem', ['missing', 'older', 'newer', 'corrupt'])
def test_web_rejects_db_without_current_schema(tmp_path, problem):
    path = tmp_path / 'private' / 'lawbot.db'
    hints = {
        'missing': 'lawbot-db init',
        'older': 'lawbot-db migrate',
        'newer': '보다 높습니다',
        'corrupt': 'LAWBOT_DB',
    }
    if problem != 'missing':
        migrate.init(path)
    if problem == 'older':
        with connection.connect(path, write=True) as conn:
            conn.execute('DELETE FROM schema_migrations WHERE version=2')
    elif problem == 'newer':
        with connection.connect(path, write=True) as conn:
            conn.execute("INSERT INTO schema_migrations VALUES (3, 'newer', 'checksum', 'time')")
    elif problem == 'corrupt':
        path.write_text('not SQLite')
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(settings.SettingsError, match=hints[problem]):
        settings.load(BASE | {'LAWBOT_DB': str(path)}, web=True)
    assert (path.read_bytes() if path.exists() else None) == before


def test_web_db_rejects_legacy_feedback_directory(tmp_path):
    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    config = {'LAWBOT_DB': str(path), 'LAWBOT_FEEDBACK_DIR': str(tmp_path / 'old'), 'LAWBOT_FEEDBACK': '1'}
    with pytest.raises(settings.SettingsError, match='migrate_csv_to_sqlite feedback'):
        settings.load(BASE | config, web=True)


def test_missing_model_server_is_reported(env):
    with pytest.raises(settings.SettingsError, match='모델 서버 주소가 없습니다'):
        settings.load({k: v for k, v in BASE.items() if k != 'LAWBOT_MODEL_SERVER'})


def test_load_app_uses_given_settings(env):
    import lawbot.service as service
    from fakes import FakeLLM, FakeRetriever

    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    s = settings.load(
        BASE | {'LAWBOT_MODEL_READ_TIMEOUT': '90', 'LAWBOT_RERANKER_ENABLED': '0', 'LAWBOT_DECREE_ENABLED': '0'}
    )
    env.setenv('LAWBOT_MODEL_READ_TIMEOUT', '5')  # load_app은 받은 설정을 쓴다
    a = service.load_app(settings=s)
    assert a.settings is s and a.retriever.args[0][0].query_timeout.read == 90
    assert list(a.models.specs) == ['pro']
