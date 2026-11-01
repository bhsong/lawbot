# tests/test_model_server.py  →  내부 모델 서버(lawbot.model_server) 검사: 토큰·로컬 전용·API·끊김 처리
# 가짜 LawbotApp을 127.0.0.1의 빈 포트에 띄운다 (모델 파일 불필요). 원격 모드 웹앱 쪽은 test_remote.py
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

import lawbot.model_server as ms
import lawbot.service as service
from conftest import free_port
from model_fakes import TOKEN, GenLLM, Reranker, Ret, serve

pytestmark = pytest.mark.integration      # 127.0.0.1 접속 허용 (conftest)

@pytest.fixture
def lawbot(env):
    """가짜 모델로 만든 로컬 모드 LawbotApp (v1, Fast·Pro 상주)"""
    env.setattr(service, 'Retriever', Ret)
    env.setattr(service, 'LLM', GenLLM)
    return service.load_app()

def call(url, path, body=None, token=TOKEN, raw=None):
    """(상태 코드, JSON 응답). raw를 주면 그 바이트를 본문으로"""
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    headers = {} if token is None else {'Authorization': f'Bearer {token}'}
    req = urllib.request.Request(url + path, data=data, headers=headers, method='GET' if data is None else 'POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if r.headers['Content-Type'].startswith('application/json')
                              else [json.loads(l) for l in text.splitlines()])
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# ---- T4: 토큰 없으면 실행 거부, 127.0.0.1 고정 ----
@pytest.mark.parametrize('token', [None, '', 'short-token'])
def test_server_refuses_without_token(token):
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_TOKEN'):
        ms.create_server(None, 0, token)

def test_server_binds_localhost_only(lawbot):
    with serve(lawbot) as (server, url):
        assert server.server_address[0] == '127.0.0.1'
        assert url.startswith('http://127.0.0.1:')

def test_main_refuses_before_loading_models(env, capsys):
    loaded = []
    env.setattr(service, 'load_app', lambda **kw: loaded.append(kw))
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_TOKEN'):
        ms.main()
    env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_SERVER_PORT'):        # 포트 기본값 없음
        ms.main()
    env.setenv('LAWBOT_MODEL_SERVER_PORT', '1023')
    with pytest.raises(ValueError, match='LAWBOT_MODEL_SERVER_PORT'):
        ms.main()
    assert loaded == []
    assert TOKEN not in capsys.readouterr().out

# ---- T10: 모델 서버는 LAWBOT_MODEL_SERVER가 있어도 로컬 모드로 모델을 올린다 ----
def test_main_loads_local_even_with_remote_env(env, capsys):
    env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    env.setenv('LAWBOT_MODEL_SERVER_PORT', str(free_port()))
    env.setenv('LAWBOT_MODEL_SERVER', f'http://127.0.0.1:{free_port()}')    # 같은 .env를 웹앱과 함께 읽는 경우
    seen = []
    def fail(**kw):
        seen.append(kw)
        raise FileNotFoundError('models/없음')
    env.setattr(service, 'load_app', fail)
    with pytest.raises(SystemExit) as e:          # 로딩 실패는 종료 코드 1 (launchd가 간격을 두고 다시 띄움)
        ms.main()
    assert e.value.code == 1 and seen == [{'local': True}]
    assert '모델 불러오기 실패: FileNotFoundError' in capsys.readouterr().out

def test_load_app_local_ignores_remote_env(env):
    env.setattr(service, 'Retriever', Ret)
    env.setattr(service, 'LLM', GenLLM)
    env.setenv('LAWBOT_MODEL_SERVER', f'http://127.0.0.1:{free_port()}')    # 아무도 없는 포트: 접속하면 실패
    app = service.load_app(local=True)
    assert isinstance(app.retriever, Ret) and isinstance(app.models, service.ModelManager)

