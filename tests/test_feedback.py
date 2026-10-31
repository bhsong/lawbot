# tests/test_feedback.py  →  답변 피드백(B6): 화면 버튼·저장 기록·검수·내보내기 (가짜 모델, 피드백 파일은 tmp_path에만)
import json
import os
import stat
import subprocess
import sys
import threading
import types
import pandas as pd
import pytest
import lawbot.app as app_mod
import lawbot.service as service
from lawbot import feedback, feedback_export, feedback_review, feedback_ui
from lawbot.paths import ROOT
from fakes import FakeLLM, FakeRetriever
from model_fakes import TOKEN, GenLLM, Ret, serve

QUESTION = '해고 예고 없이 해고되면 어떻게 하나요 고유문구XYZ'
CHUNKS = ['【결론】\n예고해야', ' 합니다.\n\n【근거 조문】\n근로기준법 제26조\n\n【설명】\n- 설명']

def local_app(env):
    env.setattr(FakeLLM, 'chunks', CHUNKS)
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setenv('LAWBOT_MAX_RESIDENT', '2')
    return service.load_app()

def req(session='s1'):
    return types.SimpleNamespace(session_hash=session)

def lines(folder):
    return [json.loads(x) for f in sorted(folder.glob('feedback_*.jsonl')) for x in f.read_text(encoding='utf-8').splitlines()]

def turn(chat, history, question=QUESTION, model='fast', fmt='full', session='s1'):
    """한 턴을 돌리고 Chatbot에 보일 history(질문·답변 추가)를 돌려준다"""
    text = list(chat(question, history, model, fmt, req(session)))[-1][0]
    return history + [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': text}]

def like_deps(demo):
    cfg = demo.get_config_file()
    return [d for d in cfg['dependencies'] if any(t[1] == 'like' for t in d['targets'])]

# ---- T1·T2·T3: 화면 구성 ----
def test_disabled_by_default(env, tmp_path):
    assert feedback.from_env() is None
    demo = app_mod.build_demo(local_app(env))
    assert like_deps(demo) == []                                       # 버튼(.like 의존성) 없음
    bot = next(b for b in demo.blocks.values() if isinstance(b, app_mod.gr.Chatbot))
    assert not bot.feedback_options
    assert feedback_ui.launch_options(None) == {}
    env.setenv('LAWBOT_FEEDBACK', 'yes')
    with pytest.raises(ValueError, match='LAWBOT_FEEDBACK'):
        feedback.enabled()

def test_gradio_flagging_never(env, tmp_path):
    env.setenv('GRADIO_CHAT_FLAGGING_MODE', 'manual')                  # 기본 피드백(.gradio/flagged 저장)을 켜는 변수
    made = []
    class Recorded(app_mod.gr.ChatInterface):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)
    env.setattr(app_mod.gr, 'ChatInterface', Recorded)
    for fb in (None, feedback.Feedback(tmp_path)):
        demo = app_mod.build_demo(local_app(env), fb)
        assert made[-1].flagging_mode == 'never'
        assert len(like_deps(demo)) == (0 if fb is None else 1)        # Gradio 기본 flag 이벤트가 없음

def test_api_inputs_unchanged_and_feedback_private(env, tmp_path):
    demo = app_mod.build_demo(local_app(env), feedback.Feedback(tmp_path))
    cfg = demo.get_config_file()
    [chat] = [d for d in cfg['dependencies'] if d.get('api_name') == 'chat']
    comps = {c['id']: c for c in cfg['components']}
    assert [comps[i]['type'] for i in chat['inputs'] if comps[i]['type'] != 'state'] == ['textbox', 'radio', 'radio']
    [like] = like_deps(demo)
    assert like['api_visibility'] == 'private'
    bot = next(b for b in demo.blocks.values() if isinstance(b, app_mod.gr.Chatbot))
    assert list(bot.feedback_options) == ['Like', '검색 오류', '답변 오류', '불필요한 거절', '기타']
    assert 'Dislike' not in bot.feedback_options and 'copy' in bot.buttons

