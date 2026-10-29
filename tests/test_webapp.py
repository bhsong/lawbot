# tests/test_webapp.py  →  Fast/Pro 모델 선택·전환, 간결형 답변, v4 검색 형식, 배포 검사 (가짜 모델, 모델 파일 불필요)
import json
import struct
import types
import pandas as pd
import pytest
import lawbot.app as app_mod
import lawbot.retriever as retriever_mod
import lawbot.service as service
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot import check_deploy, deploy, paths
from lawbot.answer_format import cited_hits, concise
from lawbot.llm import build_prompt_v4

class PathLLM(FakeLLM):
    """모델 폴더 이름을 답으로 내는 가짜 sLLM: 어떤 모델이 답했는지 확인용"""
    def stream(self, question, hits, system=None):
        self.args, self.system = (question, hits), system
        for c in ('【결론】', self.path.name, '\n【근거 조문】\n근로기준법 제26조(해고의 예고)\n【설명】\n- 설명'):
            yield types.SimpleNamespace(text=c, generation_tps=20.0, finish_reason='stop')

def make_app(env, resident=2, llm=PathLLM, early_stop=False):
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', llm)
    env.setenv('LAWBOT_MAX_RESIDENT', str(resident))
    if early_stop:
        env.setenv('LAWBOT_EARLY_STOP', '1')
    return service.load_app()

# ---- 레지스트리 ----
def test_profiles_define_fast_and_pro(env):
    for name in ('v1', 'v4'):
        p = deploy.get_profile(name)
        assert [m.key for m in p.models] == ['fast', 'pro']
        assert [m.label for m in p.models] == ['Lawbot Fast', 'Lawbot Pro']
        assert all(m.path.is_absolute() for m in p.models)
        assert '정확' not in ' '.join(m.info for m in p.models)     # 검증 전 정확도 단정 금지
        assert [m.fmt for m in p.models] == ['full', 'concise']     # Fast 4항목, Pro 간결형
        assert p.models[0].system_file is None and p.models[1].system_file is not None
    v1, v4 = deploy.get_profile('v1'), deploy.get_profile('v4')
    assert (v1.articles, v1.emb, v1.max_seq, v1.doc_style) == (paths.ARTICLES, paths.EMB, 512, 'v1')
    assert v1.models[0].path == paths.LLM_4BIT and v1.system_file is None
    assert v1.models[1].system_file == paths.SYSTEM_V1_CONCISE and v4.models[1].system_file == paths.V4_SYSTEM_CONCISE
    assert (v4.max_seq, v4.doc_style, v4.system_file) == (2304, 'v4', paths.V4_SYSTEM)
    assert {v4.articles, v4.emb, v4.models[0].path} & {v1.articles, v1.emb, v1.models[0].path} == set()

def test_default_profile_is_v4(env):
    env.delenv('LAWBOT_PROFILE')
    p = deploy.get_profile()
    assert p.name == 'v4' and p.title == '노동법 챗봇'

def test_v4_without_assets_refuses_before_loading(env, tmp_path):
    env.delenv('LAWBOT_PROFILE')
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / '없음.csv'))
    FakeRetriever.created = 0
    with pytest.raises(RuntimeError, match='LAWBOT_PROFILE=v1'):
        make_app(env)
    assert FakeRetriever.created == 0                   # 검색기·모델을 올리기 전에 거부

def test_profile_env_override_is_absolute(env):
    env.setenv('LAWBOT_PRO_PATH', 'models/qwen3_14b_qlora')
    assert deploy.get_profile('v1').models[1].path == paths.ROOT / 'models/qwen3_14b_qlora'
    env.setenv('LAWBOT_PROFILE', 'v9')
    with pytest.raises(ValueError, match='LAWBOT_PROFILE'):
        deploy.get_profile()
    env.setenv('LAWBOT_MAX_RESIDENT', '3')
    with pytest.raises(ValueError):
        deploy.max_resident()

