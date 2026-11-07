# tests/test_webapp.py  →  Fast/Pro 모델 선택·전환, 간결형 답변, v4 검색 형식, 배포 검사 (가짜 모델, 모델 파일 불필요)
import types

import pandas as pd
import pytest
from fastapi import HTTPException

import lawbot.retriever as retriever_mod
import lawbot.service as service
from chat_support import run
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot import check_deploy, deploy, manifest, paths, view, web
from lawbot.answer_format import cited_hits, concise
from lawbot.llm import build_prompt


class PathLLM(FakeLLM):
    """API 모델 id를 답으로 내는 가짜 답변 모델: 어떤 모델이 답했는지 확인용"""

    def stream(self, question, hits, system=None):
        self.args, self.system = (question, hits), system
        for c in ('【결론】', self.path, '\n【근거 조문】\n근로기준법 제26조(해고의 예고)\n【설명】\n- 설명'):
            yield types.SimpleNamespace(text=c, generation_tps=20.0, finish_reason='stop')


def make_app(env, llm=PathLLM):
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', llm)
    return service.load_app()


# ---- 레지스트리 ----
def test_profiles_define_fast_and_pro(env):
    p = deploy.get_profile()
    assert [m.key for m in p.models] == ['fast', 'pro']
    assert [m.label for m in p.models] == ['Lawbot Fast', 'Lawbot Pro']
    assert '정확' not in ' '.join(m.info for m in p.models)  # 검증 전 정확도 단정 금지
    assert [m.fmt for m in p.models] == ['full', 'concise']  # Fast 4항목, Pro 간결형
    assert p.models[0].system_file is None and p.models[1].system_file == paths.V4_SYSTEM_CONCISE
    assert [m.model for m in p.models] == ['lawbot-fast-v4', 'qwen3.8-27b-4bit'] and p.emb_model == 'lawbot-emb-v4'
    assert (p.articles, p.max_seq, p.system_file) == (paths.V4_ARTICLES, 2304, paths.V4_SYSTEM)


def test_default_profile_is_v4(env):
    p = deploy.get_profile()
    assert p.name == 'v4' and p.title == 'Lawbot'


@pytest.mark.parametrize('value', ['', 'v4', ' v4 '])
def test_profile_env_v4_or_empty_passes(env, value):
    env.setenv('LAWBOT_PROFILE', value)  # 예전 .env의 LAWBOT_PROFILE=v4가 남아 있어도 그대로 시작
    assert deploy.get_profile().name == 'v4'


@pytest.mark.parametrize('value', ['v1', 'v9'])
def test_profile_env_other_refused(env, value):
    env.setenv('LAWBOT_PROFILE', value)  # v1은 삭제됨: 조용히 v4로 바꾸지 않고 거부
    with pytest.raises(ValueError, match='LAWBOT_PROFILE은 v4 또는 v5'):
        deploy.get_profile()


def test_v4_without_assets_refuses_before_loading(env, tmp_path):
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / '없음.csv'))
    FakeRetriever.created = 0
    with pytest.raises(RuntimeError, match='필요한 파일이 없습니다'):
        make_app(env)
    assert FakeRetriever.created == 0  # 모델 서버에 접속하기 전에 거부


def test_profile_env_override(env):
    env.setenv('LAWBOT_PRO_MODEL', 'gpt-x')  # 클라우드 모델 이름도 그대로
    env.setenv('LAWBOT_EMB_MODEL', 'emb-x')
    env.setenv('LAWBOT_PRO_SYSTEM', 'data/x.txt')
    p = deploy.get_profile()
    assert p.models[1].model == 'gpt-x' and p.emb_model == 'emb-x'
    assert p.models[1].system_file == paths.ROOT / 'data/x.txt'  # 파일 경로는 레포 최상위 기준 절대 경로


# ---- 모델 관리 (불러오기·상주는 모델 서버) ----
def test_chat_models_create_once_and_check_server(env, model_server):
    a = make_app(env)
    assert a.models.llms == {}  # 답변 모델 클라이언트는 처음 쓸 때
    fast, load_s = a.models.get('fast')
    assert a.models.get('fast') == (fast, None) and load_s is None and fast.path == 'lawbot-fast-v4'
    assert fast.client is a.models.get('pro')[0].client  # 같은 생성 API 클라이언트
    with pytest.raises(KeyError):
        a.models.get('ultra')
    model_server.loaded = {'lawbot-fast-v4'}  # 서버 /v1/models의 loaded
    assert a.models.is_loaded('fast') and not a.models.is_loaded('pro')


