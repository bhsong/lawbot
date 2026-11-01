# tests/test_remote.py  →  원격 모드 웹앱 (LAWBOT_MODEL_SERVER → lawbot.remote) 검사
# 같은 가짜 모델로 로컬 모드와 원격 모드(가짜 모델 서버 경유)를 돌려 결과가 같은지 본다. 모델 파일 불필요
import dataclasses
import os
import subprocess
import sys
import textwrap
import threading
import time

import pandas as pd
import pytest

import lawbot.app as app_mod
import lawbot.eval_v4 as ev
import lawbot.remote as remote
import lawbot.service as service
from conftest import free_port
from lawbot import deploy, envfile, security
from lawbot.prompts import SYSTEM
from model_fakes import TOKEN, GenLLM, Reranker, Ret, serve

pytestmark = pytest.mark.integration  # 127.0.0.1 접속 허용 (conftest)


@pytest.fixture
def fakes(env):
    env.setattr(service, 'Retriever', Ret)
    env.setattr(service, 'LLM', GenLLM)
    env.setattr(remote, 'WAIT_INTERVAL', 0.05)
    return env


def local_app(env, resident=2, reranker=False):
    """가짜 모델로 만든 로컬 모드 LawbotApp (v1). 서버에 넣거나 원격 결과와 비교하는 기준"""
    env.setenv('LAWBOT_MAX_RESIDENT', str(resident))
    a = service.load_app(local=True)
    env.delenv('LAWBOT_MAX_RESIDENT')
    return dataclasses.replace(a, reranker=Reranker(4)) if reranker else a


def connect(env, url, **kw):
    """원격 모드 load_app (웹앱 쪽). 모델 클래스(Retriever·LLM)는 만들지 않아야 함"""
    env.setenv('LAWBOT_MODEL_SERVER', url)
    env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    created = []
    env.setattr(service, 'Retriever', lambda *a, **k: created.append('Retriever'))
    env.setattr(service, 'LLM', lambda *a, **k: created.append('LLM'))
    a = service.load_app(**kw)
    assert created == []
    return a


def run(a, question, model, **kw):
    return list(service.ask(a, question, model, **kw))


# ---- T1: 로컬과 원격의 결과가 같다 ----
@pytest.mark.parametrize('reranker', [False, True], ids=['embedding', 'reranker'])
@pytest.mark.parametrize('resident', [1, 2])
def test_remote_matches_local(fakes, reranker, resident):
    loc, srv = local_app(fakes, resident, reranker), local_app(fakes, resident, reranker)
    with serve(srv) as (_, url):
        rem = connect(fakes, url)
        assert (rem.reranker is None) == (not reranker)  # 리랭커 설정은 서버 기준
        assert rem.full_system == loc.full_system and rem.early_stop == loc.early_stop
        for model, fmt in (('pro', None), ('fast', 'full'), ('pro', 'full'), ('fast', None), ('pro', 'concise')):
            a, b = (
                run(loc, '수습 기간에도 해고 예고?', model, fmt=fmt),
                run(rem, '수습 기간에도 해고 예고?', model, fmt=fmt),
            )
            assert [s for s, *_ in a] == [s for s, *_ in b]  # load/generate/done 순서
            assert [h for _, h, _, _ in a] == [h for _, h, _, _ in b]
            assert [r for _, _, r, _ in a] == [r for _, _, r, _ in b]
            sa, sb = a[-1][3], b[-1][3]
            assert set(sa) == set(sb)
            for k in ('model', 'tps', 'prompt_tokens', 'prompt_tps', 'gen_tokens', 'peak_gb', 'finish'):
                assert sa[k] == sb[k], k
            assert (sa['load'] is None) == (sb['load'] is None)
            # T2: 서버의 llm.stream이 받은 system·질문·조문이 로컬과 같다
            assert srv.models.loaded[model].calls[-1] == loc.models.loaded[model].calls[-1]


def test_system_sent_per_request(fakes):
    # D5: 웹앱 쪽 system을 요청마다 보낸다 (Pro 간결 system 파일, '전체'는 4항목, Fast는 v1 SYSTEM)
    srv = local_app(fakes)
    with serve(srv) as (_, url):
        rem = connect(fakes, url)
        pro_file = deploy.get_profile().models[1].system_file.read_text(encoding='utf-8').strip()
        for model, fmt, want in (
            ('pro', None, pro_file),
            ('pro', 'full', SYSTEM),
            ('fast', 'full', SYSTEM),
            ('fast', 'concise', SYSTEM),
        ):
            run(rem, '질문', model, fmt=fmt)
            assert srv.models.loaded[model].calls[-1]['system'] == want, (model, fmt)