# ---- 모델 관리 ----
def test_manager_lru_one_resident():
    loads = []
    specs = deploy.get_profile('v1').models
    m = service.ModelManager(specs, lambda s: loads.append(s.key) or s.key, max_resident=1)
    assert m.get('fast')[0] == 'fast' and m.get('fast')[1] is None
    llm, sec = m.get('pro')
    assert llm == 'pro' and sec is not None and list(m.loaded) == ['pro']   # Fast를 내린 뒤 Pro
    m.get('fast')
    assert loads == ['fast', 'pro', 'fast']
    with pytest.raises(KeyError):
        m.get('ultra')

def test_load_app_preloads_both_when_resident_two(env):
    a = make_app(env, resident=2)
    assert list(a.models.loaded) == ['fast', 'pro']
    assert a.retriever.args[1] == {'max_seq_length': 512, 'doc_style': 'v1'}
    assert a.models.loaded['fast'].kw == {'prompt_style': 'v1'}      # Fast는 llm.SYSTEM(v1 4섹션) 그대로
    concise_system = paths.SYSTEM_V1_CONCISE.read_text(encoding='utf-8').strip()
    assert a.models.loaded['pro'].kw == {'prompt_style': 'v1', 'system': concise_system}
    assert '【결론】' in concise_system and '【근거 조문】' in concise_system and '【설명】' not in concise_system

def test_load_app_v4_reads_system_file(env, tmp_path):
    (tmp_path / 'system.txt').write_text('V4 SYSTEM\n', encoding='utf-8')
    (tmp_path / 'concise.txt').write_text('V4 CONCISE\n', encoding='utf-8')
    (tmp_path / 'v4.csv').write_text('law,article_id,title,deleted,text\n', encoding='utf-8')
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / 'v4.csv'))
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / 'system.txt'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / 'concise.txt'))
    a = make_app(env, resident=2)
    assert a.models.loaded['fast'].kw == {'prompt_style': 'v4', 'system': 'V4 SYSTEM'}
    assert a.models.loaded['pro'].kw == {'prompt_style': 'v4', 'system': 'V4 CONCISE'}
    assert a.retriever.args[1] == {'max_seq_length': 2304, 'doc_style': 'v4'}

def test_pro_system_missing_is_reported(env, tmp_path):
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음.txt'))
    chat = app_mod.make_chat(make_app(env, resident=1))
    with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
        list(chat('질문', [], 'pro', 'concise'))

def test_format_follows_model_by_default(env):
    a = make_app(env)
    demo = app_mod.build_demo(a)
    radios = {c.label: c for c in demo.blocks.values() if isinstance(c, app_mod.gr.Radio)}
    assert radios['답변 형식'].value == 'full'                       # 기본 선택 Fast → 4항목
    follow = app_mod.default_fmt(a)
    assert (follow('fast'), follow('pro')) == (('fast', 'full'), ('pro', 'concise'))

def test_pro_preload_failure_keeps_fast(env, capsys):
    class ProMissing(PathLLM):
        def __init__(self, path, **kw):
            if path.name == paths.PRO_BASE.name:
                raise ValueError('Repo id must be in the form')     # mlx_lm.load가 없는 절대 경로에 내는 오류
            super().__init__(path, **kw)
    a = make_app(env, resident=2, llm=ProMissing)
    assert list(a.models.loaded) == ['fast']
    assert 'Lawbot Pro 미리 불러오기 실패' in capsys.readouterr().out
    chat = app_mod.make_chat(a)
    with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
        list(chat('질문', [], 'pro', 'full'))
    assert list(chat('질문', [], 'fast', 'full'))[-1][0].startswith('【결론】sllm_mlx_4bit')

def test_instance_name_in_title(env):
    # LAWBOT_INSTANCE: 운영/개발 화면을 탭 제목과 맨 위 제목으로 구분
    a = make_app(env)
    assert app_mod.build_demo(a).title == '근로기준법 챗봇'
    env.setenv('LAWBOT_INSTANCE', '개발')
    demo = app_mod.build_demo(a)
    assert demo.title == '근로기준법 챗봇 · 개발'
    assert any('# 근로기준법 챗봇 · 개발' in str(getattr(b, 'value', '')) for b in demo.blocks.values())