# ---- T4·T5: 저장 기록 ----
def test_like_saves_full_turn(env, tmp_path):
    a = local_app(env)
    fb = feedback.Feedback(tmp_path)
    chat = app_mod.make_chat(a, fb)
    h = turn(chat, [], fmt='concise', model='pro')
    expected = list(service.ask(a, QUESTION, 'pro', fmt='concise'))[-1]
    row = fb.on_like('s1', h, 1, [h[1]['content']], True)
    assert row is not None and lines(tmp_path) == [row]
    assert (row['label'], row['reason'], row['question'], row['model'], row['fmt']) == ('like', None, QUESTION, 'pro', 'concise')
    assert row['hits'] == expected[1] and row['answer_raw'] == expected[2]
    assert row['system'] == service.system_text(a.profile, a.models.specs['pro'])   # Pro 간결형 system 파일
    assert row['answer_shown'] == h[1]['content'] != row['answer_raw']              # 간결형: 화면 글은 후처리
    assert row['refused'] is False and row['citations'] == [{'law': '근로기준법', 'article_id': '제26조', 'verdict': 'ok'}]
    assert (row['mode'], row['profile'], row['dataset_version']) == ('local', 'v1', 'v1')
    assert row['request_id'] and row['timing']['total'] is not None and row['commit']

def test_full_format_system_and_default(env, tmp_path):
    a = local_app(env)
    fb = feedback.Feedback(tmp_path)
    chat = app_mod.make_chat(a, fb)
    h = turn(chat, [], model='pro', fmt='full')                         # 간결형 모델에서 '전체' → 4항목 system
    h = turn(chat, h, model='fast', fmt='full')                         # Fast: 모델 기본 system (v1 SYSTEM)
    r1 = fb.on_like('s1', h, 1, h[1]['content'], True)
    r2 = fb.on_like('s1', h, 3, h[3]['content'], True)
    from lawbot.prompts import SYSTEM
    assert r1['system'] == a.full_system and r2['system'] == SYSTEM

def test_labels_reasons_and_dedup(env, tmp_path):
    fb = feedback.Feedback(tmp_path)
    chat = app_mod.make_chat(local_app(env), fb)
    h = turn(chat, [])
    h = turn(chat, h, question='두 번째 질문')
    v = h[3]['content']
    t = 100.0
    for liked, label, reason in ((True, 'like', None), ('검색 오류', 'flag', 'retrieval'), ('답변 오류', 'flag', 'answer'),
                                 ('불필요한 거절', 'flag', 'false_refusal'), ('기타', 'flag', 'other'), ('', 'cleared', None)):
        row = fb.on_like('s1', h, 3, [v], liked, now=t)
        assert (row['label'], row['reason'], row['question']) == (label, reason, '두 번째 질문')
        assert fb.on_like('s1', h, 3, [v], liked, now=t + 0.5) is None  # 이유 클릭 한 번 = 이벤트 두 번
        t += 10
    assert fb.on_like('s1', h, 3, [v], True, now=t + 2.5) is not None   # 2초가 지나면 다시 저장
    old = fb.on_like('s1', h, 1, [h[1]['content']], '답변 오류', now=t)  # 이전 답변의 버튼은 그 답변의 turn_id로
    assert old['question'] == QUESTION and old['turn_id'] != lines(tmp_path)[0]['turn_id']
    assert fb.on_like('s1', h, 3, [v], '엉뚱한 값') is None
    assert len(lines(tmp_path)) == 8

def test_launch_passes_touch_css_js(env, tmp_path):
    from conftest import set_auth
    set_auth(env)
    local_app(env)
    launched = []
    env.setattr(app_mod.gr.Blocks, 'launch', lambda demo, **kw: launched.append(kw))
    env.setattr(app_mod, 'load_app', lambda: local_app(env))
    app_mod.main()
    assert 'css' not in launched[-1] and 'js' not in launched[-1]       # 기본: 꺼짐
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_FEEDBACK_DIR', str(tmp_path / 'fb'))
    app_mod.main()
    assert launched[-1]['css'] == feedback_ui.CSS and launched[-1]['js'] == feedback_ui.JS
    assert 'lawbot-touch' in feedback_ui.CSS and '(hover: none)' in feedback_ui.JS and 'pointerdown' in feedback_ui.JS
    assert not (tmp_path / 'fb').exists()                               # 버튼을 누르기 전에는 파일을 만들지 않음

