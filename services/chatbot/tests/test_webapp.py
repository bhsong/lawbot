# tests/test_webapp.py  →  Fast/Pro 모델 선택·전환, 간결형 답변, v4 검색 형식, 배포 검사 (가짜 모델, 모델 파일 불필요)
import types

import pandas as pd
import pytest
from fastapi import HTTPException

import lawbot.retriever as retriever_mod
import lawbot.service as service
from chat_support import run
from fakes import HITS, FakeLLM, FakeOpenAI, FakeRetriever
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


def test_v4_profile_defines_pro(env):
    p = deploy.get_profile()
    assert (p.model.key, p.model.label, p.model.model) == ('pro', 'Lawbot Pro', 'qwen3.8-27b-4bit')
    assert p.emb_model == 'lawbot-emb-v4'
    assert (p.articles, p.max_seq, p.system_file) == (paths.V4_ARTICLES, 2304, paths.V4_SYSTEM_CONCISE)


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
    assert p.model.model == 'gpt-x' and p.emb_model == 'emb-x'
    assert p.system_file == paths.ROOT / 'data/x.txt'  # 파일 경로는 레포 최상위 기준 절대 경로


def test_pro_client_is_created_lazily_and_reused(env):
    env.setattr(FakeLLM, 'created', 0)
    a = make_app(env, llm=FakeLLM)
    assert FakeLLM.created == 0
    run(a, '첫 질문')
    run(a, '두 번째 질문')
    assert FakeLLM.created == 1 and a.llm.path == a.profile.model.model


def test_load_app_builds_clients(env):
    a = make_app(env)
    assert a.retriever.args[1] == {'max_seq_length': 2304}
    assert a.retriever.args[0][0].model == 'lawbot-emb-v4'  # 모델 서버의 임베딩 모델 (api_client.Embedder)
    concise_system = paths.V4_SYSTEM_CONCISE.read_text(encoding='utf-8').strip()
    assert a.llm.kw == {'system': concise_system}
    assert '【결론】' in concise_system and '【설명】' not in concise_system


def test_load_app_v4_reads_system_file(env, tmp_path):
    (tmp_path / 'concise.txt').write_text('V4 CONCISE\n', encoding='utf-8')
    (tmp_path / 'v4.csv').write_text('law,article_id,title,deleted,text\n', encoding='utf-8')
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / 'v4.csv'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / 'concise.txt'))
    a = make_app(env)
    assert a.llm.kw == {'system': 'V4 CONCISE'}
    assert a.retriever.args[1] == {'max_seq_length': 2304}


def test_pro_system_missing_is_reported(env, tmp_path):
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음.txt'))
    with pytest.raises(RuntimeError, match=r'필요한 파일이 없습니다: .*없음.txt'):
        make_app(env)


def test_missing_model_on_generation_server_refuses_start(env, model_server):
    FakeOpenAI.available = [
        {
            'id': 'different-chat-model',
            'object': 'model',
            'revision': 'chat-revision',
            'mlx_lm_version': '0.31.2',
            'max_concurrent': 1,
        }
    ]
    with pytest.raises(RuntimeError, match=r'서버에 필요한 모델이 없습니다: chat qwen3\.8-27b-4bit'):
        make_app(env)


def test_server_model_load_failure_is_reported(env):
    from lawbot.api_client import ModelLoadError

    class ServerCannotLoad(PathLLM):
        def stream(self, question, hits, system=None):
            raise ModelLoadError('모델 서버 오류 FileNotFoundError: 모델 폴더 없음')
            yield

    r = run(make_app(env, llm=ServerCannotLoad), '질문')
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


def test_pro_answers_successive_questions_with_retrieved_context(env):
    a = make_app(env)
    for question in ('해고 예고?', '수습 해고 예고?'):
        answer = run(a, question)
        assert a.profile.model.model in answer.text and answer.stats.startswith('Lawbot Pro')
        assert a.llm.args == (question, HITS)