def test_load_app_builds_clients(env):
    a = make_app(env)
    assert a.retriever.args[1] == {'max_seq_length': 2304}
    assert a.retriever.args[0][0].model == 'lawbot-emb-v4'  # 모델 서버의 임베딩 모델 (api_client.Embedder)
    full_system = paths.V4_SYSTEM.read_text(encoding='utf-8').strip()
    assert a.models.get('fast')[0].kw == {'system': full_system}  # Fast는 학습 때 쓴 4항목 system
    concise_system = paths.V4_SYSTEM_CONCISE.read_text(encoding='utf-8').strip()
    assert a.models.get('pro')[0].kw == {'system': concise_system}
    assert '【결론】' in concise_system and '【설명】' not in concise_system


def test_load_app_v4_reads_system_file(env, tmp_path):
    (tmp_path / 'system.txt').write_text('V4 SYSTEM\n', encoding='utf-8')
    (tmp_path / 'concise.txt').write_text('V4 CONCISE\n', encoding='utf-8')
    (tmp_path / 'v4.csv').write_text('law,article_id,title,deleted,text\n', encoding='utf-8')
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / 'v4.csv'))
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / 'system.txt'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / 'concise.txt'))
    a = make_app(env)
    assert a.models.get('fast')[0].kw == {'system': 'V4 SYSTEM'}
    assert a.models.get('pro')[0].kw == {'system': 'V4 CONCISE'}
    assert a.retriever.args[1] == {'max_seq_length': 2304}


def test_pro_system_missing_is_reported(env, tmp_path):
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음.txt'))
    assert run(make_app(env), '질문', 'pro', 'concise').error.startswith('Lawbot Pro 모델을 불러오지 못했습니다')


def test_format_follows_model_by_default(env):
    # 화면은 모델을 바꾸면 형식을 그 모델의 기본값으로 바꾼다 (frontend App.test). 서버는 모델별 기본 형식을 알려 준다
    cfg = web.config_data(make_app(env), None)
    assert cfg['default_model'] == 'fast'  # 기본 선택 Fast → 4항목
    assert {m['key']: m['format'] for m in cfg['models']} == {'fast': 'full', 'pro': 'concise'}


def test_missing_model_on_server_refuses_start(env, model_server):
    p = deploy.get_profile()
    model_server.available = [{'id': p.emb_model, 'type': 'embedding'}, {'id': 'lawbot-fast-v4', 'type': 'chat'}]
    with pytest.raises(RuntimeError, match=r'모델 서버에 필요한 모델이 없습니다: chat qwen3\.8-27b-4bit'):
        make_app(env)
    env.setenv('LAWBOT_MODELS', 'fast')  # 이 기기에서 Fast만 쓰면 Pro는 서버에 없어도 됨
    assert list(make_app(env).models.specs) == ['fast']


def test_server_model_load_failure_is_reported(env):
    from lawbot.api_client import ModelLoadError

    class ServerCannotLoad(PathLLM):
        def stream(self, question, hits, system=None):
            raise ModelLoadError('모델 서버 오류 FileNotFoundError: 모델 폴더 없음')
            yield

    r = run(make_app(env, llm=ServerCannotLoad), '질문', 'pro', 'full')
    assert r.error.startswith('Lawbot Pro 모델을 불러오지 못했습니다: qwen3.8-27b-4bit')


def test_chat_concurrency_setting(env):
    """웹앱 동시 질문 수: 기본 1, LAWBOT_CHAT_CONCURRENCY로 바꿈. 범위 밖은 시작 거부 (대기 동작은 test_web_integration)"""
    assert view.chat_concurrency() == 1
    env.setenv('LAWBOT_CHAT_CONCURRENCY', '4')
    assert view.chat_concurrency() == 4
    for bad in ('0', '17', 'x'):
        env.setenv('LAWBOT_CHAT_CONCURRENCY', bad)
        with pytest.raises(ValueError, match='LAWBOT_CHAT_CONCURRENCY는 1–16'):
            view.chat_concurrency()


def test_instance_name_in_title(env):
    # LAWBOT_INSTANCE: 운영/개발 화면을 탭 제목과 맨 위 제목으로 구분
    a = make_app(env)
    assert web.config_data(a, None)['title'] == 'Lawbot'
    env.setenv('LAWBOT_INSTANCE', '개발')
    assert web.config_data(a, None)['title'] == 'Lawbot · 개발'


# ---- 모델 선택 ----
def test_selection_is_per_request(env):
    # 요청마다 고른 모델이 답한다: 이전 선택이 다음 질문에 남지 않음
    a = make_app(env)
    names = {'fast': 'lawbot-fast-v4', 'pro': 'qwen3.8-27b-4bit'}
    for key in ('fast', 'pro', 'fast', 'pro', 'pro', 'fast'):
        r = run(a, '해고 예고?', key, 'full')
        assert names[key] in r.text and r.stats.startswith(f'Lawbot {key.title()}')


