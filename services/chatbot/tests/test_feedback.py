# tests/test_feedback.py  →  답변 피드백(B6): 턴 기록·저장 줄·검수·내보내기 (가짜 모델, 피드백 파일은 tmp_path에만)
# 화면 버튼과 HTTP 경로는 test_web.py, 화면 표시는 frontend의 Vitest
import hashlib
import json
import stat
import subprocess
import sys
import threading

import pandas as pd
import pytest

import lawbot.service as service
from chat_support import run
from fakes import FakeLLM, FakeRetriever
from lawbot import feedback, settings, web
from lawbot.db import migrate
from lawbot.db.connection import connect
from lawbot.db.feedback_import import prepare_feedback, write_feedback
from lawbot.paths import ROOT
from lawbot_tools import feedback_export, feedback_review

QUESTION = '해고 예고 없이 해고되면 어떻게 하나요 고유문구XYZ'
CHUNKS = ['【결론】\n예고해야', ' 합니다.\n\n【근거 조문】\n근로기준법 제26조\n\n【설명】\n- 설명']
LEGACY_KEYS = set(
    'feedback_id turn_id ts label reason question hits system answer_raw answer_shown model fmt refused '
    'n_cited citations timing decree n_decree instance mode profile dataset_version commit request_id'.split()
)


@pytest.fixture
def feedback_db(env, tmp_path):
    from conftest import set_auth

    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    set_auth(env)
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_DB', str(path))
    env.setattr(feedback, 'DEFAULT_DIR', tmp_path / 'fallback')
    return path, settings.load(web=True).feedback


def local_app(env):
    env.setattr(FakeLLM, 'chunks', CHUNKS)
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setenv('LAWBOT_MAX_RESIDENT', '2')
    return service.load_app()


def lines(folder):
    return [
        json.loads(x)
        for f in sorted(folder.glob('feedback_*.jsonl'))
        for x in f.read_text(encoding='utf-8').splitlines()
    ]


def turn(a, fb, question=QUESTION, model='fast', fmt='full', session='s1'):
    """한 턴을 돌리고 (turn_id, 화면 글). 웹앱처럼 끝난 턴을 세션의 다음 순번에 기억한다"""
    done = run(a, question, model, fmt, remember=lambda record: fb.remember_turn(session, record)).done
    return done['turn_id'], done['text']


# ---- T1·T2·T3: 켜기·끄기, 화면에 알리는 이유 ----
def test_disabled_by_default(env, tmp_path):
    assert feedback.from_env() is None
    assert web.config_data(local_app(env), None)['feedback'] == {'enabled': False, 'reasons': []}  # 버튼 없음
    env.setenv('LAWBOT_FEEDBACK', 'yes')
    with pytest.raises(ValueError, match='LAWBOT_FEEDBACK'):
        feedback.enabled()


def test_reasons_offered_to_screen(env, tmp_path):
    cfg = web.config_data(local_app(env), feedback.Feedback(tmp_path))['feedback']
    assert cfg['enabled'] and [(r['label'], r['value']) for r in cfg['reasons']] == [
        ('검색 오류', 'retrieval'),
        ('답변 오류', 'answer'),
        ('불필요한 거절', 'false_refusal'),
        ('기타', 'other'),
    ]  # 👎는 없음 (👍와 깃발만)


