# tests/test_e2e_live_preflight.py  →  e2e_live의 실행 전 확인·구성 비교 (모델 서버·브라우저 없이, 기본 실행)
# 실제 질문은 test_e2e_live.py (-m e2e_live, 승인 후)
import datetime

import httpx
import pytest

from test_e2e_live import Live, comparable

ENV = {
    'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:65535',
    'LAWBOT_MODEL_TOKEN': 'test-only-model-token-0123',
    'LAWBOT_EMB_MODEL': 'emb',
    'LAWBOT_RERANK_MODEL': 'rr',
    'LAWBOT_PRO_MODEL': 'chat',
}
NOON = datetime.time(12, 0)


def server(ready=True, loaded=True, missing=(), error=None):
    models = [
        {'id': 'emb', 'type': 'embedding', 'loaded': True, 'revision': 'sha256:a'},
        {'id': 'rr', 'type': 'rerank', 'loaded': True, 'revision': 'sha256:b'},
        {'id': 'chat', 'type': 'chat', 'loaded': loaded, 'revision': 'sha256:c'},
    ]

    def get(self, path):
        if error:
            raise error
        if path == '/health':
            return {'ready': ready, 'commit': 'abc'}
        return {'data': [m for m in models if m['id'] not in missing]}

    return get


@pytest.fixture
def live(tmp_path, env):
    def make(**kw):
        env.setattr(Live, 'get', server(**kw))
        return Live(tmp_path, dict(ENV))

    return make


def test_preflight_passes(live):
    assert live().preflight(NOON) == []


@pytest.mark.parametrize('t', [datetime.time(6, 20), datetime.time(6, 30), datetime.time(6, 50)])
def test_preflight_update_window(live, t):
    assert '조문 자동 갱신 시간대' in live().preflight(t)[0]


def test_preflight_no_server_config(tmp_path):
    assert 'LAWBOT_MODEL_SERVER' in Live(tmp_path, {}).preflight(NOON)[0]


def test_preflight_not_ready(live):
    assert live(ready=False).preflight(NOON) == ['서버 미준비: /health ready가 아님']


def test_preflight_unreachable(live):
    assert live(error=httpx.ConnectError('x')).preflight(NOON) == ['서버 미준비: ConnectError']


def test_preflight_missing_model(live):
    assert live(missing=('rr',)).preflight(NOON) == ['모델 없음: rr']


def test_preflight_chat_not_loaded_stops(live):
    (reason,) = live(loaded=False).preflight(NOON)
    assert '올라 있지 않음' in reason  # 모델 교체로 운영이 느려지므로 질문하지 않음


def test_config_hides_secrets(live):
    cfg = live().config()
    text = repr(cfg)
    assert ENV['LAWBOT_MODEL_TOKEN'] not in text and 'LAWBOT_MODEL_TOKEN' not in cfg['settings']
    assert cfg['model_server_commit'] == 'abc' and [m['revision'] for m in cfg['models']] == [
        'sha256:a',
        'sha256:b',
        'sha256:c',
    ]


def test_comparable_ignores_loaded_but_not_revision(live):
    before = live().config()
    unloaded = before | {'models': [m | {'loaded': False} for m in before['models']]}
    assert comparable(unloaded) == comparable(before)  # 운영 사용으로 상주 여부는 바뀔 수 있음
    swapped = before | {'models': [m | {'revision': 'sha256:z'} for m in before['models']]}
    assert comparable(swapped) != comparable(before)  # 같은 id로 폴더가 바뀌면 무효