# ---- 모델 선택 ----
def test_selection_is_per_request(env):
    # 요청마다 고른 모델이 답한다: 이전 선택이 다음 질문에 남지 않음
    chat = app_mod.make_chat(make_app(env, resident=2))
    names = {'fast': paths.LLM_4BIT.name, 'pro': paths.PRO_BASE.name}
    for key in ('fast', 'pro', 'fast', 'pro', 'pro', 'fast'):
        text, _, stats = list(chat('해고 예고?', [], key, 'full'))[-1]
        assert names[key] in text and stats.startswith(f"Lawbot {key.title()}")

def test_fast_and_pro_get_same_context(env):
    a = make_app(env, resident=2)
    chat = app_mod.make_chat(a)
    src = [list(chat('수습 해고 예고?', [], k, 'full'))[-1][1] for k in ('fast', 'pro')]
    assert src[0] == src[1]
    assert a.models.loaded['fast'].args == a.models.loaded['pro'].args == ('수습 해고 예고?', HITS)

def test_switch_shows_loading_then_answer(env):
    chat = app_mod.make_chat(make_app(env, resident=1))
    steps = list(chat('질문', [], 'pro', 'concise'))
    assert steps[0][0] == '⏳ Lawbot Pro 불러오는 중…' and '모델 전환 대기' in steps[0][2]
    assert steps[0][1] == steps[-1][1] != ''            # 조문 카드는 로딩 중에도 먼저 보임
    assert '모델 전환' in steps[-1][2]
    assert '모델 전환' not in list(chat('질문', [], 'pro', 'concise'))[-1][2]   # 이미 올라 있으면 전환 없음

def test_invalid_inputs(env):
    a = make_app(env)
    chat = app_mod.make_chat(a)
    assert list(chat('   ', [], 'fast', 'concise')) == [('질문을 입력해 주세요.', '', '')]
    assert not hasattr(a.retriever, 'query')            # 빈 질문은 검색하지 않음
    for key, fmt in (('ultra', 'concise'), ('fast', 'long')):
        with pytest.raises(app_mod.gr.Error, match='알 수 없는 선택'):
            list(chat('질문', [], key, fmt))

def test_empty_and_truncated_answer(env):
    class Odd(FakeLLM):
        def stream(self, question, hits):
            yield types.SimpleNamespace(text=Odd.text, finish_reason=Odd.finish)
    Odd.text, Odd.finish = ' ', 'stop'
    chat = app_mod.make_chat(make_app(env, llm=Odd))
    assert list(chat('질문', [], 'fast', 'concise'))[-1][0] == app_mod.EMPTY
    Odd.text, Odd.finish = '【결론】길게', 'length'
    assert '⚠ 최대 길이에서 잘림' in list(chat('질문', [], 'fast', 'full'))[-1][2]

def test_early_stop_cuts_generation(env):
    seen = []
    class Long(FakeLLM):
        def stream(self, question, hits):
            for c in ('【결론】', '예고해야 합니다.', '\n【근거 조문】\n제26조(해고의 예고)\n', '【설명】', '- 긴 설명', '【참고】'):
                seen.append(c)
                yield types.SimpleNamespace(text=c)
    chat = app_mod.make_chat(make_app(env, llm=Long, early_stop=True))
    text, _, stats = list(chat('질문', [], 'fast', 'concise'))[-1]
    assert text == '예고해야 합니다.\n\n근거: 근로기준법 제26조(해고의 예고)'
    assert '간결형 조기 종료' in stats and seen[-1] == '【설명】'
    seen.clear()                                        # 전체 형식은 조기 종료하지 않음
    assert list(chat('질문', [], 'fast', 'full'))[-1][0].endswith('【참고】') and seen[-1] == '【참고】'

# ---- 간결형 ----
FULL = ('【결론】\n연장근로수당은 통상임금의 50% 이상을 가산해 지급하는 것이 원칙입니다.\n\n'
        '【근거 조문】\n근로기준법 제56조(연장·야간 및 휴일 근로) 제1항\n\n'
        '【설명】\n- 제56조: 사용자는 ...\n\n【참고】\n- 위반 시 3년 이하의 징역 또는 3천만원 이하의 벌금')