# ---- T3: 토큰 ----
@pytest.mark.parametrize('token', [None, '', 'wrong-token-0123456789', TOKEN + 'x'])
def test_wrong_token_is_401(lawbot, token, capsys):
    with serve(lawbot) as (_, url):
        for path, body in (('/health', None), ('/info', None), ('/search', {'question': 'q', 'k': 1})):
            status, out = call(url, path, body, token=token)
            assert status == 401 and out['error']['type'] == 'Unauthorized'
            assert TOKEN not in json.dumps(out, ensure_ascii=False)
    assert TOKEN not in capsys.readouterr().out

# ---- API ----
def test_health_info_articles(lawbot):
    with serve(lawbot) as (_, url):
        assert call(url, '/health') == (200, {'ready': True})
        status, info = call(url, '/info')
        assert status == 200
        assert info['api_version'] == ms.API_VERSION
        assert info['profile'] == {'name': 'v1', 'doc_style': 'v1'}
        assert info['models'] == ['fast', 'pro'] and info['loaded'] == ['fast', 'pro'] and info['max_resident'] == 2
        assert info['reranker'] is None and info['n_articles'] == 4
        assert info['memory'] is None or set(info['memory']) == {'active', 'peak', 'cache'}
        status, arts = call(url, '/articles')
        assert arts['ids'] == list(lawbot.retriever.ids) and arts['docs'] == lawbot.retriever.docs
        assert arts['laws'] is None

def test_not_ready_is_503():
    with serve(None) as (server, url):
        assert call(url, '/health') == (200, {'ready': False})
        status, out = call(url, '/search', {'question': 'q', 'k': 1})
        assert status == 503 and out['error']['type'] == 'NotReady'

def test_search_rank_rerank_load(lawbot):
    lawbot.reranker = Reranker(4)
    with serve(lawbot) as (_, url):
        status, out = call(url, '/search', {'question': '해고 예고?', 'k': 3})
        assert status == 200 and out['hits'] == lawbot.retriever.search('해고 예고?', k=3)
        status, out = call(url, '/rank_all', {'questions': ['a', 'bb']})
        assert out['rank'] == lawbot.retriever.rank_all(['a', 'bb']).tolist()
        cands = call(url, '/search', {'question': 'q', 'k': 4})[1]['hits']
        status, out = call(url, '/rerank', {'question': 'q', 'candidates': cands, 'top_k': 3})
        assert out['hits'] == Reranker().rerank('q', cands, top_k=3)
        assert call(url, '/info')[1]['reranker'] == {'candidates': 4}
        assert call(url, '/load', {'model': 'fast'}) == (200, {'load_s': None})   # 이미 상주

def test_generate_stream(lawbot):
    with serve(lawbot) as (_, url):
        status, lines = call(url, '/generate', {'model': 'pro', 'question': '질문', 'hits': [{'article_id': '제26조'}],
                                                'system': 'SYS', 'max_tokens': 77})
        assert status == 200
        assert ''.join(l['text'] for l in lines) == '【결론】예고해야 합니다.'
        assert lines[-1] == {'text': ' 합니다.', 'generation_tps': 20.0, 'prompt_tokens': 11, 'prompt_tps': 100.0,
                             'generation_tokens': 3, 'peak_memory': 1.5, 'finish_reason': 'stop'}
        assert lawbot.models.loaded['pro'].calls[-1] == {'question': '질문', 'hits': [{'article_id': '제26조'}],
                                                         'max_tokens': 77, 'system': 'SYS'}

# ---- T14: 입력 오류 ----
def test_bad_requests(lawbot):
    with serve(lawbot) as (_, url):
        gen = {'model': 'fast', 'question': 'q', 'hits': [], 'system': 'S'}
        for path, body in (('/search', {'question': 'q'}), ('/search', {'question': 1, 'k': 1}),
                           ('/search', {'question': 'q', 'k': 0}), ('/search', {'question': 'q', 'k': True}),
                           ('/rank_all', {'questions': 'q'}), ('/rerank', {'question': 'q', 'candidates': [], 'top_k': 1}),
                           ('/load', {'model': 'ultra'}), ('/generate', gen | {'system': '  '}),
                           ('/generate', gen | {'model': 'ultra'}), ('/generate', gen | {'hits': ['x']})):
            status, out = call(url, path, body)
            assert status == 400 and out['error']['type'] == 'BadRequest', (path, body)
        for raw in (b'{', b'[1]', b'\xff'):
            assert call(url, '/search', raw=raw)[0] == 400
        assert call(url, '/nothing')[0] == 404