def test_early_stop_closes_stream(fakes):
    # 간결형 조기 종료: 원격 생성기를 닫으면 서버 생성도 멈춤
    fakes.setattr(GenLLM, 'chunks', ['【결론】', '답', '【설명】'] + ['더'] * 2000)
    fakes.setattr(GenLLM, 'delay', 0.001)
    srv = local_app(fakes)
    with serve(srv) as (server, url):
        rem = connect(fakes, url)
        steps = run(rem, '질문', 'fast', early_stop=True)
        assert steps[-1][2] == '【결론】답【설명】' and 'early_stop_at' in steps[-1][3]
        llm = srv.models.loaded['fast']
        deadline = time.monotonic() + 10
        while not llm.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert llm.closed and llm.yielded < 2003
        assert server.model_lock.acquire(timeout=5)
        server.model_lock.release()


# ---- 웹앱 화면 (app.py) ----
def test_chat_through_model_server(fakes):
    srv = local_app(fakes)
    with serve(srv) as (_, url):
        chat = app_mod.make_chat(connect(fakes, url))
        text, src, stats = list(chat('해고 예고?', [], 'fast', 'full'))[-1]
        assert text.startswith('【결론】예고해야') and '해고의 예고' in src and '완료' in stats
        assert app_mod.build_demo(connect(fakes, url)).title == '근로기준법 챗봇'


# ---- T7: 오류 ----
def test_generation_error_raises(fakes):
    srv = local_app(fakes)
    with serve(srv) as (_, url):
        chat = app_mod.make_chat(connect(fakes, url))
        fakes.setattr(GenLLM, 'chunks', ['【결론】', RuntimeError('generation failed')])
        with pytest.raises(RuntimeError, match='RuntimeError: generation failed'):
            list(chat('질문', [], 'fast', 'full'))


@pytest.mark.parametrize('error,cls', [(FileNotFoundError('models/없음'), OSError), (ValueError('형식'), ValueError)])
def test_load_failure_shows_existing_message(fakes, error, cls):
    srv = local_app(fakes)
    fast = srv.models.loaded['fast']

    def loader(spec):
        if spec.key == 'pro':
            raise error
        return fast

    srv.models = service.ModelManager(srv.profile.models, loader, 1)
    srv.models.get('fast')
    with serve(srv) as (_, url):
        rem = connect(fakes, url)
        with pytest.raises(cls):
            rem.models.get('pro')
        chat = app_mod.make_chat(rem)
        with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
            list(chat('질문', [], 'pro', 'concise'))


def test_missing_model_system_file_is_load_error(fakes, tmp_path):
    srv = local_app(fakes, resident=1)  # Pro가 아직 안 올라간 상태
    with serve(srv) as (_, url):
        fakes.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없는파일.txt'))
        chat = app_mod.make_chat(connect(fakes, url))
        with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
            list(chat('질문', [], 'pro', 'concise'))


def test_server_down_is_clear(fakes):
    port = free_port()
    fakes.setenv('LAWBOT_MODEL_SERVER_WAIT', '0.2')
    with pytest.raises(RuntimeError, match=f'모델 서버에 연결할 수 없습니다: http://127.0.0.1:{port}'):
        connect(fakes, f'http://127.0.0.1:{port}')


def test_wrong_token_is_clear(fakes, capsys):
    srv = local_app(fakes)
    with serve(srv, token='server-only-token-0123456789') as (_, url):
        with pytest.raises(remote.ModelServerError, match='LAWBOT_MODEL_TOKEN') as e:
            connect(fakes, url)
        assert TOKEN not in str(e.value) and 'server-only-token' not in str(e.value)
    assert TOKEN not in capsys.readouterr().out


def test_remote_requires_token(fakes):
    fakes.setenv('LAWBOT_MODEL_SERVER', f'http://127.0.0.1:{free_port()}')
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_TOKEN'):
        service.load_app()


# ---- T5: 127.0.0.1만 ----
@pytest.mark.parametrize(
    'url',
    [
        'http://0.0.0.0:{p}',
        'http://example.com:{p}',
        'https://127.0.0.1:{p}',
        'http://localhost:{p}',
        'http://127.0.0.1',
        'http://user:pw@127.0.0.1:{p}',
        'http://127.0.0.1:{p}/v1',
        'http://127.0.0.1:1023',
        'http://[::1]:{p}',
        'http://127.0.0.1:{p}?x=1',
        '127.0.0.1:{p}',
    ],
)
def test_remote_url_must_be_localhost(fakes, url):
    fakes.setenv('LAWBOT_MODEL_SERVER', url.format(p=free_port()))
    fakes.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    with pytest.raises(ValueError, match='LAWBOT_MODEL_SERVER'):
        service.load_app()


def test_remote_url_normalized(env):
    port = free_port()
    env.setenv('LAWBOT_MODEL_SERVER', f' http://127.0.0.1:{port}/ ')
    assert security.model_server_url() == f'http://127.0.0.1:{port}'


