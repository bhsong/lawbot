# tests/test_common_envfile.py  →  공통 .env 파서 (lawbot_common.envfile)와 세 서비스가 같은 규칙으로 읽는지
import pytest

from lawbot_common import envfile
from lawbot_common.ports import parse_port

SAMPLE = (
    '# 주석\n'
    'export LAWBOT_MODEL_TOKEN=abcdef1234567890  # 운영 토큰\n'
    'LAWBOT_MODEL_SERVER="http://127.0.0.1:8001"\n'
    "LAWBOT_PW='a b#c'\n"
    'LAWBOT_EMPTY=\n'
)
EXPECTED = {
    'LAWBOT_MODEL_TOKEN': 'abcdef1234567890',
    'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:8001',
    'LAWBOT_PW': 'a b#c',
    'LAWBOT_EMPTY': '',
}


def test_parse_rules():
    assert envfile.parse(SAMPLE, allowed=envfile.lawbot_keys) == EXPECTED


@pytest.mark.parametrize('bad', ['LAWBOT_PW', 'GRADIO_SERVER_NAME=0.0.0.0', 'LAWBOT_ENV_FILE=x', '1X=2'])
def test_parse_rejects_with_line_number_only(bad):
    with pytest.raises(ValueError, match='1번째 줄') as e:
        envfile.parse(bad + 'secretvalue', allowed=envfile.lawbot_keys)
    assert 'secretvalue' not in str(e.value)  # 값은 오류에 넣지 않음


def test_read_missing_file_is_none(tmp_path):
    assert envfile.read(tmp_path / '없음.env', envfile.lawbot_keys) is None


def test_three_services_read_same_values(tmp_path, monkeypatch):
    # 같은 레포 최상위 .env를 웹앱·모델 서버·monitor가 같은 값으로 읽는다 (export·따옴표·줄 끝 주석)
    from law_monitor import secrets
    from lawbot import envfile as web
    from lawbot_model_server import envfile as ms

    root = tmp_path / '.env'
    root.write_text(SAMPLE, encoding='utf-8')
    assert web.parse(SAMPLE) == ms.parse(SAMPLE) == EXPECTED
    monkeypatch.setattr(secrets, 'ENV_FILE', tmp_path / 'none.env')
    monkeypatch.setattr(secrets, 'ROOT_ENV_FILE', root)
    for key in ('LAWBOT_MODEL_SERVER', 'LAWBOT_MODEL_TOKEN'):
        monkeypatch.delenv(key, raising=False)
        assert secrets.get(key, root=True) == EXPECTED[key]


def test_monitor_reads_only_model_server_keys_from_root(tmp_path, monkeypatch):
    from law_monitor import secrets

    root = tmp_path / '.env'
    root.write_text('LAWBOT_MODEL_TOKEN=tok\nLAWBOT_PW=pw\n', encoding='utf-8')
    monkeypatch.setattr(secrets, 'ENV_FILE', tmp_path / 'none.env')
    monkeypatch.setattr(secrets, 'ROOT_ENV_FILE', root)
    for key in ('LAWBOT_MODEL_TOKEN', 'LAWBOT_PW', 'LAW_MONITOR_SUMMARY_MODEL'):
        monkeypatch.delenv(key, raising=False)
    assert secrets.get('LAWBOT_MODEL_TOKEN', root=True) == 'tok'
    assert secrets.get('LAWBOT_PW', root=True) is None  # 모델 서버 주소·토큰만
    assert secrets.get('LAW_MONITOR_SUMMARY_MODEL', root=True) is None


def test_monitor_env_file_rules(tmp_path, monkeypatch):
    from law_monitor import secrets

    mon = tmp_path / 'monitor.env'
    mon.write_text(
        'export LAW_OC=oc1  # 법제처\nDATA_GO_KR_KEY=\nLAW_MONITOR_SUMMARY_MODEL=m\nLAWBOT_MODEL_TOKEN=t\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(secrets, 'ENV_FILE', mon)
    for key in ('LAW_OC', 'DATA_GO_KR_KEY', 'LAW_MONITOR_SUMMARY_MODEL', 'LAWBOT_MODEL_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    assert secrets.law_oc() == 'oc1' and secrets.get('DATA_GO_KR_KEY') is None  # 빈 값은 없음
    assert secrets.get('LAW_MONITOR_SUMMARY_MODEL') == 'm' and secrets.get('LAWBOT_MODEL_TOKEN') == 't'
    mon.write_text('LAWBOT_PW=x\n', encoding='utf-8')  # monitor .env에 웹앱 비밀번호는 적을 수 없음
    with pytest.raises(ValueError, match='1번째 줄'):
        secrets.get('LAW_OC')


@pytest.mark.parametrize('value,ok', [('1024', True), (' 65535 ', True), ('1023', False), ('x', False), ('', False)])
def test_parse_port(value, ok):
    if ok:
        assert parse_port('P', value) == int(value)
    else:
        with pytest.raises(ValueError, match='P는 1024–65535 정수'):
            parse_port('P', value)