def test_fast_and_pro_get_same_context(env):
    a = make_app(env)
    src = [run(a, '수습 해고 예고?', k, 'full').sources for k in ('fast', 'pro')]
    assert src[0] == src[1]
    assert a.models.llms['fast'].args == a.models.llms['pro'].args == ('수습 해고 예고?', HITS)


def test_switch_shows_loading_then_answer(env, model_server):
    model_server.loaded = {'lawbot-fast-v4'}  # 서버에 Pro가 올라 있지 않음 (첫 요청 때 서버가 불러옴)
    a = make_app(env)
    r = run(a, '질문', 'pro', 'concise')
    events = r.events[1:]  # 첫 이벤트는 검색 중 문구 (test_status_steps)
    assert events[0] == ('sources', web.sources_data(HITS[:3]))  # 조문 카드는 로딩 중에도 먼저 보임
    assert events[1] == ('status', {'text': '⏳ Lawbot Pro 불러오는 중…'})
    assert events[2][0] == 'stats' and '모델 전환 대기' in events[2][1]['text']
    assert r.text.startswith('qwen3.8-27b-4bit')  # 그다음 답 (로딩 시간은 서버의 첫 토큰 시간에 포함)
    model_server.loaded = None
    again = run(a, '질문', 'pro', 'concise')
    assert not any(
        '불러오는 중' in d.get('text', '') for e, d in again.events if e == 'status'
    )  # 이미 올라 있으면 바로 답


def test_invalid_inputs(env):
    a = make_app(env)
    for body, shown in (
        ({'question': '   ', 'model': 'fast', 'format': 'concise'}, '질문을 입력해 주세요.'),
        ({'question': '질문', 'model': 'ultra', 'format': 'concise'}, '알 수 없는 답변 모델입니다.'),
        ({'question': '질문', 'model': 'fast', 'format': 'long'}, '알 수 없는 답변 형식입니다.'),
    ):
        with pytest.raises(HTTPException) as ei:
            web.valid_chat(a, body)
        assert (ei.value.status_code, ei.value.detail) == (400, shown)
    assert not hasattr(a.retriever, 'query')  # 검사에 걸린 질문은 검색하지 않음


def test_empty_and_truncated_answer(env):
    class Odd(FakeLLM):
        def stream(self, question, hits):
            yield types.SimpleNamespace(text=Odd.text, finish_reason=Odd.finish)

    Odd.text, Odd.finish = ' ', 'stop'
    a = make_app(env, llm=Odd)
    assert run(a, '질문', 'fast', 'concise').text == view.EMPTY
    Odd.text, Odd.finish = '【결론】길게', 'length'
    assert '⚠ 최대 길이에서 잘림' in run(a, '질문', 'fast', 'full').stats


# ---- 간결형 ----
FULL = (
    '【결론】\n연장근로수당은 통상임금의 50% 이상을 가산해 지급하는 것이 원칙입니다.\n\n'
    '【근거 조문】\n근로기준법 제56조(연장·야간 및 휴일 근로) 제1항\n\n'
    '【설명】\n- 제56조: 사용자는 ...\n\n【참고】\n- 위반 시 3년 이하의 징역 또는 3천만원 이하의 벌금'
)
H56 = [
    {'article_id': a, 'title': t, 'score': 0.9, 'doc': f'근로기준법 {a}({t})\n본문', 'law': '근로기준법'}
    for a, t in (('제56조', '연장·야간 및 휴일 근로'), ('제55조', '휴일'), ('제50조', '근로시간'))
]


def test_concise_shows_only_cited_articles():
    out = concise(FULL, H56)
    assert out == (
        '연장근로수당은 통상임금의 50% 이상을 가산해 지급하는 것이 원칙입니다.\n\n'
        '근거: 근로기준법 제56조(연장·야간 및 휴일 근로)'
    )
    assert '제55조' not in out and '벌금' not in out and '【' not in out


def test_concise_streaming_steps():
    assert concise('【결', H56, done=False) == ''  # 표시 기호를 쓰는 중
    partial = FULL[: FULL.index('제1항')]
    assert '근거:' not in concise(partial, H56, done=False)  # 근거 조문을 쓰는 중이면 붙이지 않음
    assert concise(FULL[: FULL.index('【설명】') + 5], H56, done=False).endswith('휴일 근로)')