# ---- T6: 기록이 없거나 다른 답 ----
def test_unknown_or_changed_turn_not_saved(env, tmp_path, capsys):
    fb = feedback.Feedback(tmp_path)
    chat = app_mod.make_chat(local_app(env), fb)
    h = turn(chat, [])
    restarted = feedback.Feedback(tmp_path)                             # 앱 재시작: 메모리 기록 없음
    assert restarted.on_like('s1', h, 1, h[1]['content'], True) is None
    assert fb.on_like('s2', h, 1, h[1]['content'], True) is None        # 다른 세션
    assert fb.on_like('s1', h, 1, '다른 답변', True) is None             # 화면 글이 기록과 다름
    assert fb.on_like('s1', h, 0, h[0]['content'], True) is None        # 사용자 메시지
    assert lines(tmp_path) == []
    out = capsys.readouterr().out
    assert '저장 안 함' in out and '고유문구XYZ' not in out and '예고해야' not in out   # 콘솔에 질문·답변 없음

def test_retry_replaces_turn(env, tmp_path):
    fb = feedback.Feedback(tmp_path)
    chat = app_mod.make_chat(local_app(env), fb)
    h1 = turn(chat, [])
    h2 = turn(chat, [], question='고쳐 쓴 질문')                         # 재시도·편집: 같은 순번에 새 답
    row = fb.on_like('s1', h2, 1, h2[1]['content'], True)
    assert row['question'] == '고쳐 쓴 질문'
    assert fb.lookup('s1', 0)[0] != feedback.turn_id('s1', 0, 'x') and h1 != h2

def test_memory_limits(env, tmp_path):
    fb = feedback.Feedback(tmp_path)
    rec = {'request_id': 'r'}
    for n in range(feedback.TURNS_PER_SESSION + 5):
        fb.remember('s', n, rec)
    assert len(fb.turns['s']) == feedback.TURNS_PER_SESSION and fb.lookup('s', 0) is None
    for i in range(feedback.MAX_SESSIONS + 3):
        fb.remember(f'x{i}', 0, rec)
    assert len(fb.turns) == feedback.MAX_SESSIONS and 's' not in fb.turns

def test_index_and_value_shapes():
    h = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'},
         types.SimpleNamespace(role='user', content='q2'), types.SimpleNamespace(role='assistant', content='b')]
    assert [feedback.assistant_index(h, i) for i in (1, 3, (3, 0), 0, 9, None)] == [0, 1, 1, None, None, None]
    assert feedback.text_of(['가', {'type': 'text', 'text': '나'}]) == '가나' and feedback.text_of(None) == ''

# ---- T7: 저장 폴더·권한 ----
def test_dir_must_be_ignored(env, tmp_path):
    feedback.check_private(ROOT / 'feedback')                           # gitignore
    feedback.check_private(tmp_path / 'fb')                             # 레포 밖
    with pytest.raises(RuntimeError, match='gitignore'):
        feedback.check_private(ROOT / 'src' / 'fb_test')
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_FEEDBACK_DIR', 'docs/fb')                        # 상대 경로는 레포 최상위 기준
    with pytest.raises(RuntimeError, match='실행을 거부'):
        feedback.from_env()
    env.delenv('LAWBOT_FEEDBACK_DIR')
    assert feedback.from_env().folder == ROOT / 'feedback'
    assert not (ROOT / 'src' / 'fb_test').exists()

def test_file_mode_600(env, tmp_path):
    fb = feedback.Feedback(tmp_path / 'fb')
    fb.write({'a': 1})
    [f] = (tmp_path / 'fb').glob('feedback_*.jsonl')
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / 'fb').stat().st_mode) == 0o700