# ---- T8: 서버와 웹앱 설정이 다르면 거부 ----
def test_mismatch_refused(fakes):
    srv = local_app(fakes)
    p = deploy.get_profile()
    with serve(srv) as (_, url):
        fakes.setenv('LAWBOT_MODEL_SERVER', url)
        fakes.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
        for profile, want in (
            (dataclasses.replace(p, name='v4'), '프로필: 서버 v1 / 웹앱 v4'),
            (dataclasses.replace(p, doc_style='v4'), 'doc_style: 서버 v1 / 웹앱 v4'),
        ):
            with pytest.raises(RuntimeError, match=want):
                service.load_app(profile)
        fakes.setattr(remote, 'API_VERSION', 99)
        with pytest.raises(RuntimeError, match='api_version: 서버 2 / 웹앱 99'):
            service.load_app()


def test_missing_model_key_refused(fakes):
    fakes.setenv('LAWBOT_MODELS', 'fast')  # 서버는 Fast만
    srv = local_app(fakes)
    fakes.delenv('LAWBOT_MODELS')  # 웹앱은 Fast·Pro
    with serve(srv) as (_, url):
        with pytest.raises(RuntimeError, match=r"웹앱 모델 \['pro'\]이 서버 모델 \['fast'\]에 없음"):
            connect(fakes, url)
        fakes.setenv('LAWBOT_MODELS', 'fast')
        assert list(connect(fakes, url).models.specs) == ['fast']


# ---- T9: 서버 준비 대기 ----
def test_waits_until_ready(fakes):
    srv = local_app(fakes)
    with serve(None) as (server, url):
        threading.Timer(0.3, lambda: setattr(server, 'lawbot', srv)).start()
        fakes.setenv('LAWBOT_MODEL_SERVER_WAIT', '10')
        t0 = time.monotonic()
        a = connect(fakes, url)
        assert time.monotonic() - t0 >= 0.25 and isinstance(a.models, remote.RemoteModelManager)


def test_wait_timeout(fakes):
    with serve(None) as (_, url):
        fakes.setenv('LAWBOT_MODEL_SERVER_WAIT', '0.3')
        with pytest.raises(RuntimeError, match=r'0.3초 안에 준비되지 않았습니다 .모델을 불러오는 중'):
            connect(fakes, url)


# ---- 원격 ModelManager ----
def test_remote_manager_follows_server(fakes):
    srv = local_app(fakes, resident=1)
    with serve(srv) as (_, url):
        m = connect(fakes, url).models
        assert m.max_resident == 1 and m.loaded == ['fast'] and m.is_loaded('fast') and not m.is_loaded('pro')
        llm, load_s = m.get('pro')
        assert load_s is not None and m.loaded == ['pro']
        assert m.get('pro') == (llm, None)  # 같은 RemoteLLM (llm.system을 바꾸면 계속 적용: eval_concise)
        with pytest.raises(KeyError):
            m.get('ultra')


# ---- T13: 평가 스크립트(eval_v4)의 조문 키 계산이 로컬과 같다 ----
class V4Ret:
    def __init__(self, *a, **kw):
        from test_eval_v4 import Ret as R

        self.r = R()
        self.ids, self.titles, self.docs, self.laws = self.r.ids, self.r.titles, self.r.docs, self.r.laws

    def rank_all(self, queries):
        return self.r.rank_all(list(queries))

    def search(self, q, k=3):
        return self.r.search(q, k)


def test_eval_v4_retrieval_same_remote(fakes, tmp_path):
    from test_eval_v4 import RR, frame

    p = deploy.get_profile('v4')

    def app_v4():
        models = service.ModelManager(p.models, lambda spec: GenLLM(spec.path), 1)
        models.get('pro')
        return service.LawbotApp(p, V4Ret(), models, False, RR(), 'FULL')

    loc = app_v4()
    with serve(app_v4()) as (_, url):
        fakes.setenv('LAWBOT_PROFILE', 'v4')
        rem = connect(fakes, url)
        assert ev.hit_table(rem) == ev.hit_table(loc)
        assert (
            rem.retriever.rank_all(pd.Series(['질문', '질문질문'])) == loc.retriever.rank_all(['질문', '질문질문'])
        ).all()
        t = frame()
        ev.run_retrieval(loc, t, tmp_path / 'local.csv')
        ev.run_retrieval(rem, t, tmp_path / 'remote.csv')
        cols = ['id', 'emb_rank', 'rr_rank', 'rr_top3']
        assert pd.read_csv(tmp_path / 'local.csv')[cols].equals(pd.read_csv(tmp_path / 'remote.csv')[cols])
        assert ev.refusal_cases(rem, t.assign(confusable_with=None), n=6) == ev.refusal_cases(
            loc, t.assign(confusable_with=None), n=6
        )