# ---- T4·T5: 저장 기록 ----
def test_like_saves_full_turn(env, feedback_db):
    a = local_app(env)
    path, fb = feedback_db
    tid, shown = turn(a, fb, fmt='concise', model='pro')
    expected = list(service.ask(a, QUESTION, 'pro', fmt='concise'))[-1]
    row = fb.save('s1', tid, 'like')
    assert row is not None
    with connect(path) as conn:
        stored = conn.execute(
            'SELECT record, origin, question, articles_sha256, corpus_release_id, commit_sha FROM feedbacks'
        ).fetchall()
    assert len(stored) == 1
    record, origin, question, digest, release, commit_sha = stored[0]
    assert json.loads(record) == row and LEGACY_KEYS <= row.keys()
    assert (origin, question, digest, release, commit_sha) == (
        'web',
        QUESTION,
        row['articles_sha256'],
        None,
        row['commit'],
    )
    assert not (path.parent.parent / 'fallback').exists()
    assert (row['label'], row['reason'], row['question'], row['model'], row['fmt'], row['turn_id']) == (
        'like',
        None,
        QUESTION,
        'pro',
        'concise',
        tid,
    )
    assert row['hits'] == expected[1] and row['answer_raw'] == expected[2]
    assert row['system'] == service.system_text(a.profile, a.models.specs['pro'])  # Pro 간결형 system 파일
    assert row['answer_shown'] == shown != row['answer_raw']  # 간결형: 화면 글은 후처리
    assert row['refused'] is False and row['citations'] == [
        {'law': '근로기준법', 'article_id': '제26조', 'verdict': 'ok'}
    ]
    assert (row['mode'], row['profile'], row['dataset_version']) == ('api', 'v4', 'v4')
    assert row['request_id'] and row['timing']['total'] is not None and row['commit']
    assert row['articles_sha256'] == hashlib.sha256(a.profile.articles.read_bytes()).hexdigest()
    assert row['emb_model'] == a.profile.emb_model and row['rerank_model'] is None
    assert row['chat_model'] == a.models.specs['pro'].model and row['corpus_release_id'] is None
    assert row['system_sha256'] == hashlib.sha256(row['system'].encode()).hexdigest()


def test_full_format_system_and_default(env, tmp_path):
    a = local_app(env)
    fb = feedback.Feedback(tmp_path)
    t1, _ = turn(a, fb, model='pro', fmt='full')  # 간결형 모델에서 '전체' → 4항목 system
    t2, _ = turn(a, fb, model='fast', fmt='full')  # Fast: 모델 기본 system (프로필 4항목 system 파일)
    r1, r2 = fb.save('s1', t1, 'like'), fb.save('s1', t2, 'like')
    assert r1['system'] == a.full_system and r2['system'] == service.system_text(a.profile, a.models.specs['fast'])


def test_turn_provenance_matches_loaded_csv_and_system(env, tmp_path):
    csv_path, system_path = tmp_path / 'articles.csv', tmp_path / 'system.md'
    csv = (ROOT / 'data/v4/articles_v4.csv').read_bytes()
    csv_path.write_bytes(csv)
    system_path.write_text('initial system')
    env.setenv('LAWBOT_V4_ARTICLES', str(csv_path))
    env.setenv('LAWBOT_PRO_SYSTEM', str(system_path))

    class ProvenanceLLM(FakeLLM):
        def stream(self, question, hits, system=None):
            from lawbot.llm import response

            yield response(f'【결론】예고해야 합니다.\n【근거 조문】\n{hits[0]["law"]} {hits[0]["article_id"]}')

    env.setattr(service, 'LLM', ProvenanceLLM)
    a = service.load_app()
    a.models.get('pro')
    csv_path.write_bytes(csv + b'\n')
    system_path.write_text('edited system')
    fb = feedback.Feedback(tmp_path / 'fb')
    tid, _ = turn(a, fb, model='pro', fmt='concise')
    row = fb.save('s1', tid, 'like')
    assert row['system'] == 'initial system'
    assert row['system_sha256'] == hashlib.sha256(b'initial system').hexdigest()
    assert row['articles_sha256'] == hashlib.sha256(csv).hexdigest()


def test_labels_reasons_and_dedup(env, tmp_path):
    a = local_app(env)
    fb = feedback.Feedback(tmp_path)
    first, _ = turn(a, fb)
    second, _ = turn(a, fb, question='두 번째 질문')
    t = 100.0
    for label, reason in (
        ('like', None),
        ('flag', 'retrieval'),
        ('flag', 'answer'),
        ('flag', 'false_refusal'),
        ('flag', 'other'),
        ('cleared', None),
    ):
        row = fb.save('s1', second, label, reason, now=t)
        assert (row['label'], row['reason'], row['question']) == (label, reason, '두 번째 질문')
        assert fb.save('s1', second, label, reason, now=t + 0.5) is None  # 같은 버튼이 바로 다시 오면 한 번만
        t += 10
    assert fb.save('s1', second, 'like', now=t + 2.5) is not None  # 2초가 지나면 다시 저장
    old = fb.save('s1', first, 'flag', 'answer', now=t)  # 이전 답변의 버튼은 그 답변의 turn_id로
    assert old['question'] == QUESTION and old['turn_id'] == first != second
    for label, reason in (('dislike', None), ('flag', '기타'), ('flag', None), ('like', 'answer')):
        with pytest.raises(ValueError):
            fb.save('s1', second, label, reason)
    assert len(lines(tmp_path)) == 8