# ---- T8: 원격 모드·import ----
@pytest.mark.integration                                                # 127.0.0.1 접속 허용 (conftest)
def test_remote_mode_same_record(env, tmp_path):
    env.setattr(GenLLM, 'chunks', CHUNKS)
    env.setattr(service, 'Retriever', Ret)
    env.setattr(service, 'LLM', GenLLM)
    env.setenv('LAWBOT_MAX_RESIDENT', '2')
    srv = service.load_app(local=True)
    loc = feedback.Feedback(tmp_path / 'local')
    h = turn(app_mod.make_chat(srv, loc), [], model='pro', fmt='concise')
    with serve(srv) as (_, url):
        env.setenv('LAWBOT_MODEL_SERVER', url)
        env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
        rem = feedback.Feedback(tmp_path / 'remote')
        hr = turn(app_mod.make_chat(service.load_app(), rem), [], model='pro', fmt='concise')
    a = loc.on_like('s1', h, 1, h[1]['content'], True)
    b = rem.on_like('s1', hr, 1, hr[1]['content'], True)
    assert b['mode'] == 'remote' and a['mode'] == 'local'
    for k in ('question', 'hits', 'system', 'answer_raw', 'answer_shown', 'refused', 'citations', 'model', 'fmt'):
        assert a[k] == b[k], k
    assert srv.models.loaded['pro'].calls[-1]['system'] == b['system']   # 서버가 실제로 쓴 system

def test_feedback_module_has_no_model_imports():
    code = ('import sys, lawbot.feedback, lawbot.feedback_review\n'
            "print(','.join(m for m in ('mlx', 'mlx_lm', 'torch', 'sentence_transformers', 'gradio', 'lawbot.llm') "
            'if m in sys.modules))')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ''

# ---- T10: 동시 쓰기 ----
def test_concurrent_writes_do_not_interleave(tmp_path):
    fb = feedback.Feedback(tmp_path)
    big = '가' * 200_000
    threads = [threading.Thread(target=lambda i=i: [fb.write({'i': i, 'j': j, 'x': big}) for j in range(5)]) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = lines(tmp_path)
    assert len(rows) == 20 and all(r['x'] == big for r in rows)

# ---- T9: 검수 ----
def fb_row(fid, question, label='like', reason=None, ts='2026-11-01T10:00:00', turn=None, profile='v4', hits=None):
    hits = hits or [{'law': '근로기준법', 'article_id': '제26조', 'title': '해고의 예고', 'score': 0.9,
                     'doc': '근로기준법 제26조(해고의 예고)\n본문'}]
    return {'feedback_id': fid, 'turn_id': turn or fid, 'ts': ts, 'label': label, 'reason': reason, 'question': question,
            'hits': hits, 'system': 'S', 'answer_raw': '【결론】\n답', 'answer_shown': '답', 'model': 'pro',
            'fmt': 'concise', 'refused': False, 'profile': profile}

def write_rows(folder, rows, name='feedback_2026-11.jsonl'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows) + '{"깨진', encoding='utf-8')

EVALS = [('v4_test', 'Q0001', '회사가 해고 예고를 안 했어요. 어떻게 하나요?'),
         ('oos', 'OOS001', '내일 서울 날씨 어때요?')]