# ---- T11: 원격 모드 웹앱은 모델 라이브러리를 올리지 않는다 ----
def test_remote_webapp_does_not_load_model_libraries(fakes):
    srv = local_app(fakes)
    with serve(srv) as (_, url):
        code = textwrap.dedent("""
            import sys
            import lawbot.app, lawbot.service as s
            a = s.load_app()
            steps = list(s.ask(a, '해고 예고?', 'pro', fmt='full'))
            assert steps[-1][2].startswith('【결론】'), steps[-1]
            print('LOADED=' + ','.join(m for m in ('mlx', 'mlx_lm', 'torch', 'sentence_transformers', 'lawbot.llm',
                                       'lawbot.retriever', 'lawbot.reranker') if m in sys.modules))
        """)
        env = {k: v for k, v in os.environ.items()} | {
            'LAWBOT_MODEL_SERVER': url,
            'LAWBOT_MODEL_TOKEN': TOKEN,
            'LAWBOT_PROFILE': 'v1',
            'LAWBOT_ENV_FILE': '',
        }
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().splitlines()[-1] == 'LOADED='
        assert TOKEN not in out.stdout + out.stderr


# ---- T15: .env의 토큰도 파일 권한 경고 ----
def test_env_file_warns_for_token(env, tmp_path, capsys):
    f = tmp_path / 'lawbot.env'
    f.write_text(f'LAWBOT_MODEL_TOKEN={TOKEN}\n', encoding='utf-8')
    env.setenv('LAWBOT_ENV_FILE', str(f))
    f.chmod(0o600)
    envfile.load()
    assert '경고' not in capsys.readouterr().out
    f.chmod(0o644)
    env.delenv('LAWBOT_MODEL_TOKEN')
    envfile.load()
    out = capsys.readouterr().out
    assert 'chmod 600' in out and 'LAWBOT_MODEL_TOKEN' in out and TOKEN not in out


# ---- 운영 도구: check_deploy ----
def test_check_deploy_model_server(fakes):
    from lawbot import check_deploy

    def server_lines():
        return [(lv, m) for lv, m in check_deploy.run() if '모델 서버' in m or '로컬 모드' in m or '웹앱 주소' in m]

    assert ('INFO', '로컬 모드 (LAWBOT_MODEL_SERVER 없음): 웹앱이 모델을 직접 올림') in server_lines()
    srv = local_app(fakes)
    with serve(srv) as (_, url):
        fakes.setenv('LAWBOT_MODEL_SERVER', url)
        lines = server_lines()
        assert lines[0][0] == 'OK' and lines[0][1].startswith('웹앱 주소 127.0.0.1:')
        assert any(lv == 'FAIL' and 'LAWBOT_MODEL_TOKEN' in m for lv, m in lines)
        fakes.setenv('LAWBOT_MODEL_TOKEN', 'wrong-token-0123456789')
        assert any(lv == 'FAIL' and '토큰이 맞지 않습니다' in m for lv, m in server_lines())
        fakes.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
        levels = [lv for lv, _ in server_lines()]
        assert levels == ['OK', 'OK', 'OK', 'INFO'], server_lines()
        fakes.setenv('LAWBOT_PROFILE', 'v4')
        assert any(lv == 'FAIL' and '프로필: 서버 v1 / 웹앱 v4' in m for lv, m in server_lines())
    fakes.setenv('LAWBOT_PROFILE', 'v1')
    assert any(lv == 'FAIL' and '연결할 수 없습니다' in m for lv, m in server_lines())  # 서버가 꺼져 있음
    fakes.setenv('LAWBOT_MODEL_SERVER', 'http://example.com:80')
    assert any(lv == 'FAIL' and '모델 서버 주소' in m for lv, m in server_lines())


def test_proxy_env_is_ignored(fakes):
    # 프록시 환경 변수가 있어도 127.0.0.1에 직접 접속 (질문이 프록시로 나가지 않음)
    with serve(local_app(fakes)) as (_, url):
        for k in ('HTTP_PROXY', 'http_proxy', 'ALL_PROXY'):
            fakes.setenv(k, 'http://10.255.255.1:3128')
        fakes.delenv('NO_PROXY', raising=False)
        fakes.delenv('no_proxy', raising=False)
        assert run(connect(fakes, url), '질문', 'fast')[-1][2] == '【결론】예고해야 합니다.'


def test_wait_must_be_number(fakes):
    fakes.setenv('LAWBOT_MODEL_SERVER_WAIT', '5분')
    with pytest.raises(ValueError, match='LAWBOT_MODEL_SERVER_WAIT'):
        connect(fakes, f'http://127.0.0.1:{free_port()}')
