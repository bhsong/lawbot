"""Chromium and the real web/model-server apps with a synthetic reviewed answer."""

import json
import time
from hashlib import sha256

import numpy as np
import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from lawbot import deploy
from lawbot.cache_store import Store, init
from lawbot.corpus_store import active_release, active_token
from lawbot.db import cli
from lawbot.db.connection import connect
from lawbot.exact_cache import Snapshot, digest
from lawbot.semantic_handoff import Answer, Handoff, Question, Reference
from lawbot.semantic_import_store import activate_seed, import_seed
from lawbot.semantic_index_tools import IndexContract, build_index
from lawbot.semantic_settings import Calibration
from lawbot.semantic_val import Protection
from lawbot.service import system_text
from test_exact_cache import ID, RAW
from test_migrate_csv_to_sqlite import run as migrate
from test_semantic_cache import stub_tokenizer

pytestmark = pytest.mark.e2e_stub


def request_row(stack, number):
    deadline = time.monotonic() + e2e.ANSWER_TIMEOUT / 1000
    while time.monotonic() < deadline:
        rows = e2e.request_log(stack.request_log)
        if len(rows) >= number:
            return rows[number - 1]
        time.sleep(0.02)
    pytest.fail(f'request log row {number} was not written')


class IndexEmbedding:
    def revision(self):
        return e2e.STUB_MODEL_REVISION

    def fits(self, question):
        return True

    def encode(self, questions):
        vectors = np.zeros((len(questions), 8), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors


def test_exact_cache_browser_flow(tmp_path, page, request):
    question = '합성 검증 사례에서 해고 예고 절차는 무엇인가요?'
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
    row = Answer(
        'key', 'review-unit', ID, RAW, digest(RAW), (ref, additional), (additional,), {}, article.text, 0, ref.sha256
    )
    handoff = Handoff('a' * 64, (row,), (Question('fixture', row.key, question, {}),))
    token = active_token(corpus, 'v5')
    assert token is not None
    event = token[1]
    protection = Protection([])
    assert import_seed(path, handoff, snapshot, event, protection)['approved_questions'] == 1
    assert store.control().mode == 'off'
    approval = store.control().approval_id
    assert approval is not None
    activate_seed(path, 'shadow', 'tester', 'test', approval, snapshot, event, protection, lambda: None)
    example = e2e.example_env()
    index_directory = tmp_path / 'index'
    contract = IndexContract(example['LAWBOT_EMB_MODEL'], e2e.STUB_MODEL_REVISION, 1024, 8)
    index = build_index(path, index_directory, snapshot, event, contract, IndexEmbedding())
    with connect(path) as conn:
        (answer_id,) = conn.execute('SELECT answer_id FROM cache_answers').fetchone()
    raw_tokenizer = stub_tokenizer()
    tokenizer_file = tmp_path / 'tokenizer.json'
    tokenizer_file.write_bytes(raw_tokenizer)
    calibration = Calibration(
        calibration_id='synthetic',
        embedding_model=example['LAWBOT_EMB_MODEL'],
        embedding_revision=e2e.STUB_MODEL_REVISION,
        reranker_model=example['LAWBOT_RERANK_MODEL'],
        reranker_revision=e2e.STUB_MODEL_REVISION,
        reranker_tokenizer_sha256=sha256(raw_tokenizer).hexdigest(),
        index_revision=index['index_revision'],
        query_max_seq=1024,
        top_k=2,
        parent_instruction='Find the legal basis.',
        question_instruction='Check whether the same reviewed answer fits both questions.',
        parent_threshold=0.0,
        question_threshold=0.0,
        min_answer_margin=0.05,
    )
    calibration_file = tmp_path / 'calibration.json'
    calibration_file.write_text(calibration.model_dump_json(), encoding='utf-8')
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
        LAWBOT_SEMANTIC_CACHE_MODE='shadow',
        LAWBOT_SEMANTIC_CALIBRATION=str(calibration_file),
        LAWBOT_SEMANTIC_INDEX=str(index_directory),
        LAWBOT_SEMANTIC_RERANK_TOKENIZER=str(tokenizer_file),
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
        assert shadow['call_counts'] == {'embedding': 2, 'search': 1, 'rerank': 3, 'llm': 1, 'models': 2}
        assert shadow['semantic_would_hit'] and shadow['semantic_rerank_http_calls'] == 2

        activate_seed(path, 'on', 'tester', 'test', approval, snapshot, event, protection, lambda: None)
        stack.stop_model_server()
        page.reload()
        e2e.ask(page, question)
        hit = request_row(stack, 2)
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
        assert 'semantic_would_hit' not in hit
        with connect(corpus) as conn:
            record = json.loads(conn.execute('SELECT record FROM feedbacks').fetchone()[0])
        assert record['request_id'] == hit['request_id'] and record['cache_answer_id'] == answer_id
        assert record['question'] == question and record['answer_raw'] is None

        store.revoke('answer', answer_id, 'tester', 'test')
        stack.ensure('normal')
        e2e.ask(page, question)
        e2e.wait_done(page)
        expect(e2e.answer(page).last).to_contain_text('가짜 모델 답변입니다.')
        assert request_row(stack, 3)['cache_reason'] == 'inactive'
    finally:
        stack.stop()
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