def test_review_dedup_and_overlap(env, tmp_path):
    write_rows(tmp_path / 'prod', [
        fb_row('a', '회사가 해고 예고를 안 했어요. 어떻게 하나요?'),           # 평가 문항과 같음
        fb_row('b', '회사가  해고예고를 안했어요 어떻게하나요'),                # 공백·문장부호만 다름
        fb_row('c', '회사가 해고 예고를 안 했어요. 어떻게 하나여?'),            # 유사도 0.9 이상
        fb_row('d1', '연차휴가는 며칠인가요?', ts='2026-11-01T10:00:00', turn='t'),
        fb_row('d2', '연차휴가는 며칠인가요?', 'flag', 'retrieval', ts='2026-11-01T10:00:05', turn='t'),   # 같은 turn: 마지막
        fb_row('e1', '퇴직금은 언제 주나요?', turn='u'),
        fb_row('e2', '퇴직금은 언제 주나요?', 'cleared', turn='u', ts='2026-11-01T11:00:00')])           # 해제: 뺌
    write_rows(tmp_path / 'dev', [fb_row('f', '야간 근로 수당은?')])
    out = tmp_path / 'feedback_review_2026-11-30.csv'
    lines_ = feedback_review.review([tmp_path / 'prod', tmp_path / 'dev'], out, evals=EVALS)
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    assert sorted(d['feedback_id']) == ['d2', 'f']
    assert list(d.columns[-4:]) == ['h_ok', 'h_gold', 'h_scope', 'h_note'] and d.loc[0, 'hits'] == '근로기준법 제26조'
    text = '\n'.join(lines_)
    assert '평가 데이터와 겹쳐 뺌 3 (완전 일치 2, 유사도 0.9 이상 1)' in text and 'v4_test:Q0001' in text
    assert '깨진 줄 2' in text and '선택 해제로 뺌 1' in text
    assert '회사가' not in text                                         # 출력에 질문 원문 없음
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert len(feedback_review.companion(out).read_text(encoding='utf-8').splitlines()) == 2
    d.loc[d.feedback_id == 'f', 'h_ok'] = '맞음'                         # 다시 돌려도 사람이 채운 칸을 이어받음
    d.to_csv(tmp_path / 'prev.csv', index=False, encoding='utf-8-sig')
    feedback_review.review([tmp_path / 'prod', tmp_path / 'dev'], out, keep=tmp_path / 'prev.csv', evals=EVALS)
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    assert d.set_index('feedback_id').loc['f', 'h_ok'] == '맞음'
    with pytest.raises(RuntimeError, match='gitignore'):                 # 레포 안의 무시되지 않는 경로에는 안 씀
        feedback_review.review([tmp_path / 'dev'], ROOT / 'docs' / 'review.csv', evals=EVALS)

def test_review_real_eval_sets():
    ov = feedback_review.Overlap(feedback_review.eval_sets())
    t = pd.read_csv(ROOT / 'data/v4/qa_full_v4.csv', encoding='utf-8-sig').merge(
        pd.read_csv(ROOT / 'data/v4/split_v4.csv', encoding='utf-8-sig')[['id', 'split_emb']], on='id')
    test_q = t[(t.split_emb == 'test') & t.eval_cat.isin(['A_lsa', 'B_newlaw'])].iloc[0]
    assert ov.match(test_q.question + ' ') == (f'v4_test:{test_q.id}', 'exact')
    assert sum(src == 'v4_test' for src, _, _ in feedback_review.eval_sets()) == 2075

