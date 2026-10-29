# tests/test_request_log.py  →  요청 로그 JSONL (service.log_request)·오류 유형 구분·log_summary
# 가짜 검색기·sLLM과 가짜 모델 서버로 돈다 (모델 파일 불필요). 로그는 tmp_path에만 쓴다
import json
import types
import pytest
import lawbot.app as app_mod
import lawbot.remote as remote
import lawbot.service as service
from lawbot import log_summary
from fakes import FakeLLM, FakeRetriever
from model_fakes import TOKEN, GenLLM, Ret, serve

QUESTION = '해고 예고 없이 해고되면 어떻게 하나요 고유문구XYZ'   # 로그에 남으면 안 되는 질문 원문

@pytest.fixture
def log(env, tmp_path):
    path = tmp_path / 'logs' / 'requests.jsonl'
    env.setenv('LAWBOT_REQUEST_LOG', str(path))
    return path

def lines(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]

CHUNKS = ['【결론】', '예고해야', ' 합니다.']

def local_app(env, llm=FakeLLM, resident=2):
    env.setattr(FakeLLM, 'chunks', CHUNKS)              # 다른 테스트가 바꿔 둔 값에 영향받지 않게
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', llm)
    env.setenv('LAWBOT_MAX_RESIDENT', str(resident))
    return service.load_app()

def test_one_line_per_question_without_text(env, log):
    a = local_app(env)
    for fmt in ('full', 'concise'):
        list(service.ask(a, QUESTION, 'fast', fmt=fmt))
    rows = lines(log)
    assert len(rows) == 2 and len({r['request_id'] for r in rows}) == 2
    r = rows[0]
    assert (r['status'], r['error_type'], r['mode'], r['profile'], r['model'], r['fmt']) == ('ok', None, 'local', 'v1', 'fast', 'full')
    assert r['search_ms'] is not None and r['total_ms'] >= r['search_ms'] and r['rerank_ms'] is None
    assert {'ttft_ms', 'load_ms', 'prompt_tokens', 'gen_tokens', 'peak_gb', 'finish', 'caller', 'ts'} <= set(r)
    text = log.read_text(encoding='utf-8')
    assert '고유문구XYZ' not in text and '예고해야' not in text and '해고의 예고' not in text   # 질문·답변·조문 없음

def test_default_fmt_and_disabled(env, log, tmp_path):
    a = local_app(env)
    list(service.ask(a, QUESTION, 'pro'))                 # 평가 스크립트처럼 fmt 없이: 모델 기본 형식
    assert lines(log)[0]['fmt'] == 'concise'
    env.setenv('LAWBOT_REQUEST_LOG', '')                  # 빈 값이면 기록하지 않음
    list(service.ask(a, QUESTION, 'fast'))
    assert len(lines(log)) == 1

def test_default_path_is_gitignored():
    assert service.REQUEST_LOG == service.ROOT / 'logs' / 'requests.jsonl'
    assert 'logs/' in (service.ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()

def test_generation_error(env, log):
    chat = app_mod.make_chat(local_app(env))
    env.setattr(FakeLLM, 'chunks', ['【결론】', RuntimeError('생성 실패')])
    with pytest.raises(RuntimeError, match='생성 실패'):
        list(chat(QUESTION, [], 'fast', 'full'))
    r = lines(log)[-1]
    assert (r['status'], r['error_type']) == ('error', 'generation')

def test_model_load_error(env, log, tmp_path):
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음.txt'))
    chat = app_mod.make_chat(local_app(env, resident=1))
    with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
        list(chat(QUESTION, [], 'pro', 'concise'))
    assert (lines(log)[-1]['status'], lines(log)[-1]['error_type']) == ('error', 'model_load')

def test_rerank_error(env, log):
    a = local_app(env)
    class Broken:
        candidates = 3
        def rerank(self, *a, **kw):
            raise RuntimeError('리랭커 점수 오류')
    a.reranker = Broken()
    with pytest.raises(RuntimeError, match='리랭커 점수 오류'):
        list(app_mod.make_chat(a)(QUESTION, [], 'fast', 'full'))   # 임베딩 순위로 되돌리지 않음
    r = lines(log)[-1]
    assert (r['error_type'], r['reranker']) == ('rerank', True) and r['search_ms'] is not None

def test_cancelled(env, log):
    gen = service.ask(local_app(env), QUESTION, 'fast')
    next(gen)                                             # 첫 토큰까지
    gen.close()                                           # 정지 버튼
    assert lines(log)[-1]['status'] == 'cancelled'

@pytest.mark.parametrize('e,stage,kind', [
    (ConnectionError('x'), 'load', 'model_server'),
    (remote.ModelServerError('Unauthorized', 'x', 401), 'search', 'model_server'),
    (remote.ModelServerError('NotReady', 'x', 503), 'generation', 'model_server'),
    (remote.ModelServerError('RuntimeError', 'x', 500), 'rerank', 'rerank'),   # 서버 쪽 연산 오류는 단계로
    (remote.RemoteLoadError('x'), 'load', 'model_load'),
    (FileNotFoundError('x'), 'load', 'model_load'),
    (KeyError('ultra'), 'load', 'config'),
    (RuntimeError('x'), 'generation', 'generation'),
])
def test_error_type(e, stage, kind):
    assert service.error_type(e, stage) == kind

@pytest.mark.integration                                  # 127.0.0.1 접속 허용 (conftest)
def test_server_down_is_model_server(env, log):
    env.setattr(service, 'Retriever', Ret)
    env.setattr(service, 'LLM', GenLLM)
    srv = service.load_app(local=True)
    with serve(srv) as (_, url):
        env.setenv('LAWBOT_MODEL_SERVER', url)
        env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
        rem = service.load_app()
        chat = app_mod.make_chat(rem)
        list(chat(QUESTION, [], 'fast', 'full'))
    assert lines(log)[-1]['mode'] == 'remote' and lines(log)[-1]['status'] == 'ok'
    with pytest.raises(app_mod.gr.Error, match='모델 서버가 준비되지 않았습니다'):   # 서버를 끈 뒤
        list(chat(QUESTION, [], 'fast', 'full'))
    r = lines(log)[-1]
    assert (r['status'], r['error_type'], r['mode']) == ('error', 'model_server', 'remote')

# ---- log_summary ----
def test_log_summary(env, log, capsys):
    a = local_app(env)
    for _ in range(3):
        list(service.ask(a, QUESTION, 'fast'))
    with log.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'ts': '2026-10-01T00:00:00', 'request_id': 'x', 'mode': 'remote', 'caller': 'lawbot-app',
                            'profile': 'v4', 'model': 'pro', 'status': 'error', 'error_type': 'model_server',
                            'total_ms': 5}) + '\n{"깨진 줄\n')
    log_summary.main([str(log)])
    out = capsys.readouterr().out
    assert '요청 4건' in out and 'ok 3' in out and 'error 1' in out and '읽지 못한 줄 1' in out
    assert '[local · fast] 성공 3건' in out and 'search' in out and 'p95' in out
    assert '오류 유형: model_server 1' in out
    log_summary.main([str(log), '--caller', 'lawbot-app'])
    out = capsys.readouterr().out
    assert '요청 1건' in out
    with pytest.raises(SystemExit, match='요청 로그가 없습니다'):
        log_summary.main([str(log.with_name('없음.jsonl'))])