def test_folder_made_only_when_saving(env, tmp_path):
    """피드백을 켜도 버튼을 누르기 전에는 파일·폴더를 만들지 않는다"""
    from conftest import set_auth

    set_auth(env)
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_FEEDBACK_DIR', str(tmp_path / 'fb'))
    env.setattr(web, 'load_app', lambda *a: local_app(env))
    env.setattr(web, 'require_build', lambda dist: None)
    env.setattr(web.uvicorn, 'run', lambda *a, **kw: None)
    web.main([])
    assert not (tmp_path / 'fb').exists()


def test_unconfigured_db_keeps_legacy_jsonl(env, tmp_path):
    from conftest import set_auth

    set_auth(env)
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_FEEDBACK_DIR', str(tmp_path))
    cfg = settings.load(web=True)
    tid, _ = turn(local_app(env), cfg.feedback)
    row = cfg.feedback.save('s1', tid, 'like')
    assert cfg.db is None and lines(tmp_path) == [row]


# ---- T6: 기록이 없는 답 ----
def test_unknown_turn_not_saved(env, tmp_path, capsys):
    a = local_app(env)
    fb = feedback.Feedback(tmp_path)
    tid, _ = turn(a, fb)
    restarted = feedback.Feedback(tmp_path)  # 앱 재시작: 메모리 기록 없음
    for store, session in ((restarted, 's1'), (fb, 's2')):  # 다른 세션
        with pytest.raises(LookupError):
            store.save(session, tid, 'like')
    assert lines(tmp_path) == []
    out = capsys.readouterr().out
    assert '고유문구XYZ' not in out and '예고해야' not in out  # 콘솔에 질문·답변 없음


def test_retry_is_a_new_turn(env, tmp_path):
    a = local_app(env)
    fb = feedback.Feedback(tmp_path)
    t1, _ = turn(a, fb)
    t2, _ = turn(a, fb, question='고쳐 쓴 질문')  # 다시 묻기·고쳐 묻기: 새 턴
    assert t1 != t2
    assert fb.save('s1', t2, 'like')['question'] == '고쳐 쓴 질문'
    assert fb.save('s1', t1, 'like')['question'] == QUESTION


def test_memory_limits(env, tmp_path):
    fb = feedback.Feedback(tmp_path)
    rec = {'request_id': 'r'}
    for n in range(feedback.TURNS_PER_SESSION + 5):
        fb.remember('s', n, rec)
    assert len(fb.turns['s']) == feedback.TURNS_PER_SESSION and 0 not in fb.turns['s']
    for i in range(feedback.MAX_SESSIONS + 3):
        fb.remember_turn(f'x{i}', rec)
    assert len(fb.turns) == len(fb.counts) == feedback.MAX_SESSIONS and 's' not in fb.turns


# ---- T7: 저장 폴더·권한 ----
def test_dir_must_be_ignored(env, tmp_path):
    feedback.check_private(ROOT / 'feedback')  # gitignore
    feedback.check_private(tmp_path / 'fb')  # 레포 밖
    with pytest.raises(RuntimeError, match='gitignore'):
        feedback.check_private(ROOT / 'services' / 'fb_test')
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_FEEDBACK_DIR', 'docs/fb')  # 상대 경로는 레포 최상위 기준
    with pytest.raises(RuntimeError, match='실행을 거부'):
        feedback.from_env()
    env.delenv('LAWBOT_FEEDBACK_DIR')
    assert feedback.from_env().folder == ROOT / 'feedback'
    assert not (ROOT / 'services' / 'fb_test').exists()


def test_file_mode_600(env, tmp_path):
    fb = feedback.Feedback(tmp_path / 'fb')
    fb.write({'a': 1})
    [f] = (tmp_path / 'fb').glob('feedback_*.jsonl')
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / 'fb').stat().st_mode) == 0o700


# ---- T8: import ----


