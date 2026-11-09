"""Chromium and the real web/model-server apps with a synthetic reviewed answer."""

import json

import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from lawbot import deploy
from lawbot.cache_store import Draft, Store, init
from lawbot.corpus_store import active_release, active_token
from lawbot.db import cli
from lawbot.db.connection import connect
from lawbot.exact_cache import Snapshot, digest
from lawbot.service import system_text
from test_exact_cache import ID, QUESTION, RAW
from test_migrate_csv_to_sqlite import run as migrate

pytestmark = pytest.mark.e2e_stub


def test_exact_cache_browser_flow(tmp_path, page, request):
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
    citations = [(ID, snapshot.articles[ID].text_hash)]
    draft = Draft('fixture', QUESTION, 'key', digest(RAW), snapshot.render(RAW, citations), citations, {})
    store.stage(snapshot.namespace, [draft], 'tester', 'synthetic draft')
    namespace = snapshot.namespace.id
    approval = store.approve(
        namespace,
        active_token(corpus, 'v5')[1],
        'synthetic',
        {draft.query_id(namespace): draft.answer_id(namespace)},
        actor='tester',
        reason='test',
    )
    store.set_mode('shadow', 'tester', 'test', approval)
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
    )
    try:
        assert stack.web.alive()
        denied = page.request.post(
            stack.url + '/api/chat', data={'question': QUESTION, 'model': 'pro', 'format': 'concise'}
        )
        assert denied.status == 401 and not e2e.request_log(stack.request_log)
        e2e.login(page, stack.url)
        e2e.ask(page, QUESTION)
        e2e.wait_done(page)
        expect(e2e.answer(page).last).to_contain_text('가짜 모델 답변입니다.')
        shadow = e2e.request_log(stack.request_log)[-1]
        assert shadow['response_source'] == 'rag' and shadow['would_hit']
        assert shadow['call_counts'] == {'embedding': 1, 'search': 1, 'rerank': 1, 'llm': 1}

        store.set_mode('on', 'tester', 'test', approval)
        stack.stop_model_server()
        e2e.ask(page, QUESTION)
        e2e.wait_done(page)
        expect(e2e.answer(page).last).to_contain_text('합성 결론입니다.')
        expect(page.get_by_test_id('source-card')).to_have_count(1)
        expect(page.get_by_test_id('source-card')).to_contain_text(ID)
        expect(page.get_by_text('정확 일치 캐시 조회', exact=False)).to_be_visible()
        with page.expect_response(stack.url + '/api/feedback', timeout=e2e.ANSWER_TIMEOUT) as response:
            page.get_by_role('button', name='좋아요').last.click()
        assert response.value.json() == {'saved': True}
        hit = e2e.request_log(stack.request_log)[-1]
        assert hit['response_source'] == 'exact_cache' and set(hit['call_counts'].values()) == {0}
        with connect(corpus) as conn:
            record = json.loads(conn.execute('SELECT record FROM feedbacks').fetchone()[0])
        assert record['request_id'] == hit['request_id'] and record['cache_answer_id'] == draft.answer_id(namespace)
        assert record['question'] == QUESTION and record['answer_raw'] is None

        store.revoke('answer', draft.answer_id(namespace), 'tester', 'test')
        stack.ensure('normal')
        e2e.ask(page, QUESTION)
        e2e.wait_done(page)
        expect(e2e.answer(page).last).to_contain_text('가짜 모델 답변입니다.')
        assert e2e.request_log(stack.request_log)[-1]['cache_reason'] == 'inactive'
    finally:
        stack.stop()
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