def test_chat_does_not_wait_for_model_residency(env, model_server):
    a = make_app(env)
    r = run(a, '질문')
    assert r.events[1] == ('sources', web.sources_data(HITS[:3]))
    assert r.text.startswith('qwen3.8-27b-4bit')
    assert not any(
        '불러오는 중' in data.get('text', '') or '모델 전환' in data.get('text', '')
        for event, data in r.events
        if event in ('status', 'stats')
    )


def test_invalid_inputs(env):
    a = make_app(env)
    for body, shown in (
        ({'question': '   '}, '질문을 입력해 주세요.'),
        ({'question': 'x' * (web.MAX_QUESTION + 1)}, f'질문이 너무 깁니다 (최대 {web.MAX_QUESTION}자).'),
    ):
        with pytest.raises(HTTPException) as ei:
            web.valid_chat(body)
        assert (ei.value.status_code, ei.value.detail) == (400, shown)
    assert not hasattr(a.retriever, 'query')  # 검사에 걸린 질문은 검색하지 않음


def test_empty_and_truncated_answer(env):
    class Odd(FakeLLM):
        def stream(self, question, hits):
            yield types.SimpleNamespace(text=Odd.text, finish_reason=Odd.finish)

    Odd.text, Odd.finish = ' ', 'stop'
    a = make_app(env, llm=Odd)
    assert run(a, '질문').text == view.EMPTY
    Odd.text, Odd.finish = '【결론】길게', 'length'
    truncated = run(a, '질문')
    assert '⚠ 최대 길이에서 잘림' in truncated.stats
    assert truncated.text == '' and '길이' in truncated.done['notice']


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
    assert concise(outside, H56).endswith('제109조⚠확인되지 않은 인용')
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
    assert 'OK 생성 API: http://127.0.0.1:65534/v1' in text and 'OK 필요한 모델이 각 서버에 모두 있음' in text
    model_server.available = []
    fails = [m for lv, m in check_deploy.run() if lv == 'FAIL']
    assert '모델 서버에 embedding 모델 lawbot-emb-v4 없음' in fails
    assert not any('chat 모델' in message for message in fails)
    FakeOpenAI.available = []
    assert any(
        level == 'FAIL' and '생성 API에 chat 모델 qwen3.8-27b-4bit 없음' in message
        for level, message in check_deploy.run()
    )
    env.delenv('LAWBOT_MODEL_SERVER')
    assert any(lv == 'FAIL' and '모델 서버 주소 없음' in m for lv, m in check_deploy.run())
    assert all('test-only-model-token' not in m for _, m in out)  # 토큰 값은 출력하지 않음


def test_check_deploy_v4_placeholders_fail(env, tmp_path):
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / '없음.csv'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음.txt'))
    fails = [m for level, m in check_deploy.run() if level == 'FAIL']
    assert len(fails) == 2 and all('없' in m for m in fails)