H56 = [{'article_id': a, 'title': t, 'score': 0.9, 'doc': f'{a} {t}\n본문'}
       for a, t in (('제56조', '연장·야간 및 휴일 근로'), ('제55조', '휴일'), ('제50조', '근로시간'))]

def test_concise_shows_only_cited_articles():
    out = concise(FULL, H56)
    assert out == ('연장근로수당은 통상임금의 50% 이상을 가산해 지급하는 것이 원칙입니다.\n\n'
                   '근거: 근로기준법 제56조(연장·야간 및 휴일 근로)')
    assert '제55조' not in out and '벌금' not in out and '【' not in out

def test_concise_streaming_steps():
    assert concise('【결', H56, done=False) == ''                       # 표시 기호를 쓰는 중
    partial = FULL[:FULL.index('제1항')]
    assert '근거:' not in concise(partial, H56, done=False)             # 근거 조문을 쓰는 중이면 붙이지 않음
    assert concise(FULL[:FULL.index('【설명】') + 5], H56, done=False).endswith('휴일 근로)')

def test_concise_refusal_and_fallbacks():
    refusal = '【결론】\n제공된 조문으로는 확인할 수 없습니다.\n\n【근거 조문】\n해당 없음 (제공된 조문: 제56조, 제55조)'
    assert concise(refusal, H56) == '제공된 조문으로는 확인할 수 없습니다.'
    assert concise('형식 없는 답변', H56) == '형식 없는 답변'                # 형식을 벗어난 답은 숨기지 않음
    outside = FULL.replace('제56조(연장·야간 및 휴일 근로) 제1항', '제56조, 제109조(벌칙)')
    assert concise(outside, H56).endswith('⚠ 검색된 조문에서 확인되지 않은 인용: 제109조')
    no_cite = '【결론】\n가산해야 합니다.\n\n【설명】\n- 설명'
    assert '⚠ 인용 조문 없음' in concise(no_cite, H56)

def test_cited_hits_v4_law_names():
    hits = [{'law': '근로기준법', 'article_id': '제2조', 'title': '정의'},
            {'law': '최저임금법', 'article_id': '제2조', 'title': '정의'},
            {'law': '최저임금법', 'article_id': '제6조', 'title': '최저임금의 효력'}]
    used, unknown = cited_hits('최저임금법 제2조(정의), 제6조(최저임금의 효력)', hits)
    assert used == hits[1:] and unknown == []
    used, unknown = cited_hits('제2조(정의)', hits)                     # 법령을 특정할 수 없음
    assert used == [] and unknown == ['제2조']
    assert cited_hits('근로기준법 제2조의2', hits) == ([], ['제2조의2'])   # 번호가 비슷한 조문과 구분

# ---- v4 검색 문서 형식 ----
class FakeEncoder:
    def __init__(self, path, device=None, model_kwargs=None):
        pass

    def encode(self, texts, prompt_name=None, **kw):
        import numpy as np
        return np.eye(len(texts), 8, dtype=np.float32) if prompt_name is None else np.ones((len(texts), 8), np.float32)

def test_retriever_v4_format(env, tmp_path):
    csv = tmp_path / 'v4.csv'
    pd.DataFrame({'law': ['근로기준법', '최저임금법', '최저임금법'], 'article_id': ['제2조', '제2조', '제3조'],
                  'title': ['정의', '정의', '삭제'], 'deleted': [0, 0, 1], 'text': ['본문 A', '본문 B', '']}
                 ).to_csv(csv, index=False, encoding='utf-8-sig')
    env.setattr(retriever_mod, 'SentenceTransformer', FakeEncoder)
    env.setattr(retriever_mod.torch.mps, 'empty_cache', lambda: None)
    r = retriever_mod.Retriever('models/fake', csv, max_seq_length=2304, doc_style='v4')
    assert r.docs == ['근로기준법 제2조(정의)\n본문 A', '최저임금법 제2조(정의)\n본문 B']
    assert r.m.max_seq_length == 2304 and r.starter == set()
    hits = r.search('정의', k=2)
    assert {(h['law'], h['article_id']) for h in hits} == {('근로기준법', '제2조'), ('최저임금법', '제2조')}
    assert app_mod.fmt_sources(hits).count('**최저임금법 제2조 정의**') == 1