def test_concise_refusal_and_fallbacks():
    refusal = (
        '【결론】\n제공된 조문으로는 확인할 수 없습니다.\n\n【근거 조문】\n해당 없음 (제공된 조문: 제56조, 제55조)'
    )
    assert concise(refusal, H56) == '제공된 조문으로는 확인할 수 없습니다.'
    assert concise('형식 없는 답변', H56) == '형식 없는 답변'  # 형식을 벗어난 답은 숨기지 않음
    outside = FULL.replace('제56조(연장·야간 및 휴일 근로) 제1항', '제56조, 제109조(벌칙)')
    assert concise(outside, H56).endswith('⚠ 검색된 조문에서 확인되지 않은 인용: 제109조')
    no_cite = '【결론】\n가산해야 합니다.\n\n【설명】\n- 설명'
    assert '⚠ 인용 조문 없음' in concise(no_cite, H56)


def test_cited_hits_v4_law_names():
    hits = [
        {'law': '근로기준법', 'article_id': '제2조', 'title': '정의'},
        {'law': '최저임금법', 'article_id': '제2조', 'title': '정의'},
        {'law': '최저임금법', 'article_id': '제6조', 'title': '최저임금의 효력'},
    ]
    used, unknown = cited_hits('최저임금법 제2조(정의), 제6조(최저임금의 효력)', hits)
    assert used == hits[1:] and unknown == []
    used, unknown = cited_hits('제2조(정의)', hits)  # 법령을 특정할 수 없음
    assert used == [] and unknown == ['제2조']
    assert cited_hits('근로기준법 제2조의2', hits) == ([], ['제2조의2'])  # 번호가 비슷한 조문과 구분


# ---- v4 검색 문서 형식 ----
class FakeEncoder:
    """api_client.Embedder 대역: 문서는 단위 벡터, 질문은 모두 1"""

    def encode(self, texts, input_type='document', max_seq_length=None):
        import numpy as np

        return (
            np.eye(len(texts), 8, dtype=np.float32)
            if input_type == 'document'
            else np.ones((len(texts), 8), np.float32)
        )


def test_retriever_v4_format(env, tmp_path):
    csv = tmp_path / 'v4.csv'
    pd.DataFrame(
        {
            'law': ['근로기준법', '최저임금법', '최저임금법'],
            'article_id': ['제2조', '제2조', '제3조'],
            'title': ['정의', '정의', '삭제'],
            'deleted': [0, 0, 1],
            'text': ['본문 A', '본문 B', ''],
        }
    ).to_csv(csv, index=False, encoding='utf-8-sig')
    r = retriever_mod.Retriever(FakeEncoder(), csv, max_seq_length=2304)
    assert r.docs == ['근로기준법 제2조(정의)\n본문 A', '최저임금법 제2조(정의)\n본문 B']
    assert r.max_seq_length == 2304 and r.starter == set()
    hits = r.search('정의', k=2)
    assert {(h['law'], h['article_id']) for h in hits} == {('근로기준법', '제2조'), ('최저임금법', '제2조')}
    assert [web.source_item(h)['title'] for h in hits].count('최저임금법 제2조 정의') == 1


def test_retriever_v4_article_no(env, tmp_path):
    """실제 v4 CSV: article_id에 법령명이 들어 있고 번호는 article_no. 법령명이 두 번 들어가면 안 됨"""
    csv = tmp_path / 'v4.csv'
    pd.DataFrame(
        {
            'law': ['근로기준법', '최저임금법'],
            'article_id': ['근로기준법 제26조', '최저임금법 제5조'],
            'article_no': ['제26조', '제5조'],
            'title': ['해고의 예고', '최저임금액'],
            'deleted': [0, 0],
            'text': ['본문 A', '본문 B'],
        }
    ).to_csv(csv, index=False, encoding='utf-8-sig')
    r = retriever_mod.Retriever(FakeEncoder(), csv, max_seq_length=2304)
    assert r.docs == ['근로기준법 제26조(해고의 예고)\n본문 A', '최저임금법 제5조(최저임금액)\n본문 B']
    hits = r.search('해고', k=2)
    assert {h['article_id'] for h in hits} == {'제26조', '제5조'}
    assert build_prompt(hits, 'q').count('근로기준법 제26조(해고의 예고)') == 1
    titles = ' '.join(web.source_item(h)['title'] for h in hits)
    assert '근로기준법 근로기준법' not in build_prompt(hits, 'q') + titles
    assert cited_hits('근로기준법 제26조(해고의 예고)', hits) == ([h for h in hits if h['law'] == '근로기준법'], [])