@pytest.mark.parametrize('content', ['', ' \n\t'])
def test_check_deploy_reports_empty_system_and_continues(env, tmp_path, content):
    system = tmp_path / 'empty-system.txt'
    system.write_text(content, encoding='utf-8')
    env.setenv('LAWBOT_PRO_SYSTEM', str(system))
    out = check_deploy.run()
    failure = next(i for i, (level, message) in enumerate(out) if level == 'FAIL' and system.name in message)
    assert any(
        level == 'OK' and '필요한 모델이 각 서버에 모두 있음' in message for level, message in out[failure + 1 :]
    )


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
    assert values['LAWBOT_RERANKER_ENABLED'] == '1' and values['LAWBOT_DECREE_ENABLED'] == '1'
    for k, v in values.items():
        env.setenv(k, v)
    p = deploy.get_profile()
    assert p.name == 'v5' and p.model.key == 'pro'
    assert p.model.model == 'qwen3.8-27b-4bit'
    assert p.emb_model == 'lawbot-emb-v6' and p.max_seq == 4352
    law, combined = deploy.article_paths(p.name)
    assert law.name == 'articles_v5.csv' and p.articles == combined == paths.DATA / 'v5/law_articles_v5_decree.csv'
    assert p.system_file == paths.V5_SYSTEM_CONCISE
    assert not (paths.DATA / 'v5/system_v5.txt').exists()  # full-format system removed with the format
    assert all(manifest.check(csv)['dataset_version'] == 'v5' for csv in (law, combined))
    assert deploy.reranker(p) == deploy.RerankerConfig('qwen3-reranker-4b', 10)
    assert deploy.decree(p) == deploy.DecreeConfig(0.9, 2, max_seq=5120)
    assert paths.V5_SYSTEM_CONCISE.is_file()  # Pro 간결형 system은 저장소에 있음
    from lawbot_model_server.config import parse_models

    served = [
        {'id': entry.id, 'type': entry.kind}
        for kind, key in (('embedding', 'EMBEDDING'), ('rerank', 'RERANK'))
        for entry in parse_models(kind, values[f'LAWBOT_SERVE_{key}'])
    ]
    assert service.missing_models(served, p, deploy.reranker(p)) == []
    from conftest import TEST_LLM_TOKEN, TEST_TOKEN
    from lawbot.settings import load

    cfg = load(
        values
        | {
            'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:65535',
            'LAWBOT_MODEL_TOKEN': TEST_TOKEN,
            'LAWBOT_LLM_BASE_URL': 'http://127.0.0.1:65534/v1',
            'LAWBOT_LLM_API_KEY': TEST_LLM_TOKEN,
        }
    )
    assert cfg.model_server != cfg.llm_url.removesuffix('/v1')
    assert cfg.llm_key == TEST_LLM_TOKEN and not cfg.llm_external
    assert values['LAWBOT_MODEL_TOKEN'] == values['LAWBOT_LLM_API_KEY'] == ''
    assert values['LAWBOT_MODEL_SERVER_PORT'] == ''
    assert not [v for v in values.values() if v.isdigit() and len(v) >= 4]  # 포트 번호를 적지 않음


def test_env_dev_example_is_valid(env):
    """개발 웹앱 .env 예시: 모델 서버 설정 없이 웹앱 설정만, 검색·하위법령 설정이 유효"""
    values = envfile.parse((paths.ROOT / '.env.dev.example').read_text(encoding='utf-8'))
    assert not any(k.startswith('LAWBOT_SERVE_') or k == 'LAWBOT_MODEL_SERVER_PORT' for k in values)
    assert values['LAWBOT_USER'] == values['LAWBOT_PW'] == values['LAWBOT_MODEL_TOKEN'] == ''
    for k, v in values.items():
        env.setenv(k, v)
    p = deploy.get_profile()
    assert (p.name, p.emb_model, p.max_seq) == ('v5', 'lawbot-emb-v6', 4352)
    assert deploy.reranker(p) == deploy.RerankerConfig('qwen3-reranker-4b', 10)
    assert deploy.decree(p) == deploy.DecreeConfig(0.9, 2, max_seq=5120)
    assert p.model.model == 'qwen3.8-27b-4bit'


def test_env_file_loading(env, tmp_path, capsys):
    f = tmp_path / 'lawbot.env'
    f.write_text(
        '# 주석\nexport LAWBOT_PROFILE=v4\nLAWBOT_USER = \'me\'\nLAWBOT_PW="p w#1"\n'
        'LAWBOT_RERANKER_ENABLED=1  # 끝 주석\nLAWBOT_INSTANCE=파일\n',
        encoding='utf-8',
    )
    f.chmod(0o600)
    env.setenv('LAWBOT_ENV_FILE', str(f))
    env.setenv('LAWBOT_INSTANCE', '셸')  # 셸 값이 우선
    assert envfile.load() == f
    import os

    assert (os.environ['LAWBOT_PROFILE'], os.environ['LAWBOT_USER'], os.environ['LAWBOT_PW']) == ('v4', 'me', 'p w#1')
    assert os.environ['LAWBOT_RERANKER_ENABLED'] == '1' and os.environ['LAWBOT_INSTANCE'] == '셸'
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