def test_feedback_module_has_no_model_imports():
    code = (
        'import sys, lawbot.feedback, lawbot_tools.feedback_review\n'
        "print(','.join(m for m in ('mlx', 'mlx_lm', 'torch', 'sentence_transformers', 'fastapi', 'lawbot.llm') "
        'if m in sys.modules))'
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ''


# ---- T10: 동시 쓰기 ----
def test_concurrent_writes_do_not_interleave(tmp_path):
    fb = feedback.Feedback(tmp_path)
    big = '가' * 200_000
    threads = [
        threading.Thread(target=lambda i=i: [fb.write({'i': i, 'j': j, 'x': big}) for j in range(5)]) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = lines(tmp_path)
    assert len(rows) == 20 and all(r['x'] == big for r in rows)


# ---- T9: 검수 ----
def fb_row(fid, question, label='like', reason=None, ts='2026-11-01T10:00:00', turn=None, profile='v4', hits=None):
    hits = hits or [
        {
            'law': '근로기준법',
            'article_id': '제26조',
            'title': '해고의 예고',
            'score': 0.9,
            'doc': '근로기준법 제26조(해고의 예고)\n본문',
        }
    ]
    return {
        'feedback_id': fid,
        'turn_id': turn or fid,
        'ts': ts,
        'label': label,
        'reason': reason,
        'question': question,
        'hits': hits,
        'system': 'S',
        'answer_raw': '【결론】\n답',
        'answer_shown': '답',
        'model': 'pro',
        'fmt': 'concise',
        'refused': False,
        'profile': profile,
    }


def write_rows(folder, rows, name='feedback_2026-11.jsonl'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows) + '{"깨진', encoding='utf-8'
    )


def test_review_db_keeps_legacy_csv_and_latest_rules(env, tmp_path):
    rows = [
        fb_row('first', '첫 질문', turn='one'),
        fb_row('cleared', '두 번째 질문', 'cleared', ts='2026-11-01T10:00:01', turn='two'),
        fb_row('tie-winner', '첫 질문', 'flag', 'answer', turn='one'),
        fb_row('earlier', '두 번째 질문', turn='two'),
        fb_row('third', '세 번째 질문', turn='three'),
    ]
    source = tmp_path / 'source'
    write_rows(source, rows)
    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    with connect(path, write=True) as conn:
        write_feedback(conn, prepare_feedback(list(source.glob('*.jsonl'))))

    env.setattr(feedback_review, 'eval_sets', lambda: [])
    from_jsonl, from_db = tmp_path / 'jsonl.csv', tmp_path / 'db.csv'
    feedback_review.main([str(source), '--out', str(from_jsonl)])
    feedback_review.main(['--db', str(path), '--out', str(from_db)])
    assert from_db.read_bytes() == from_jsonl.read_bytes()
    assert from_db.with_suffix('.jsonl').read_bytes() == from_jsonl.with_suffix('.jsonl').read_bytes()
    assert pd.read_csv(from_db).feedback_id.tolist() == ['tie-winner', 'third']


EVALS = [
    ('v4_test', 'Q0001', '회사가 해고 예고를 안 했어요. 어떻게 하나요?'),
    ('oos', 'OOS001', '내일 서울 날씨 어때요?'),
]


def test_review_dedup_and_overlap(env, tmp_path):
    write_rows(
        tmp_path / 'prod',
        [
            fb_row('a', '회사가 해고 예고를 안 했어요. 어떻게 하나요?'),  # 평가 문항과 같음
            fb_row('b', '회사가  해고예고를 안했어요 어떻게하나요'),  # 공백·문장부호만 다름
            fb_row('c', '회사가 해고 예고를 안 했어요. 어떻게 하나여?'),  # 유사도 0.9 이상
            fb_row('d1', '연차휴가는 며칠인가요?', ts='2026-11-01T10:00:00', turn='t'),
            fb_row(
                'd2', '연차휴가는 며칠인가요?', 'flag', 'retrieval', ts='2026-11-01T10:00:05', turn='t'
            ),  # 같은 turn: 마지막
            fb_row('e1', '퇴직금은 언제 주나요?', turn='u'),
            fb_row('e2', '퇴직금은 언제 주나요?', 'cleared', turn='u', ts='2026-11-01T11:00:00'),
        ],
    )  # 해제: 뺌
    write_rows(tmp_path / 'dev', [fb_row('f', '야간 근로 수당은?')])
    out = tmp_path / 'feedback_review_2026-11-30.csv'
    lines_ = feedback_review.review([tmp_path / 'prod', tmp_path / 'dev'], out, evals=EVALS)
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    assert sorted(d['feedback_id']) == ['d2', 'f']
    assert list(d.columns[-4:]) == ['h_ok', 'h_gold', 'h_scope', 'h_note'] and d.loc[0, 'hits'] == '근로기준법 제26조'
    text = '\n'.join(lines_)
    assert '평가 데이터와 겹쳐 뺌 3 (완전 일치 2, 유사도 0.9 이상 1)' in text and 'v4_test:Q0001' in text
    assert '깨진 줄 2' in text and '선택 해제로 뺌 1' in text
    assert '회사가' not in text  # 출력에 질문 원문 없음
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert len(feedback_review.companion(out).read_text(encoding='utf-8').splitlines()) == 2
    d.loc[d.feedback_id == 'f', 'h_ok'] = '맞음'  # 다시 돌려도 사람이 채운 칸을 이어받음
    d.to_csv(tmp_path / 'prev.csv', index=False, encoding='utf-8-sig')
    feedback_review.review([tmp_path / 'prod', tmp_path / 'dev'], out, keep=tmp_path / 'prev.csv', evals=EVALS)
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    assert d.set_index('feedback_id').loc['f', 'h_ok'] == '맞음'
    with pytest.raises(RuntimeError, match='gitignore'):  # 레포 안의 무시되지 않는 경로에는 안 씀
        feedback_review.review([tmp_path / 'dev'], ROOT / 'docs' / 'review.csv', evals=EVALS)


def test_review_real_eval_sets():
    ov = feedback_review.Overlap(feedback_review.eval_sets())
    t = pd.read_csv(ROOT / 'data/v4/qa_full_v4.csv', encoding='utf-8-sig').merge(
        pd.read_csv(ROOT / 'data/v4/split_v4.csv', encoding='utf-8-sig')[['id', 'split_emb']], on='id'
    )
    test_q = t[(t.split_emb == 'test') & t.eval_cat.isin(['A_lsa', 'B_newlaw'])].iloc[0]
    assert ov.match(test_q.question + ' ') == (f'v4_test:{test_q.id}', 'exact')
    assert sum(src == 'v4_test' for src, _, _ in feedback_review.eval_sets()) == 2075


def test_review_excludes_val_and_decree_sets():
    """T13: v4와 하위법령 질문의 양쪽 검증 분할을 보호한다"""
    evals = feedback_review.eval_sets()
    count = lambda s: sum(src == s for src, _, _ in evals)
    # sllm-only validation adds 444 v4 and 181 decree questions.
    assert (count('v4_val'), count('decree_val'), count('decree_test')) == (1554, 625, 1880)
    ov = feedback_review.Overlap(evals)
    from lawbot_tools.eval_v4 import load_test

    v = load_test('val').iloc[0]
    assert ov.match(v.question) == (f'v4_val:{v.id}', 'exact')
    qa = pd.read_csv(ROOT / 'data/decree_exp/qa_decree_v4.csv', encoding='utf-8-sig').merge(
        pd.read_csv(ROOT / 'data/decree_exp/split_decree_v4.csv', encoding='utf-8-sig')[
            ['id', 'split_emb', 'split_sllm']
        ],
        on='id',
    )
    for split in ('val', 'test'):
        q = qa[qa.split_emb == split].iloc[0]
        assert ov.match(q.question)[0].startswith('decree_')
    ids = {i for src, i, _ in evals if src.startswith('decree_')}
    assert not ids & set(qa.loc[(qa.split_emb == 'train') & (qa.split_sllm == 'train'), 'id'])


def test_review_eval_sets_include_v5_and_reasoning():
    evals = feedback_review.eval_sets()
    assert {source: sum(s == source for s, _, _ in evals) for source in ('v5_test', 'v5_val', 'reasoning_v5')} == {
        'v5_test': 3420,
        'v5_val': 2907,  # 855 sllm-only validation questions
        'reasoning_v5': 30,
    }


def test_review_excludes_feedback_matching_v5_test(tmp_path):
    from lawbot_tools.eval_v4 import load_tax

    question = load_tax('test').iloc[0]
    write_rows(tmp_path / 'fb', [fb_row('v5-leak', question.question, profile='v5')])
    out = tmp_path / 'review.csv'
    summary = '\n'.join(feedback_review.review([tmp_path / 'fb'], out))
    assert f'v5-leak ↔ v5_test:{question.id} (exact)' in summary
    assert pd.read_csv(out, encoding='utf-8-sig').empty
    assert feedback_review.companion(out).read_text(encoding='utf-8') == ''


# ---- 내보내기 ----
def test_export(env, tmp_path):
    hits = [
        {
            'law': '근로기준법',
            'article_id': '제27조',
            'title': '해고사유 등의 서면통지',
            'score': 0.9,
            'doc': '근로기준법 제27조(해고사유 등의 서면통지)\n본문27',
        },
        {
            'law': '근로기준법',
            'article_id': '제26조',
            'title': '해고의 예고',
            'score': 0.8,
            'doc': '근로기준법 제26조(해고의 예고)\n본문26',
        },
    ]
    write_rows(
        tmp_path / 'fb',
        [
            fb_row('a', '질문A', hits=hits),
            fb_row('b', '질문B', 'flag', 'retrieval', hits=hits),
            fb_row('c', '질문C', 'flag', 'answer', hits=hits),
            fb_row('d', '질문D', hits=hits),
            fb_row('v1', '질문E', profile='v1', hits=hits),
        ],
    )
    out = tmp_path / 'review.csv'
    feedback_review.review([tmp_path / 'fb'], out, evals=[])
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig').set_index('feedback_id')
    d.loc['a', ['h_ok', 'h_gold']] = ['맞음', '근로기준법제26조']  # 띄어쓰기 없이 써도 됨
    d.loc['b', ['h_ok', 'h_gold']] = ['틀림', '근로기준법 제26조; 근로기준법 제23조']
    d.loc['c', 'h_ok'] = '틀림'
    d.loc['v1', 'h_ok'] = '맞음'
    d.reset_index().to_csv(out, index=False, encoding='utf-8-sig')
    arts = tmp_path / 'arts.csv'
    pd.DataFrame(
        {
            'law': ['근로기준법'] * 3,
            'article_id': ['근로기준법 제23조', '근로기준법 제26조', '근로기준법 제27조'],
            'article_no': ['제23조', '제26조', '제27조'],
            'title': ['해고 등의 제한', '해고의 예고', '해고사유 등의 서면통지'],
            'deleted': 0,
            'text': ['본문23', '본문26', '본문27'],
        }
    ).to_csv(arts, index=False, encoding='utf-8-sig')
    lines_ = feedback_export.export(out, tmp_path / 'out', articles=arts)
    read = lambda n: pd.read_csv(tmp_path / 'out' / f'{n}.csv', dtype=str, keep_default_na=False, encoding='utf-8-sig')
    q, pairs, sft, kto = (read(n) for n in ('fb_questions', 'fb_emb_pairs', 'fb_sft', 'fb_kto'))
    assert sorted(q.feedback_id) == ['a', 'b', 'c']  # 검수 안 한 d, v1 기록은 없음
    assert list(pairs.gold) == ['근로기준법 제26조', '근로기준법 제26조', '근로기준법 제23조']
    assert pairs.positive.iloc[0] == '근로기준법 제26조(해고의 예고)\n본문26'
    assert list(pairs.negative) == ['', hits[0]['doc'], hits[0]['doc']]  # reason=retrieval만 검색 1위가 hard negative
    assert list(sft.feedback_id) == ['a'] and list(kto.label) == ['True', 'False', 'False']
    from lawbot.llm import build_prompt

    assert sft.prompt.iloc[0] == build_prompt(hits, '질문A') and sft.completion.iloc[0] == '【결론】\n답'
    assert set(q.split) <= {'train', 'val'}
    assert "지원하지 않는 프로필이라 뺌 1: ['v1'] (지원 범위 v4·v5)" in lines_
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in (tmp_path / 'out').iterdir())
    d.loc['c', 'h_gold'] = '제26조'  # 법령명 없는 정답 조문
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
    env.setattr(td.RR, 'model', 'test-reranker', raising=False)
    a = td.make()
    fb = feedback.Feedback(tmp_path)
    q = '해고 예고?'
    tid, _ = turn(a, fb, question=q, model='pro', fmt='concise')
    _, hits, _, st = list(service.ask(a, q, 'pro', fmt='concise'))[-1]
    assert len(hits) > 3 and st['n_decree'] == len(hits) - 3
    row = fb.save('s1', tid, 'like')
    assert row['hits'] == json.loads(json.dumps(hits, default=feedback.jsonable, ensure_ascii=False))
    assert row['rerank_model'] == a.reranker.model
    assert [x.get('kind') for x in row['hits'][3:]] and all(
        x['kind'] in ('시행령', '시행규칙') for x in row['hits'][3:]
    )
    assert all('kind' not in x for x in row['hits'][:3]) and row['hits'][3]['parent_law']
    assert row['decree'] == {'tau': 0.9, 'k': 2} and row['n_decree'] == st['n_decree']
    assert row['timing']['decree_rerank'] is not None
    tid, _ = turn(a, fb, question=q, model='fast', fmt='full')
    row = fb.save('s1', tid, 'like')
    assert row['decree'] is None and row['n_decree'] is None and len(row['hits']) == 3


