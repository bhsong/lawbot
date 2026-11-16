"""Chromium and the real web/model-server apps with a synthetic reviewed answer."""

import json
import time

import numpy as np
import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from lawbot import deploy
from lawbot.cache_question_store import question_catalog, save_vectors
from lawbot.cache_seed import Protection
from lawbot.cache_seed_store import Answer, Handoff, Question, Reference, import_seed
from lawbot.cache_store import Store, init
from lawbot.corpus_store import active_release, active_token
from lawbot.db import cli
from lawbot.db.connection import connect
from lawbot.exact_cache import Snapshot, digest
from lawbot.service import system_text
from lawbot.view import V5_EXAMPLES
from test_exact_cache import ID, RAW
from test_migrate_csv_to_sqlite import run as migrate

pytestmark = pytest.mark.e2e_stub


def request_row(stack, number):
    deadline = time.monotonic() + e2e.ANSWER_TIMEOUT / 1000
    while time.monotonic() < deadline:
        rows = e2e.request_log(stack.request_log)
        if len(rows) >= number:
            return rows[number - 1]
        time.sleep(0.02)
    pytest.fail(f'request log row {number} was not written')


def test_exact_cache_browser_flow(tmp_path, page, request):
    question = '연장근로 수당을 지급하는 대신 휴가를 주려면 어떤 절차가 필요한가요?'
    assert V5_EXAMPLES[0] == question
    corpus = tmp_path / 'private' / 'corpus.db'
    cli.main(['init', '--db', str(corpus)])
    profile = deploy.get_profile({'LAWBOT_PROFILE': 'v5'})
    assert (
        migrate(
            corpus, 'corpus', '--dir', profile.articles.parent, '--profile', 'v5', '--release-id', 'test', '--activate'
        )
        == 0
    )
    path = tmp_path / 'private' / 'exact.db'
    init(path)
    store = Store(path)
    snapshot = Snapshot.load(
        active_release(corpus, 'v5'),
        system=system_text(profile, profile.models[0]),
        combined=False,
        attach_decrees=False,
    )
    article = snapshot.articles[ID]
    ref = Reference(ID, article.text_hash)
    additional_id = '근로기준법 제27조'
    additional = Reference(additional_id, snapshot.articles[additional_id].text_hash)
    row = Answer('key', 'review-unit', ID, RAW, digest(RAW), (ref, additional), (additional,), {}, ref.sha256)
    handoff = Handoff('a' * 64, (row,), (Question('Q1314', row.key, question, {}),))
    token = active_token(corpus, 'v5')
    assert token is not None
    event = token[1]
    protection = Protection(frozenset(), {}, 'protected')
    report = import_seed(path, handoff, snapshot, event, protection)
    assert report['approved_questions'] == 1
    assert store.control().mode == 'off'
    approval = report['approval_id']
    assert approval == store.control().approval_id
    control, (approved,), _ = question_catalog(
        path, snapshot, profile.emb_model, e2e.STUB_MODEL_REVISION, profile.max_seq
    )
    save_vectors(
        path,
        control,
        profile.emb_model,
        e2e.STUB_MODEL_REVISION,
        profile.max_seq,
        [(approved, np.full(8, 8**-0.5, dtype=np.float32))],
        lambda: None,
    )
    store.set_mode('shadow', 'tester', 'test', approval)
    with connect(path) as conn:
        (answer_id,) = conn.execute('SELECT answer_id FROM cache_answers').fetchone()
    stack = e2e.Stack(
        tmp_path,
        3,
        LAWBOT_PROFILE='v5',
        LAWBOT_CORPUS='db',
        LAWBOT_DB=str(corpus),
        LAWBOT_EXACT_CACHE_DB=str(path),
        LAWBOT_FEEDBACK='1',
        LAWBOT_DECREE_ENABLED='0',
        LAWBOT_RERANKER_ENABLED='1',
        LAWBOT_SUGGESTIONS_TAU='-1',
    )
    try:
        assert stack.web.alive()
        denied = page.request.post(
            stack.url + '/api/chat', data={'question': question, 'model': 'pro', 'format': 'concise'}
        )
        assert denied.status == 401 and not e2e.request_log(stack.request_log)
        e2e.login(page, stack.url)
        e2e.ask(page, question)
        e2e.wait_done(page)
        expect(e2e.answer(page).last).to_contain_text('가짜 모델 답변입니다.')
        shadow = request_row(stack, 1)
        assert shadow['response_source'] == 'rag' and shadow['would_hit']
        assert shadow['call_counts'] == {'embedding': 1, 'search': 1, 'rerank': 1, 'llm': 1}
        assert page.get_by_test_id('reviewed-question').count() == 0

        store.set_mode('on', 'tester', 'test', approval)
        before_status_checks = stack.ms.output().count(' GET /v1/models')
        e2e.ask(page, V5_EXAMPLES[1])
        e2e.wait_done(page)
        recommendation = page.get_by_role('group', name='검토된 비슷한 질문').get_by_role('button', name=question)
        expect(recommendation).to_be_visible()
        suggestion_log = request_row(stack, 2)
        assert suggestion_log['suggestion_count'] == 1 and suggestion_log['suggestion_click'] is False
        assert suggestion_log['call_counts']['embedding'] == 1 and suggestion_log['suggestion_ms'] >= 0
        assert stack.ms.output().count(' GET /v1/models') - before_status_checks == 1
        format_group = page.get_by_role('radiogroup', name='답변 형식')
        other_name = format_group.get_by_role('radio', checked=False).inner_text()
        other_format = format_group.get_by_role('radio', name=other_name, exact=True)
        other_format.click()
        expect(other_format).to_have_attribute('aria-checked', 'true')
        selected_model = page.get_by_role('radiogroup', name='답변 모델').get_by_role('radio', checked=True)
        model_name = selected_model.inner_text()
        stack.stop_model_server()
        read_clicked = e2e.capture_response_body(page, '/api/chat')
        recommendation.click()
        clicked = request_row(stack, 3)
        expect(e2e.answer(page).last).to_contain_text('합성 결론입니다.')
        assert clicked['response_source'] == 'exact_cache' and clicked['suggestion_click'] is True
        assert set(clicked['call_counts'].values()) == {0}
        done = next(
            json.loads(event.split('data: ', 1)[1])
            for event in read_clicked().split('\n\n')
            if event.startswith('event: done\n')
        )
        assert done['response_source'] == clicked['response_source'] and done['suggestion_click'] is True
        expect(other_format).to_have_attribute('aria-checked', 'true')
        expect(selected_model).to_have_text(model_name)
        with page.expect_response(stack.url + '/api/feedback', timeout=e2e.ANSWER_TIMEOUT) as response:
            page.get_by_role('button', name='좋아요').last.click()
        assert response.value.ok
        with connect(corpus) as conn:
            record = json.loads(conn.execute('SELECT record FROM feedbacks').fetchone()[0])
        assert record['response_source'] == done['response_source'] and record['suggestion_click'] is True
        assert record['question'] == question

        page.reload()
        page.get_by_role('button', name=question, exact=True).click()
        hit = request_row(stack, 4)
        assert hit['cache_reason'] == 'hit', f'cache decision: {hit["cache_reason"]} ({hit["exact_lookup_ms"]}ms)'
        expect(e2e.answer(page).last).to_contain_text('합성 결론입니다.')
        expect(page.get_by_test_id('source-card')).to_have_count(2)
        expect(page.get_by_test_id('source-card').nth(0)).to_contain_text(ID)
        expect(page.get_by_test_id('source-card').nth(1)).to_contain_text(additional_id)
        expect(page.get_by_text('정확 일치 캐시 조회', exact=False)).to_be_visible()
        read_feedback = e2e.capture_response_body(page, '/api/feedback')
        with page.expect_response(stack.url + '/api/feedback', timeout=e2e.ANSWER_TIMEOUT) as response:
            page.get_by_role('button', name='좋아요').last.click()
        assert response.value.ok and json.loads(read_feedback()) == {'saved': True}
        assert hit['response_source'] == 'exact_cache' and set(hit['call_counts'].values()) == {0}
        with connect(corpus) as conn:
            record = json.loads(
                conn.execute(
                    "SELECT record FROM feedbacks WHERE json_extract(record, '$.request_id')=?", (hit['request_id'],)
                ).fetchone()[0]
            )
        assert record['request_id'] == hit['request_id'] and record['cache_answer_id'] == answer_id
        assert record['question'] == question and record['answer_raw'] is None

        store.revoke('answer', answer_id, 'tester', 'test')
        stack.ensure('normal')
        e2e.ask(page, question)
        e2e.wait_done(page)
        expect(e2e.answer(page).last).to_contain_text('가짜 모델 답변입니다.')
        assert request_row(stack, 5)['cache_reason'] == 'inactive'
    finally:
        stack.stop()
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