# ---- 배포 검사 ----
def test_check_deploy_reports_models_and_server(env, model_server):
    out = check_deploy.run()
    text = '\n'.join(f'{lv} {m}' for lv, m in out)
    assert 'OK 비삭제 조문 415개 (기대 415)' in text
    assert 'INFO 임베딩 lawbot-emb-v4' in text and 'INFO Lawbot Pro: 모델 qwen3.8-27b-4bit' in text
    assert 'OK 생성 API: 모델 서버' in text and 'OK 필요한 모델이 모델 서버에 모두 있음' in text
    model_server.available = []
    fails = [m for lv, m in check_deploy.run() if lv == 'FAIL']
    assert '모델 서버에 embedding 모델 lawbot-emb-v4 없음 (LAWBOT_SERVE_*와 맞추기)' in fails
    assert any('chat 모델 qwen3.8-27b-4bit' in m for m in fails)
    env.delenv('LAWBOT_MODEL_SERVER')
    assert any(lv == 'FAIL' and '모델 서버 주소 없음' in m for lv, m in check_deploy.run())
    assert all('test-only-model-token' not in m for _, m in out)  # 토큰 값은 출력하지 않음


def test_check_deploy_v4_placeholders_fail(env, tmp_path):
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / '없음.csv'))
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / '없음.txt'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음_concise.txt'))
    fails = [m for level, m in check_deploy.run() if level == 'FAIL']
    assert len(fails) == 3 and all('없' in m for m in fails)  # 조문 CSV·기본 system·Pro system


def test_check_deploy_article_number_format(env, tmp_path):
    csv = tmp_path / 'v4.csv'
    rows = {
        'law': ['근로기준법'] * 415,
        'article_id': [f'근로기준법 제{i}조' for i in range(1, 416)],
        'title': ['t'] * 415,
        'deleted': [0] * 415,
        'text': ['x'] * 415,
    }
    pd.DataFrame(rows).to_csv(csv, index=False, encoding='utf-8-sig')
    env.setenv('LAWBOT_V4_ARTICLES', str(csv))
    fails = [m for level, m in check_deploy.run() if level == 'FAIL']
    assert any("'제N조' 형식이 아님 415행" in m for m in fails)
    pd.DataFrame(rows | {'article_no': [f'제{i}조' for i in range(1, 416)]}).to_csv(
        csv, index=False, encoding='utf-8-sig'
    )
    out = check_deploy.run()
    assert ('OK', next(m for lv, m in out if '비삭제 조문' in m)) in out
    assert not any('형식이 아님' in m for lv, m in out)


# ---- 실행 설정 파일 (.env) ----
from lawbot import envfile


def test_env_example_is_complete_without_secrets(env):
    """레포의 .env.example: 형식이 맞고, 비밀번호·사용자명은 비어 있고, 모든 값이 앱 설정으로 읽힌다"""
    values = envfile.parse((paths.ROOT / '.env.example').read_text(encoding='utf-8'))
    assert values['LAWBOT_USER'] == '' and values['LAWBOT_PW'] == ''
    assert values['LAWBOT_RERANKER_ENABLED'] == '1' and values['LAWBOT_MODELS'] == 'pro'
    for k, v in values.items():
        env.setenv(k, v)
    p = deploy.get_profile()
    assert p.name == 'v5' and [m.key for m in p.models] == ['pro']
    assert p.models[0].model == 'qwen3.8-27b-4bit' and p.models[0].system_file == paths.V5_SYSTEM_CONCISE
    assert p.emb_model == 'lawbot-emb-v5b' and p.max_seq == 4352
    law, combined = deploy.article_paths(p.name)
    assert law.name == 'articles_v5.csv' and p.articles == combined == paths.DATA / 'v5/law_articles_v5_decree.csv'
    assert p.system_file == paths.V5_SYSTEM
    assert all(manifest.check(csv)['dataset_version'] == 'v5' for csv in (law, combined))
    assert deploy.reranker(p) == deploy.RerankerConfig('qwen3-reranker-4b', 10)
    assert deploy.decree(p) == deploy.DecreeConfig(0.9, 2, ('pro',), max_seq=5120)
    assert paths.V5_SYSTEM_CONCISE.is_file()  # Pro 간결형 system은 저장소에 있음
    from lawbot_model_server.config import parse_models

    served = {
        e.id: e.kind
        for kind, key in (('chat', 'CHAT'), ('embedding', 'EMBEDDING'), ('rerank', 'RERANK'))
        for e in parse_models(kind, values[f'LAWBOT_SERVE_{key}'])
    }
    needed = {p.emb_model: 'embedding', deploy.reranker(p).model: 'rerank'} | {m.model: 'chat' for m in p.models}
    assert needed.items() <= served.items()  # 웹앱이 고르는 모델 id를 모델 서버가 제공
    assert values['LAWBOT_MODEL_TOKEN'] == '' and values['LAWBOT_MODEL_SERVER_PORT'] == ''  # 비밀·포트는 비워 둠
    assert not [v for v in values.values() if v.isdigit() and len(v) >= 4]  # 포트 번호를 적지 않음