def test_export_with_decree_hits(env, tmp_path):
    """T12: 하위법령 hit가 있는 기록: 조문 대조 경고 없음, 프롬프트에 하위법령 블록, h_gold에 하위법령 조문"""
    hits = [
        {
            'law': '근로기준법',
            'article_id': '제26조',
            'title': '해고의 예고',
            'score': 0.9,
            'doc': '근로기준법 제26조(해고의 예고)\n본문26',
        },
        {
            'law': '근로기준법 시행령',
            'article_id': '제2조',
            'title': '평균임금의 계산',
            'score': 0.7,
            'doc': '근로기준법 시행령 제2조(평균임금의 계산)\n본문시2',
            'kind': '시행령',
            'parent_law': '근로기준법',
        },
    ]
    write_rows(tmp_path / 'fb', [fb_row('a', '질문A', hits=hits)])
    out = tmp_path / 'review.csv'
    feedback_review.review([tmp_path / 'fb'], out, evals=[])
    d = pd.read_csv(out, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    assert d.loc[0, 'hits'] == '근로기준법 제26조; 근로기준법 시행령 제2조'
    d.loc[0, ['h_ok', 'h_gold']] = ['맞음', '근로기준법 시행령 제2조']
    d.to_csv(out, index=False, encoding='utf-8-sig')
    law = tmp_path / 'law.csv'
    pd.DataFrame(
        {
            'law': ['근로기준법'],
            'article_id': ['근로기준법 제26조'],
            'article_no': ['제26조'],
            'title': ['해고의 예고'],
            'deleted': 0,
            'text': ['본문26'],
        }
    ).to_csv(law, index=False, encoding='utf-8-sig')
    dec = tmp_path / 'dec.csv'
    pd.DataFrame(
        {
            'law': ['근로기준법', '근로기준법 시행령'],
            'article_id': ['근로기준법 제26조', '근로기준법 시행령 제2조'],
            'article_no': ['제26조', '제2조'],
            'title': ['해고의 예고', '평균임금의 계산'],
            'deleted': 0,
            'text': ['본문26', '본문시2'],
            'kind': ['법률', '시행령'],
        }
    ).to_csv(dec, index=False, encoding='utf-8-sig')
    text = '\n'.join(feedback_export.export(out, tmp_path / 'out', articles=[law, dec]))
    assert '다른 기록' not in text
    read = lambda n: pd.read_csv(tmp_path / 'out' / f'{n}.csv', dtype=str, keep_default_na=False, encoding='utf-8-sig')
    from lawbot.llm import build_prompt

    sft, pairs = read('fb_sft'), read('fb_emb_pairs')
    assert sft.prompt.iloc[0] == build_prompt(hits, '질문A') and '[2] 근로기준법 시행령 제2조' in sft.prompt.iloc[0]
    assert list(pairs.gold) == ['근로기준법 시행령 제2조'] and pairs.positive.iloc[0] == hits[1]['doc']
    with pytest.raises(ValueError, match='조문 CSV에 없음'):  # 법률 CSV만: 하위법령 정답 조문을 찾을 수 없음
        feedback_export.export(out, tmp_path / 'out2', articles=law)
    d.loc[0, 'h_gold'] = ''
    d.to_csv(out, index=False, encoding='utf-8-sig')
    assert '다른 기록 1' in '\n'.join(
        feedback_export.export(out, tmp_path / 'out2', articles=law)
    )  # 하위법령 hit 대조 불가


def test_export_default_articles_include_decrees():
    from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES

    assert feedback_export.ARTICLES == (V4_ARTICLES, V4_DECREE_ARTICLES)
    docs = feedback_export.article_docs(feedback_export.ARTICLES)
    assert ('근로기준법', '제26조') in docs and ('근로기준법 시행령', '제2조') in docs


@pytest.mark.parametrize('include_v4', [True, False], ids=['mixed', 'v5-only'])
def test_export_cli_accepts_v5_gold_and_counts_unsupported_profiles(tmp_path, capsys, include_v4):
    tax_key = ('소득세법 시행령', '제38조')
    tax_doc = feedback_export.article_docs(ROOT / 'data/v5/law_articles_v5_decree.csv')[tax_key]
    entries = [
        (fb_row('v5', '질문B', profile='v5'), ' '.join(tax_key)),
        (fb_row('legacy', '질문C', profile='v1'), '알 수 없는 조문'),
    ]
    if include_v4:
        entries.insert(0, (fb_row('v4', '질문A'), '근로기준법 제26조'))
    write_rows(tmp_path / 'fb', [record for record, _ in entries])
    review = tmp_path / 'review.csv'
    feedback_review.review([tmp_path / 'fb'], review, evals=[])
    d = pd.read_csv(review, encoding='utf-8-sig', keep_default_na=False)
    d['h_ok'] = '맞음'
    d['h_gold'] = [gold for _, gold in entries]
    d.to_csv(review, index=False, encoding='utf-8-sig')

    out = tmp_path / 'out'
    feedback_export.main([str(review), '--out', str(out)])
    summary = capsys.readouterr().out
    assert "지원하지 않는 프로필이라 뺌 1: ['legacy'] (지원 범위 v4·v5)" in summary.splitlines()
    pairs = pd.read_csv(out / 'fb_emb_pairs.csv', encoding='utf-8-sig').set_index('feedback_id')
    expected_ids = ['v4', 'v5'] if include_v4 else ['v5']
    assert list(pairs.index) == expected_ids
    assert pairs.loc['v5', 'positive'] == tax_doc
    for name in ('fb_questions', 'fb_sft', 'fb_kto'):
        assert list(pd.read_csv(out / f'{name}.csv', encoding='utf-8-sig').feedback_id) == expected_ids


def test_export_uses_configured_corpus_of_each_record(env, tmp_path):
    gold = '근로기준법 제26조'
    hits = {}
    for profile in ('v4', 'v5'):
        csv = tmp_path / f'{profile}.csv'
        pd.DataFrame(
            [dict(law='근로기준법', article_id='제26조', title='해고의 예고', deleted=0, text=f'{profile} 본문')]
        ).to_csv(csv, index=False, encoding='utf-8-sig')
        env.setenv(f'LAWBOT_{profile.upper()}_ARTICLES', str(csv))
        env.setenv(f'LAWBOT_{profile.upper()}_DECREE_ARTICLES', str(csv))
        hits[profile] = [
            dict(
                law='근로기준법',
                article_id='제26조',
                title='해고의 예고',
                score=0.9,
                doc=f'{gold}(해고의 예고)\n{profile} 본문',
            )
        ]
    write_rows(tmp_path / 'fb', [fb_row(p, f'{p} 질문', profile=p, hits=hs) for p, hs in hits.items()])
    review = tmp_path / 'review.csv'
    feedback_review.review([tmp_path / 'fb'], review, evals=[])
    d = pd.read_csv(review, encoding='utf-8-sig', keep_default_na=False)
    d['h_gold'] = gold
    d.to_csv(review, index=False, encoding='utf-8-sig')

    out = tmp_path / 'out'
    summary = '\n'.join(feedback_export.export(review, out))
    assert '다른 기록' not in summary
    pairs = pd.read_csv(out / 'fb_emb_pairs.csv', encoding='utf-8-sig').set_index('feedback_id')
    assert pairs.positive.to_dict() == {p: hs[0]['doc'] for p, hs in hits.items()}