# ---- 내보내기 ----
def test_export(env, tmp_path):
    hits = [{'law': '근로기준법', 'article_id': '제27조', 'title': '해고사유 등의 서면통지', 'score': 0.9,
             'doc': '근로기준법 제27조(해고사유 등의 서면통지)\n본문27'},
            {'law': '근로기준법', 'article_id': '제26조', 'title': '해고의 예고', 'score': 0.8,
             'doc': '근로기준법 제26조(해고의 예고)\n본문26'}]
    write_rows(tmp_path / 'fb', [fb_row('a', '질문A', hits=hits), fb_row('b', '질문B', 'flag', 'retrieval', hits=hits),
                                 fb_row('c', '질문C', 'flag', 'answer', hits=hits), fb_row('d', '질문D', hits=hits),
                                 fb_row('v1', '질문E', profile='v1', hits=hits)])
    out = tmp_path / 'review.csv'
    feedback_review.review([tmp_path / 'fb'], out, evals=[])
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig').set_index('feedback_id')
    d.loc['a', ['h_ok', 'h_gold']] = ['맞음', '근로기준법제26조']               # 띄어쓰기 없이 써도 됨
    d.loc['b', ['h_ok', 'h_gold']] = ['틀림', '근로기준법 제26조; 근로기준법 제23조']
    d.loc['c', 'h_ok'] = '틀림'
    d.loc['v1', 'h_ok'] = '맞음'
    d.reset_index().to_csv(out, index=False, encoding='utf-8-sig')
    arts = tmp_path / 'arts.csv'
    pd.DataFrame({'law': ['근로기준법'] * 3, 'article_id': ['근로기준법 제23조', '근로기준법 제26조', '근로기준법 제27조'],
                  'article_no': ['제23조', '제26조', '제27조'], 'title': ['해고 등의 제한', '해고의 예고', '해고사유 등의 서면통지'],
                  'deleted': 0, 'text': ['본문23', '본문26', '본문27']}).to_csv(arts, index=False, encoding='utf-8-sig')
    lines_ = feedback_export.export(out, tmp_path / 'out', articles=arts)
    read = lambda n: pd.read_csv(tmp_path / 'out' / f'{n}.csv', dtype=str, keep_default_na=False, encoding='utf-8-sig')
    q, pairs, sft, kto = (read(n) for n in ('fb_questions', 'fb_emb_pairs', 'fb_sft', 'fb_kto'))
    assert sorted(q.feedback_id) == ['a', 'b', 'c']                      # 검수 안 한 d, v1 기록은 없음
    assert list(pairs.gold) == ['근로기준법 제26조', '근로기준법 제26조', '근로기준법 제23조']
    assert pairs.positive.iloc[0] == '근로기준법 제26조(해고의 예고)\n본문26'
    assert list(pairs.negative) == ['', hits[0]['doc'], hits[0]['doc']]   # reason=retrieval만 검색 1위가 hard negative
    assert list(sft.feedback_id) == ['a'] and list(kto.label) == ['True', 'False', 'False']
    from lawbot.llm import build_prompt_v4
    assert sft.prompt.iloc[0] == build_prompt_v4(hits, '질문A') and sft.completion.iloc[0] == '【결론】\n답'
    assert set(q.split) <= {'train', 'val'} and 'v4 프로필이 아니어서 뺌 1' in '\n'.join(lines_)
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in (tmp_path / 'out').iterdir())
    d.loc['c', 'h_gold'] = '제26조'                                       # 법령명 없는 정답 조문
    d.reset_index().to_csv(out, index=False, encoding='utf-8-sig')
    with pytest.raises(ValueError, match='법령명 제N조'):
        feedback_export.export(out, tmp_path / 'out', articles=arts)
    d.loc['c', ['h_gold', 'h_ok']] = ['', '애매']
    d.reset_index().to_csv(out, index=False, encoding='utf-8-sig')
    with pytest.raises(ValueError, match='h_ok'):
        feedback_export.export(out, tmp_path / 'out', articles=arts)

def test_split_is_deterministic():
    ids = [f'id{i}' for i in range(50)]
    s = feedback_export.assign_split(ids)
    assert s == feedback_export.assign_split(list(reversed(ids))) and sum(v == 'val' for v in s.values()) == 5

# ---- 하위법령 첨부(main #26)와 함께 ----
def test_decree_turn_record(env, tmp_path):
    """T11: 첨부 모델(Pro) 턴의 hits에 하위법령 kind·parent_law가 남고, 첨부 설정·수·측정값이 기록된다"""
    import test_decree as td
    env.setattr(td.RR, 'SCORES', {})
    env.setattr(td.RR, 'ERROR', None)
    a = td.make()
    fb = feedback.Feedback(tmp_path)
    chat = app_mod.make_chat(a, fb)
    q = '해고 예고?'
    h = turn(chat, [], question=q, model='pro', fmt='concise')
    _, hits, _, st = list(service.ask(a, q, 'pro', fmt='concise'))[-1]
    assert len(hits) > 3 and st['n_decree'] == len(hits) - 3
    row = fb.on_like('s1', h, 1, [h[1]['content']], True)
    assert row['hits'] == json.loads(json.dumps(hits, default=feedback.jsonable, ensure_ascii=False))
    assert [x.get('kind') for x in row['hits'][3:]] and all(x['kind'] in ('시행령', '시행규칙') for x in row['hits'][3:])
    assert all('kind' not in x for x in row['hits'][:3]) and row['hits'][3]['parent_law']
    assert row['decree'] == {'tau': 0.9, 'k': 2} and row['n_decree'] == st['n_decree']
    assert row['timing']['decree_rerank'] is not None
    h = turn(chat, h, question=q, model='fast', fmt='full')
    row = fb.on_like('s1', h, 3, [h[3]['content']], True)
    assert row['decree'] is None and row['n_decree'] is None and len(row['hits']) == 3