def test_env_dev_example_is_valid(env):
    """개발 웹앱 .env 예시: 모델 서버 설정 없이 웹앱 설정만, 검색·하위법령 설정이 유효"""
    values = envfile.parse((paths.ROOT / '.env.dev.example').read_text(encoding='utf-8'))
    assert not any(k.startswith('LAWBOT_SERVE_') or k == 'LAWBOT_MODEL_SERVER_PORT' for k in values)
    assert values['LAWBOT_USER'] == values['LAWBOT_PW'] == values['LAWBOT_MODEL_TOKEN'] == ''
    for k, v in values.items():
        env.setenv(k, v)
    p = deploy.get_profile()
    assert (p.name, p.emb_model, p.max_seq) == ('v5', 'lawbot-emb-v5b', 4352)
    assert deploy.reranker(p) == deploy.RerankerConfig('qwen3-reranker-4b', 10)
    assert deploy.decree(p) == deploy.DecreeConfig(0.9, 2, ('pro',), max_seq=5120)
    assert [m.model for m in p.models] == ['qwen3.8-27b-4bit']


def test_env_file_loading(env, tmp_path, capsys):
    f = tmp_path / 'lawbot.env'
    f.write_text(
        '# 주석\nexport LAWBOT_PROFILE=v4\nLAWBOT_USER = \'me\'\nLAWBOT_PW="p w#1"\n'
        'LAWBOT_MODELS=pro  # 끝 주석\nLAWBOT_INSTANCE=파일\n',
        encoding='utf-8',
    )
    f.chmod(0o600)
    env.setenv('LAWBOT_ENV_FILE', str(f))
    env.setenv('LAWBOT_INSTANCE', '셸')  # 셸 값이 우선
    assert envfile.load() == f
    import os

    assert (os.environ['LAWBOT_PROFILE'], os.environ['LAWBOT_USER'], os.environ['LAWBOT_PW']) == ('v4', 'me', 'p w#1')
    assert os.environ['LAWBOT_MODELS'] == 'pro' and os.environ['LAWBOT_INSTANCE'] == '셸'
    out = capsys.readouterr().out
    assert 'p w#1' not in out and '경고' not in out  # 값은 출력하지 않음
    f.chmod(0o644)
    env.delenv('LAWBOT_PW')
    envfile.load()
    assert 'chmod 600' in capsys.readouterr().out  # 비밀번호가 든 파일을 남이 읽을 수 있으면 경고


def test_env_file_errors_and_defaults(env, tmp_path):
    env.setenv('LAWBOT_ENV_FILE', str(tmp_path / '없음.env'))
    with pytest.raises(FileNotFoundError):  # 직접 지정한 파일이 없으면 오류
        envfile.load()
    env.setenv('LAWBOT_ENV_FILE', '')
    assert envfile.load() is None  # 빈 값이면 읽지 않음
    env.delenv('LAWBOT_ENV_FILE')
    env.setattr(envfile, 'DEFAULT', tmp_path / '.env')
    assert envfile.load() is None  # 기본 .env가 없으면 아무것도 안 함
    for bad in ('LAWBOT_PW', 'GRADIO_SERVER_NAME=0.0.0.0', 'PATH=/tmp', 'LAWBOT_ENV_FILE=x'):
        with pytest.raises(ValueError, match='1번째 줄'):
            envfile.parse(bad)


def test_models_selection(env):
    env.setenv('LAWBOT_MODELS', 'pro')
    assert [m.key for m in deploy.get_profile().models] == ['pro']
    env.setenv('LAWBOT_MODELS', 'pro, fast')
    assert [m.key for m in deploy.get_profile().models] == ['pro', 'fast']  # 첫 번째가 기본 선택
    for bad in ('ultra', 'pro,pro'):
        env.setenv('LAWBOT_MODELS', bad)
        with pytest.raises(ValueError, match='LAWBOT_MODELS'):
            deploy.get_profile()


def test_pro_only_app(env):
    env.setenv('LAWBOT_MODELS', 'pro')
    a = make_app(env)
    assert list(a.models.specs) == ['pro']  # Fast는 쓰지 않음 (모델 서버에 없어도 됨)
    assert [m.key for m in a.profile.disabled] == ['fast']
    cfg = web.config_data(a, None)
    assert cfg['default_model'] == 'pro' and cfg['models'][0]['format'] == 'concise'
    assert [m['key'] for m in cfg['models']] == ['pro'] and [m['key'] for m in cfg['disabled']] == ['fast']  # 비활성
    with pytest.raises(HTTPException, match='사용하지 않는 모델'):  # 화면은 버튼을 막고, 서버도 거부
        web.valid_chat(a, {'question': '질문', 'model': 'fast', 'format': 'full'})
    assert run(a, '해고 예고?', 'pro', 'concise').stats.startswith('Lawbot Pro')
    assert any('사용 안 함' in m for lv, m in check_deploy.run())