def test_retriever_v4_article_no(env, tmp_path):
    """실제 v4 CSV: article_id에 법령명이 들어 있고 번호는 article_no. 법령명이 두 번 들어가면 안 됨"""
    csv = tmp_path / 'v4.csv'
    pd.DataFrame({'law': ['근로기준법', '최저임금법'], 'article_id': ['근로기준법 제26조', '최저임금법 제5조'],
                  'article_no': ['제26조', '제5조'], 'title': ['해고의 예고', '최저임금액'], 'deleted': [0, 0],
                  'text': ['본문 A', '본문 B']}).to_csv(csv, index=False, encoding='utf-8-sig')
    env.setattr(retriever_mod, 'SentenceTransformer', FakeEncoder)
    env.setattr(retriever_mod.torch.mps, 'empty_cache', lambda: None)
    r = retriever_mod.Retriever('models/fake', csv, max_seq_length=2304, doc_style='v4')
    assert r.docs == ['근로기준법 제26조(해고의 예고)\n본문 A', '최저임금법 제5조(최저임금액)\n본문 B']
    hits = r.search('해고', k=2)
    assert {h['article_id'] for h in hits} == {'제26조', '제5조'}
    assert build_prompt_v4(hits, 'q').count('근로기준법 제26조(해고의 예고)') == 1
    assert '근로기준법 근로기준법' not in build_prompt_v4(hits, 'q') + app_mod.fmt_sources(hits)
    assert cited_hits('근로기준법 제26조(해고의 예고)', hits) == ([h for h in hits if h['law'] == '근로기준법'], [])

# ---- 배포 검사 ----
def write_safetensors(path, dtype):
    header = json.dumps({'w': {'dtype': dtype, 'shape': [1], 'data_offsets': [0, 2]}}).encode()
    path.write_bytes(struct.pack('<Q', len(header)) + header + b'\0\0')

def fake_mlx(folder, bits=4, hidden=2560, adapter=False):
    folder.mkdir(parents=True)
    (folder / 'config.json').write_text(json.dumps({'model_type': 'qwen3', 'hidden_size': hidden,
                                                    'quantization': {'group_size': 64, 'bits': bits}}))
    (folder / 'tokenizer.json').write_text('{}')
    (folder / 'tokenizer_config.json').write_text(json.dumps({'chat_template': '{{ messages }}'}))
    write_safetensors(folder / 'model.safetensors', 'U32')
    if adapter:
        (folder / 'adapters.safetensors').write_bytes(b'')

def test_check_deploy_reports_each_asset(env, tmp_path):
    emb = tmp_path / 'emb'
    emb.mkdir()
    write_safetensors(emb / 'model.safetensors', 'F16')
    (emb / 'config_sentence_transformers.json').write_text(json.dumps({'prompts': {'query': 'Instruct: q\nQuery:'}}))
    (emb / 'modules.json').write_text('[]')
    (emb / 'tokenizer.json').write_text('{}')
    fake_mlx(tmp_path / 'fast')
    fake_mlx(tmp_path / 'pro', bits=4, hidden=5120, adapter=True)
    env.setenv('LAWBOT_EMB', str(emb))
    env.setenv('LAWBOT_FAST_PATH', str(tmp_path / 'fast'))
    env.setenv('LAWBOT_PRO_PATH', str(tmp_path / 'pro'))
    out = dict((msg, level) for level, msg in check_deploy.run('v1'))
    text = '\n'.join(f'{lv} {m}' for m, lv in out.items())
    assert 'OK 비삭제 조문 130개 (기대 130)' in text
    assert "dtype {'F16': 1}" in text and "query 프롬프트: 'Instruct: q\\nQuery:'" in text
    assert 'OK Lawbot Fast: qwen3 Qwen3-4B' in text and 'Qwen3-14B' in text
    assert "FAIL Lawbot Pro: 어댑터 파일 남아 있음 ['adapters.safetensors']" in text