def test_generation_does_not_override_the_pro_system_per_request(env):
    a = make_app(env)
    run(a, '질문')
    assert a.llm.system is None


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
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / 's.txt'))
    return env


def test_sources_split_cited_and_retrieved(v4_files):
    a = v4_chat(
        v4_files,
        '【결론】\n30일 전에 예고해야 합니다.\n\n【근거 조문】\n근로기준법 제27조(해고사유 등의 서면통지), 제26조',
    )
    first, final = [d for e, d in run(a, '해고 예고?').events if e == 'sources']
    assert groups(first) == [(None, True, ['제26조', '제27조', '제28조'])]  # 생성 중에는 세 개 모두 펼침
    assert groups(final) == [
        (view.CITED_LABEL, True, ['제26조', '제27조']),  # 인용한 조문은 펼쳐서 먼저 (검색 순서대로)
        ('검색만 된 조문', False, ['제28조']),  # 접힌 카드
    ]
    cited, rest = final['groups']
    assert cited['items'][0]['body'] == '해고의 예고 본문 <개정 2021.1.5>'  # 제목 줄은 빼고 본문만
    assert rest['items'][0]['body'] == '부당해고등의 구제신청 본문 <개정 2021.1.5>'  # 펼치면 본문


def test_sources_for_refusal(v4_files):
    a = v4_chat(v4_files, '【결론】\n제공된 조문에서 근거를 찾을 수 없습니다.\n\n【근거 조문】\n근로기준법 제26조')
    r = run(a, '제주도 여행 코스?')
    assert r.text == '제공된 조문에서 근거를 찾을 수 없습니다.'  # 거절: 결론만
    assert groups({'groups': r.sources}) == [
        ('검색됐지만 답변에 쓰지 않은 조문', False, ['제26조', '제27조', '제28조'])
    ]


@pytest.mark.parametrize('cites', ['', '최저임금법 제26조'])
def test_answer_without_verified_citations_is_withheld(v4_files, cites):
    raw = f'【결론】\n예고해야 합니다.\n\n【근거 조문】\n{cites}'
    remembered = []
    r = run(v4_chat(v4_files, raw), '해고?', remember=remembered.append)
    assert any('예고해야' in d['text'] for e, d in r.events if e == 'answer')
    assert r.text == '' and '근거' in r.done['notice'] and r.done['turn_id'] is None
    assert groups({'groups': r.sources}) == [(None, True, ['제26조', '제27조', '제28조'])]
    assert remembered == []


def test_answer_with_mixed_citations_keeps_verified_sources_and_feedback(v4_files):
    raw = '【결론】\n예고해야 합니다.\n\n【근거 조문】\n근로기준법 제26조, 근로기준법 제109조'
    remembered = []
    r = run(v4_chat(v4_files, raw), '해고?', remember=remembered.append)
    assert 'notice' not in r.done
    assert '근거: 근로기준법 제26조(해고의 예고)' in r.text
    assert '근로기준법 제109조⚠확인되지 않은 인용' in r.text
    assert groups({'groups': r.sources})[0] == (view.CITED_LABEL, True, ['제26조'])
    assert len(remembered) == 1


def test_status_steps(env):
    events = run(make_app(env), '질문').events
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


def test_v5_profile_uses_v6_and_v5_systems(env):
    env.setenv('LAWBOT_PROFILE', 'v5')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    p = deploy.get_profile()
    assert p.articles.name == 'law_articles_v5_decree.csv'
    assert p.emb_model == 'lawbot-emb-v6'
    assert p.max_seq == 4352 and deploy.decree(p).max_seq == 5120
    assert (p.model.key, p.model.model) == ('pro', 'qwen3.8-27b-4bit')
    system = service.system_text(p)
    assert p.system_file == paths.V5_SYSTEM_CONCISE
    assert '근로소득 세금, 4대보험' in system and '【결론】' in system and '【설명】' not in system
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