def test_full_format_uses_four_part_system(env):
    # 간결형이 기본인 Pro에서 '전체'를 고르면 4항목 system으로 생성 (간결형 출력이 그대로 나오지 않게)
    a = make_app(env)
    run(a, '질문', 'pro', 'full')
    assert a.models.llms['pro'].system == a.full_system
    run(a, '질문', 'pro', 'concise')
    assert a.models.llms['pro'].system is None  # 간결형: Pro 기본(간결형) system
    for f in ('full', 'concise'):  # Fast(4항목 학습)는 system을 바꾸지 않음
        run(a, '질문', 'fast', f)
        assert a.models.llms['fast'].system is None
    list(service.ask(a, '질문', 'pro'))  # fmt 없이 (평가·벤치): 모델 기본
    assert a.models.llms['pro'].system is None


def test_full_system_is_profile_system_file(env):
    a = make_app(env)
    assert a.full_system == paths.V4_SYSTEM.read_text(encoding='utf-8').strip()
    assert '【설명】' in a.full_system and '【설명】' not in paths.V4_SYSTEM_CONCISE.read_text(encoding='utf-8')


# ---- 근거 조문 표시 (B5) ----
H3 = [
    {
        'law': '근로기준법',
        'article_id': a,
        'title': t,
        'score': 0.9 - i / 10,
        'doc': f'근로기준법 {a}({t})\n{t} 본문 <개정 2021.1.5>',
    }
    for i, (a, t) in enumerate(
        (('제26조', '해고의 예고'), ('제27조', '해고사유 등의 서면통지'), ('제28조', '부당해고등의 구제신청'))
    )
]


def v4_chat(env, text):
    """검색 결과 H3과 고정 답변을 내는 v4 앱 (가짜 모델)"""

    class Ret(FakeRetriever):
        def search(self, query, k=3):
            return H3[:k]

    class Bot(FakeLLM):
        def stream(self, question, hits, system=None):
            for c in (text[:4], text[4:]):
                yield types.SimpleNamespace(text=c, generation_tps=10.0, finish_reason='stop')

    env.setattr(service, 'Retriever', Ret)
    env.setattr(service, 'LLM', Bot)
    env.setattr(service.manifest, 'check', lambda articles: None)  # tmp 조문 CSV
    env.setenv('LAWBOT_PROFILE', 'v4')
    return service.load_app()


def groups(sources):
    """[(묶음 제목, 펼침, [조문 번호])]"""
    return [(g['label'], g['open'], [i['title'].split()[1] for i in g['items']]) for g in sources['groups']]


@pytest.fixture
def v4_files(env, tmp_path):
    (tmp_path / 'v4.csv').write_text('law,article_id,title,deleted,text\n', encoding='utf-8')
    (tmp_path / 's.txt').write_text('S', encoding='utf-8')
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / 'v4.csv'))
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / 's.txt'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / 's.txt'))
    env.setenv('LAWBOT_MODELS', 'pro')
    return env


def test_sources_split_cited_and_retrieved(v4_files):
    a = v4_chat(
        v4_files,
        '【결론】\n30일 전에 예고해야 합니다.\n\n【근거 조문】\n근로기준법 제27조(해고사유 등의 서면통지), 제26조',
    )
    first, final = [d for e, d in run(a, '해고 예고?', 'pro', 'concise').events if e == 'sources']
    assert groups(first) == [(None, True, ['제26조', '제27조', '제28조'])]  # 생성 중에는 세 개 모두 펼침
    assert groups(final) == [
        (view.CITED_LABEL, True, ['제26조', '제27조']),  # 인용한 조문은 펼쳐서 먼저 (검색 순서대로)
        ('검색만 된 조문', False, ['제28조']),  # 접힌 카드
    ]
    cited, rest = final['groups']
    assert cited['items'][0]['body'] == '해고의 예고 본문 <개정 2021.1.5>'  # 제목 줄은 빼고 본문만
    assert rest['items'][0]['body'] == '부당해고등의 구제신청 본문 <개정 2021.1.5>'  # 펼치면 본문


