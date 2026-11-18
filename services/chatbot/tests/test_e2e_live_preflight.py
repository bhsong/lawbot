# tests/test_e2e_live_preflight.py  →  e2e_live의 실행 전 확인·구성 비교 (모델 서버·브라우저 없이, 기본 실행)
# 실제 질문은 test_e2e_live.py (-m e2e_live, 승인 후)
import datetime

import httpx
import pytest

from test_e2e_live import Live, comparable

ENV = {
    'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:65535',
    'LAWBOT_MODEL_TOKEN': 'test-only-model-token-0123',
    'LAWBOT_LLM_BASE_URL': 'http://127.0.0.1:65534/v1',
    'LAWBOT_LLM_API_KEY': 'test-only-generation-token-0123',
    'LAWBOT_EMB_MODEL': 'emb',
    'LAWBOT_RERANK_MODEL': 'rr',
    'LAWBOT_RERANKER_ENABLED': '1',
    'LAWBOT_PRO_MODEL': 'chat',
}
NOON = datetime.time(12, 0)


def server(ready=True, missing=(), error=None, chat_revision='sha256:c'):
    models = [
        {'id': 'emb', 'type': 'embedding', 'loaded': True, 'revision': 'sha256:a'},
        {'id': 'rr', 'type': 'rerank', 'loaded': True, 'revision': 'sha256:b'},
    ]

    def get(self, path, *, generation=False):
        if error:
            raise error
        if generation:
            assert path == '/models'
            return {'data': [] if 'chat' in missing else [{'id': 'chat', 'object': 'model', 'revision': chat_revision}]}
        if path == '/health':
            return {'ready': ready, 'commit': 'abc'}
        assert path == '/v1/models'
        return {'data': [model for model in models if model['id'] not in missing]}

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


def test_preflight_generation_model_missing(live):
    assert live(missing=('chat',)).preflight(NOON) == ['생성 모델 없음: chat']


def test_preflight_generation_revision_required(live):
    assert live(chat_revision=None).preflight(NOON) == ['생성 모델 revision 없음: chat']


@pytest.mark.parametrize('key', ['LAWBOT_LLM_BASE_URL', 'LAWBOT_LLM_API_KEY'])
def test_preflight_generation_config_required(tmp_path, key):
    config = dict(ENV)
    del config[key]
    assert 'LAWBOT_LLM_BASE_URL·LAWBOT_LLM_API_KEY' in Live(tmp_path, config).preflight(NOON)[0]


def test_config_hides_secrets(live):
    cfg = live().config()
    text = repr(cfg)
    assert ENV['LAWBOT_MODEL_TOKEN'] not in text and 'LAWBOT_MODEL_TOKEN' not in cfg['settings']
    assert ENV['LAWBOT_LLM_API_KEY'] not in text and 'LAWBOT_LLM_API_KEY' not in cfg['settings']
    assert '65535' not in text and '65534' not in text
    assert cfg['model_server_commit'] == 'abc'
    assert [model['revision'] for model in cfg['models']] == ['sha256:a', 'sha256:b']
    assert cfg['generation_models'] == [{'id': 'chat', 'object': 'model', 'revision': 'sha256:c'}]
    assert cfg['generation_server_mlx_lm_version'] is None
    assert 'management location' in cfg['generation_server_mlx_lm_version_note']


def test_comparable_ignores_loaded_but_not_revision(live):
    before = live().config()
    unloaded = before | {'models': [m | {'loaded': False} for m in before['models']]}
    assert comparable(unloaded) == comparable(before)  # 운영 사용으로 상주 여부는 바뀔 수 있음
    swapped = before | {'models': [m | {'revision': 'sha256:z'} for m in before['models']]}
    assert comparable(swapped) != comparable(before)  # 같은 id로 폴더가 바뀌면 무효


def test_generation_revision_change_invalidates_run(live):
    before = live(chat_revision='sha256:c').config()
    after = live(chat_revision='sha256:d').config()
    assert comparable(before) != comparable(after)