def test_check_deploy_v4_placeholders_fail(env, tmp_path):
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / '없음.csv'))
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / '없음.txt'))
    env.setenv('LAWBOT_V4_EMB', str(tmp_path / '없음'))
    env.setenv('LAWBOT_FAST_PATH', str(tmp_path / '없음4b'))
    env.setenv('LAWBOT_PRO_PATH', str(tmp_path / '없음14b'))
    env.setenv('LAWBOT_PRO_SYSTEM', str(tmp_path / '없음_concise.txt'))
    fails = [m for level, m in check_deploy.run('v4') if level == 'FAIL']
    assert len(fails) == 6 and all('없' in m for m in fails)

def test_check_deploy_article_number_format(env, tmp_path):
    csv = tmp_path / 'v4.csv'
    rows = {'law': ['근로기준법'] * 415, 'article_id': [f'근로기준법 제{i}조' for i in range(1, 416)],
            'title': ['t'] * 415, 'deleted': [0] * 415, 'text': ['x'] * 415}
    pd.DataFrame(rows).to_csv(csv, index=False, encoding='utf-8-sig')
    env.setenv('LAWBOT_V4_ARTICLES', str(csv))
    fails = [m for level, m in check_deploy.run('v4') if level == 'FAIL']
    assert any("'제N조' 형식이 아님 415행" in m for m in fails)
    pd.DataFrame(rows | {'article_no': [f'제{i}조' for i in range(1, 416)]}).to_csv(csv, index=False, encoding='utf-8-sig')
    out = check_deploy.run('v4')
    assert ('OK', next(m for lv, m in out if '비삭제 조문' in m)) in out
    assert not any('형식이 아님' in m for lv, m in out)

def test_eval_concise_refuses_test_set(env):
    import lawbot.eval_concise as ec
    env.setattr('sys.argv', ['eval_concise', '--val', str(paths.EVAL), '--tag', 'x'])
    with pytest.raises(SystemExit, match='평가 전용'):
        ec.main()

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
    assert p.name == 'v4' and [m.key for m in p.models] == ['pro']
    assert p.models[0].path == paths.V4_PRO and p.models[0].system_file == paths.V4_SYSTEM_CONCISE
    assert p.emb == paths.V4_EMB and p.articles == paths.V4_ARTICLES
    assert deploy.reranker(p) == deploy.RerankerConfig(paths.V4_RERANKER, 10)
    assert paths.V4_SYSTEM_CONCISE.is_file()                       # Pro 간결형 system은 저장소에 있음

def test_env_file_loading(env, tmp_path, capsys):
    f = tmp_path / 'lawbot.env'
    f.write_text("# 주석\nexport LAWBOT_PROFILE=v4\nLAWBOT_USER = 'me'\nLAWBOT_PW=\"p w#1\"\n"
                 "LAWBOT_MODELS=pro  # 끝 주석\nLAWBOT_EARLY_STOP=1\n", encoding='utf-8')
    f.chmod(0o600)
    env.setenv('LAWBOT_ENV_FILE', str(f))
    env.delenv('LAWBOT_PROFILE')                                 # conftest의 v1을 지워 파일 값이 들어가게
    env.setenv('LAWBOT_EARLY_STOP', '0')                         # 셸 값이 우선
    assert envfile.load() == f
    import os
    assert (os.environ['LAWBOT_PROFILE'], os.environ['LAWBOT_USER'], os.environ['LAWBOT_PW']) == ('v4', 'me', 'p w#1')
    assert os.environ['LAWBOT_MODELS'] == 'pro' and os.environ['LAWBOT_EARLY_STOP'] == '0'
    out = capsys.readouterr().out
    assert 'p w#1' not in out and '경고' not in out               # 값은 출력하지 않음
    f.chmod(0o644)
    env.delenv('LAWBOT_PW')
    envfile.load()
    assert 'chmod 600' in capsys.readouterr().out                 # 비밀번호가 든 파일을 남이 읽을 수 있으면 경고