def test_body_too_large_is_413(lawbot):
    with serve(lawbot) as (_, url):
        port = int(url.rsplit(':', 1)[1])
        with socket.create_connection(('127.0.0.1', port), timeout=10) as s:   # 본문을 보내기 전에 거부되는지
            s.sendall(f'POST /rank_all HTTP/1.0\r\nAuthorization: Bearer {TOKEN}\r\n'
                      f'Content-Length: {ms.MAX_BODY + 1}\r\n\r\n'.encode())
            assert s.recv(100).split(b' ')[1] == b'413'
        assert call(url, '/rank_all', {'questions': ['질문'] * 2075})[0] == 200   # 평가 test 전체는 한도 안
        with socket.create_connection(('127.0.0.1', port), timeout=10) as s:   # 음수 길이로 서버 스레드를 잡아 두지 못함
            s.sendall(f'POST /search HTTP/1.0\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: -1\r\n\r\n'.encode())
            assert s.recv(100).split(b' ')[1] == b'400'

# ---- T6·T7: 끊김·오류 ----
def test_client_disconnect_stops_generation_and_releases_lock(lawbot, env):
    env.setattr(GenLLM, 'chunks', ['조각'] * 5000)
    env.setattr(GenLLM, 'delay', 0.001)
    llm = lawbot.models.loaded['fast']
    with serve(lawbot) as (server, url):
        req = urllib.request.Request(url + '/generate', data=json.dumps(
            {'model': 'fast', 'question': 'q', 'hits': [], 'system': 'S'}).encode(),
            headers={'Authorization': f'Bearer {TOKEN}'})
        r = urllib.request.urlopen(req, timeout=10)
        assert json.loads(r.readline())['text'] == '조각'
        r.close()                                 # 정지 버튼·조기 종료
        deadline = time.monotonic() + 10
        while not llm.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert llm.closed and llm.yielded < 5000  # 생성을 끝까지 하지 않고 멈춤
        assert server.model_lock.acquire(timeout=5)
        server.model_lock.release()
        assert call(url, '/search', {'question': 'q', 'k': 1})[0] == 200   # 서버는 살아 있고 다음 요청 처리

def test_generation_error_is_last_line(lawbot, env):
    env.setattr(GenLLM, 'chunks', ['【결론】', RuntimeError('generation failed')])
    with serve(lawbot) as (server, url):
        status, lines = call(url, '/generate', {'model': 'fast', 'question': 'q', 'hits': [], 'system': 'S'})
        assert status == 200 and lines[0]['text'] == '【결론】'
        assert lines[-1] == {'error': {'type': 'RuntimeError', 'message': 'generation failed', 'base': None}}
        assert not server.model_lock.locked()

def test_load_error_has_type_and_base(env, lawbot):
    def loader(spec):
        raise FileNotFoundError(f'모델 폴더 없음: {spec.path}')
    lawbot.models = service.ModelManager(lawbot.profile.models, loader, 1)
    with serve(lawbot) as (server, url):
        status, out = call(url, '/load', {'model': 'pro'})
        assert status == 500 and out['error']['type'] == 'FileNotFoundError' and out['error']['base'] == 'OSError'
        assert not server.model_lock.locked()

def test_requests_are_serialized(lawbot, env):
    # 모델 연산은 한 번에 하나: 생성 중 들어온 검색은 생성이 끝난 뒤 처리
    env.setattr(GenLLM, 'chunks', ['a'] * 20)
    env.setattr(GenLLM, 'delay', 0.01)
    llm, seen, search = lawbot.models.loaded['fast'], [], lawbot.retriever.search
    lawbot.retriever.search = lambda q, k=3: seen.append(llm.yielded) or search(q, k)
    with serve(lawbot) as (_, url):
        t = threading.Thread(target=call, args=(url, '/generate', {'model': 'fast', 'question': 'q', 'hits': [],
                                                                  'system': 'S'}))
        t.start()
        while not llm.yielded:
            time.sleep(0.005)
        assert call(url, '/search', {'question': 'q', 'k': 1})[0] == 200
        t.join(10)
    assert seen == [20]                           # 검색은 생성 20조각이 모두 끝난 뒤