def test_export_with_decree_hits(env, tmp_path):
    """T12: 하위법령 hit가 있는 기록: 조문 대조 경고 없음, 프롬프트에 하위법령 블록, h_gold에 하위법령 조문"""
    hits = [{'law': '근로기준법', 'article_id': '제26조', 'title': '해고의 예고', 'score': 0.9,
             'doc': '근로기준법 제26조(해고의 예고)\n본문26'},
            {'law': '근로기준법 시행령', 'article_id': '제2조', 'title': '평균임금의 계산', 'score': 0.7,
             'doc': '근로기준법 시행령 제2조(평균임금의 계산)\n본문시2', 'kind': '시행령', 'parent_law': '근로기준법'}]
    write_rows(tmp_path / 'fb', [fb_row('a', '질문A', hits=hits)])
    out = tmp_path / 'review.csv'
    feedback_review.review([tmp_path / 'fb'], out, evals=[])
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    assert d.loc[0, 'hits'] == '근로기준법 제26조; 근로기준법 시행령 제2조'
    d.loc[0, ['h_ok', 'h_gold']] = ['맞음', '근로기준법 시행령 제2조']
    d.to_csv(out, index=False, encoding='utf-8-sig')
    law = tmp_path / 'law.csv'
    pd.DataFrame({'law': ['근로기준법'], 'article_id': ['근로기준법 제26조'], 'article_no': ['제26조'],
                  'title': ['해고의 예고'], 'deleted': 0, 'text': ['본문26']}).to_csv(law, index=False, encoding='utf-8-sig')
    dec = tmp_path / 'dec.csv'
    pd.DataFrame({'law': ['근로기준법', '근로기준법 시행령'], 'article_id': ['근로기준법 제26조', '근로기준법 시행령 제2조'],
                  'article_no': ['제26조', '제2조'], 'title': ['해고의 예고', '평균임금의 계산'], 'deleted': 0,
                  'text': ['본문26', '본문시2'], 'kind': ['법률', '시행령']}).to_csv(dec, index=False, encoding='utf-8-sig')
    text = '\n'.join(feedback_export.export(out, tmp_path / 'out', articles=[law, dec]))
    assert '다른 기록' not in text
    read = lambda n: pd.read_csv(tmp_path / 'out' / f'{n}.csv', dtype=str, keep_default_na=False, encoding='utf-8-sig')
    from lawbot.llm import build_prompt_v4
    sft, pairs = read('fb_sft'), read('fb_emb_pairs')
    assert sft.prompt.iloc[0] == build_prompt_v4(hits, '질문A') and '[2] 근로기준법 시행령 제2조' in sft.prompt.iloc[0]
    assert list(pairs.gold) == ['근로기준법 시행령 제2조'] and pairs.positive.iloc[0] == hits[1]['doc']
    with pytest.raises(ValueError, match='조문 CSV에 없음'):           # 법률 CSV만: 하위법령 정답 조문을 찾을 수 없음
        feedback_export.export(out, tmp_path / 'out2', articles=law)
    d.loc[0, 'h_gold'] = ''
    d.to_csv(out, index=False, encoding='utf-8-sig')
    assert '다른 기록 1' in '\n'.join(feedback_export.export(out, tmp_path / 'out2', articles=law))   # 하위법령 hit 대조 불가

def test_export_default_articles_include_decrees():
    from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES
    assert feedback_export.ARTICLES == (V4_ARTICLES, V4_DECREE_ARTICLES)
    docs = feedback_export.article_docs(feedback_export.ARTICLES)
    assert ('근로기준법', '제26조') in docs and ('근로기준법 시행령', '제2조') in docs