def test_env_file_errors_and_defaults(env, tmp_path):
    env.setenv('LAWBOT_ENV_FILE', str(tmp_path / '없음.env'))
    with pytest.raises(FileNotFoundError):                        # 직접 지정한 파일이 없으면 오류
        envfile.load()
    env.setenv('LAWBOT_ENV_FILE', '')
    assert envfile.load() is None                                 # 빈 값이면 읽지 않음
    env.delenv('LAWBOT_ENV_FILE')
    env.setattr(envfile, 'DEFAULT', tmp_path / '.env')
    assert envfile.load() is None                                 # 기본 .env가 없으면 아무것도 안 함
    for bad in ('LAWBOT_PW', 'GRADIO_SERVER_NAME=0.0.0.0', 'PATH=/tmp', 'LAWBOT_ENV_FILE=x'):
        with pytest.raises(ValueError, match='1번째 줄'):
            envfile.parse(bad)

def test_models_selection(env):
    env.setenv('LAWBOT_MODELS', 'pro')
    assert [m.key for m in deploy.get_profile('v4').models] == ['pro']
    env.setenv('LAWBOT_MODELS', 'pro, fast')
    assert [m.key for m in deploy.get_profile('v1').models] == ['pro', 'fast']   # 첫 번째가 기본 선택
    for bad in ('ultra', 'pro,pro'):
        env.setenv('LAWBOT_MODELS', bad)
        with pytest.raises(ValueError, match='LAWBOT_MODELS'):
            deploy.get_profile('v4')

def test_pro_only_app(env):
    env.setenv('LAWBOT_MODELS', 'pro')
    a = make_app(env, resident=1)
    assert list(a.models.specs) == ['pro'] and list(a.models.loaded) == ['pro']   # Fast는 올리지 않음
    assert [m.key for m in a.profile.disabled] == ['fast']
    demo = app_mod.build_demo(a)
    radios = {c.label: c for c in demo.blocks.values() if isinstance(c, app_mod.gr.Radio)}
    assert radios['답변 모델'].value == 'pro' and radios['답변 형식'].value == 'concise'
    assert [k for _, k in radios['답변 모델'].choices] == ['pro', 'fast']         # Fast는 비활성으로 남김
    assert '이 기기에서 사용 안 함' in radios['답변 모델'].choices[1][0]
    with pytest.warns(UserWarning, match='사용하지 않는 모델'):                    # 화면 안내 (gr.Warning)
        assert app_mod.default_fmt(a)('fast') == ('pro', 'concise')                # 고르면 기본 모델로 되돌림
    chat = app_mod.make_chat(a)
    with pytest.raises(app_mod.gr.Error, match='사용하지 않는 모델'):
        list(chat('질문', [], 'fast', 'full'))
    assert FakeLLM.created == 1 or list(a.models.loaded) == ['pro']
    text, _, stats = list(chat('해고 예고?', [], 'pro', 'concise'))[-1]
    assert stats.startswith('Lawbot Pro')
    assert any('사용 안 함' in m for lv, m in check_deploy.run('v4'))

def test_full_format_uses_four_part_system(env):
    # 간결형이 기본인 Pro에서 '전체'를 고르면 4항목 system으로 생성 (간결형 출력이 그대로 나오지 않게)
    a = make_app(env, resident=2)
    chat = app_mod.make_chat(a)
    list(chat('질문', [], 'pro', 'full'))
    assert a.models.loaded['pro'].system == a.full_system
    from lawbot.llm import SYSTEM
    assert a.full_system == SYSTEM                                  # v1 프로필: llm.SYSTEM (4섹션)
    list(chat('질문', [], 'pro', 'concise'))
    assert a.models.loaded['pro'].system is None                    # 간결형: Pro 기본(간결형) system
    for f in ('full', 'concise'):                                   # Fast(4항목 학습)는 system을 바꾸지 않음
        list(chat('질문', [], 'fast', f))
        assert a.models.loaded['fast'].system is None
    list(service.ask(a, '질문', 'pro'))                             # fmt 없이 (평가·벤치): 모델 기본
    assert a.models.loaded['pro'].system is None

def test_full_system_v4_profile(env):
    env.setenv('LAWBOT_PROFILE', 'v4')
    a = make_app(env, resident=1)
    assert a.full_system == paths.V4_SYSTEM.read_text(encoding='utf-8').strip()
    assert '【설명】' in a.full_system and '【설명】' not in paths.V4_SYSTEM_CONCISE.read_text(encoding='utf-8')