# ---- T12: gradio·모델 라이브러리를 import하지 않음 ----
def test_model_server_does_not_import_gradio():
    code = ('import sys, lawbot.model_server\n'
            'print(",".join(m for m in ("gradio", "mlx", "mlx_lm", "torch", "sentence_transformers") if m in sys.modules))')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ''

# ---- 배포 템플릿 (.env 예시·launchd): 비밀 값·포트 번호 없음 ----
def test_env_examples_have_no_ports_or_secrets(env):
    import re

    from lawbot import deploy, envfile, paths, security
    prod = (paths.ROOT / '.env.example').read_text(encoding='utf-8')
    block = prod[prod.index('# ---- 상시 운영'):]
    assert re.findall(r'^# (LAWBOT_\w+)=$', block, re.M) == ['LAWBOT_PORT', 'LAWBOT_MODEL_SERVER_PORT', 'LAWBOT_MODEL_TOKEN']
    assert 'LAWBOT_MODEL_SERVER' not in envfile.parse(prod)     # 주석이라 기본 동작은 로컬 모드 그대로
    dev = envfile.parse((paths.ROOT / '.env.dev.example').read_text(encoding='utf-8'))
    assert dev['LAWBOT_USER'] == dev['LAWBOT_PW'] == dev['LAWBOT_MODEL_TOKEN'] == dev['LAWBOT_PORT'] == ''
    assert dev['LAWBOT_INSTANCE'] == '개발'
    assert dev['LAWBOT_MODEL_SERVER'] == 'http://127.0.0.1:<모델 서버 포트>'   # 채우기 전에는 실행 거부
    env.setenv('LAWBOT_MODEL_SERVER', dev['LAWBOT_MODEL_SERVER'])
    with pytest.raises(ValueError, match='LAWBOT_MODEL_SERVER'):
        security.model_server_url()
    assert not {k for k in dev if 'PATH' in k or 'RERANKER' in k or 'RESIDENT' in k}   # 모델 설정은 서버 쪽
    for k in ('LAWBOT_PROFILE', 'LAWBOT_MODELS', 'LAWBOT_PRO_SYSTEM'):
        env.setenv(k, dev[k])
    assert deploy.get_profile().models[0].system_file.is_file()
    for k in ('LAWBOT_DECREE_ENABLED', 'LAWBOT_DECREE_TAU', 'LAWBOT_DECREE_K', 'LAWBOT_DECREE_MODELS'):
        env.setenv(k, dev[k])                    # 하위법령 설정은 서버와 같아야 함 (리랭커 변수 없이 원격 모드로 읽힘)
    assert deploy.decree(deploy.get_profile(), require_reranker=False) == deploy.DecreeConfig(0.9, 2, ('pro',))

@pytest.mark.parametrize('kind,prog', [('model-server', 'lawbot-model-server'), ('web', 'lawbot-app')])
def test_launchd_templates(kind, prog):
    import plistlib

    from lawbot import paths
    text = (paths.ROOT / 'deploy' / 'launchd' / f'com.lawbot.{kind}.plist.example').read_text(encoding='utf-8')
    p = plistlib.loads(text.encode())
    assert p['Label'] == f'com.lawbot.{kind}'
    assert p['ProgramArguments'] == ['__UV__', 'run', '--frozen', prog]
    assert p['WorkingDirectory'] == '__PROD_DIR__' and p['RunAtLoad'] and p['KeepAlive'] and p['ThrottleInterval'] == 30
    assert p['StandardOutPath'] == p['StandardErrorPath'] == f'__LOG_DIR__/{kind}.log'
    assert set(p['EnvironmentVariables']) == {'PYTHONUNBUFFERED'}         # 비밀 값·포트는 .env에만
    assert 'LAWBOT_' not in text and not any('~' in str(v) for v in p.values())