def test_sources_for_refusal_and_uncited(v4_files):
    a = v4_chat(v4_files, '【결론】\n제공된 조문에서 근거를 찾을 수 없습니다.\n\n【근거 조문】\n근로기준법 제26조')
    r = run(a, '제주도 여행 코스?', 'pro', 'concise')
    assert r.text == '제공된 조문에서 근거를 찾을 수 없습니다.'  # 거절: 결론만
    assert groups({'groups': r.sources}) == [
        ('검색됐지만 답변에 쓰지 않은 조문', False, ['제26조', '제27조', '제28조'])
    ]
    a = v4_chat(v4_files, '【결론】\n예고해야 합니다.')
    r = run(a, '해고?', 'pro', 'concise')
    assert groups({'groups': r.sources}) == [(None, True, ['제26조', '제27조', '제28조'])]  # 인용이 없으면 모두 펼침


def test_full_format_warns_outside_citation(v4_files):
    raw = '【결론】\n예고해야 합니다.\n\n【근거 조문】\n근로기준법 제26조, 근로기준법 제109조\n\n【설명】\n- 설명'
    r = run(v4_chat(v4_files, raw), '해고?', 'pro', 'full')
    assert r.text == raw + '\n\n⚠ 검색된 조문에서 확인되지 않은 인용: 제109조'  # 원문은 그대로, 아래에 경고
    assert r.sources[0]['label'] == view.CITED_LABEL
    assert render_full(raw.replace(', 근로기준법 제109조', '')) == raw.replace(', 근로기준법 제109조', '')
    assert render_full(raw, done=False) == raw  # 생성 중에는 경고하지 않음


def render_full(raw, done=True):
    from lawbot.answer_format import render

    return render(raw, H3, 'full', done)


def test_status_steps(env):
    events = run(make_app(env), '질문', 'fast', 'concise').events
    assert events[0] == ('status', {'text': '⏳ 조문 검색 중…'})  # 검색 결과가 오기 전
    assert all(d['text'].strip() for e, d in events if e in ('answer', 'status', 'done'))  # 빈 화면 단계 없음
    statuses = [d['text'] for e, d in events if e == 'status']
    assert all(t.startswith('⏳') for t in statuses)  # 처리 중 문구는 '⏳'로 시작 (첫 글자 측정에서 제외)
    stats = [d['text'] for e, d in events if e == 'stats']
    assert '✍️ 답변 생성 중…' in stats[0] and '✍️' not in stats[-1]  # 생성 중 / 완료
    a = make_app(env)
    a.reranker = types.SimpleNamespace(candidates=3)
    assert view.searching_stats(a) == '⏳ 조문 검색 → 리랭킹 중…'


def test_writing_placeholder_while_marker():
    """간결형은 표시 기호를 쓰는 동안 빈 화면 대신 처리 중 문구 ('⏳': 첫 글자 측정에서 제외)"""
    assert view.WRITING.startswith('⏳')


def test_data_notice(env):
    a = make_app(env)  # data/v4/manifest.json
    assert view.data_notice(a) == '법령 기준일 2026-10-20 (데이터 v4)'
    assert web.config_data(a, None)['notice'] == view.data_notice(a)  # 화면 맨 위 줄


def test_v5_profile_uses_v5b_and_v5_systems(env):
    env.setenv('LAWBOT_PROFILE', 'v5')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    p = deploy.get_profile()
    assert p.articles.name == 'law_articles_v5_decree.csv'
    assert p.emb_model == 'lawbot-emb-v5b'
    assert p.max_seq == 4352 and deploy.decree(p).max_seq == 5120
    assert [(m.key, m.model, m.fmt) for m in p.models] == [('pro', 'qwen3.8-27b-4bit', 'concise')]
    concise_system = service.system_text(p, p.models[0])
    full = p.system_file.read_text(encoding='utf-8')
    assert concise_system.splitlines()[0] == full.splitlines()[0]
    assert '근로소득 세금, 4대보험' in full
    assert all(section in full for section in ('【결론】', '【근거 조문】', '【설명】', '【참고】'))
    service.check_profile_files(p)
    checks = []
    check_deploy.check_decree(p, checks)
    assert all(status != 'FAIL' for status, _ in checks)
    assert any('articles_v5.csv와 같음' in message for _, message in checks)


def test_v5_requires_manifest_before_contacting_server(env, tmp_path):
    env.setenv('LAWBOT_PROFILE', 'v5')
    csv = tmp_path / 'articles_v5.csv'
    csv.write_bytes((paths.DATA / 'v5' / csv.name).read_bytes())
    env.setenv('LAWBOT_V5_ARTICLES', str(csv))
    with pytest.raises(RuntimeError, match='매니페스트 없음'):
        service.check_profile_files(deploy.get_profile())
