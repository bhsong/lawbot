"""Synthetic candidates and model responses for the V2 semantic cache contract."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from hashlib import sha256
from threading import Event
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from fakes import FakeLLM, FakeRetriever
from lawbot import api_client, cache_runtime, semantic_runtime, service, view, web
from lawbot.cache_store import Draft, Store, init, now
from lawbot.db.connection import connect
from lawbot.exact_cache import Article, Namespace, Snapshot, digest
from lawbot.semantic_gate import Candidate, PairScores, Parent, assess, score_pairs, valid_vectors
from lawbot.semantic_policy import ReusePolicy, check_conditions
from lawbot.semantic_settings import Calibration, RerankTokens, ShadowSettings
from test_exact_cache import CALCULATION, ID, RAW, activate
from test_web import PW, USER, chat, events, login, make_client


def stub_tokenizer():
    tokenizer = Tokenizer(models.WordLevel({'[UNK]': 0}, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    return tokenizer.to_str().encode()


@pytest.fixture
def tokens(tmp_path):
    raw = stub_tokenizer()
    path = tmp_path / 'tokenizer.json'
    path.write_bytes(raw)
    return RerankTokens.load(path, sha256(raw).hexdigest())


@pytest.fixture
def calibration():
    return Calibration(
        calibration_id='synthetic',
        embedding_model='stub-embedding',
        embedding_revision='stub-embedding-revision',
        reranker_model='stub-reranker',
        reranker_revision='stub-reranker-revision',
        reranker_tokenizer_sha256=sha256(stub_tokenizer()).hexdigest(),
        index_revision='synthetic-index',
        query_max_seq=1024,
        top_k=3,
        parent_instruction='Find the statutory basis for the query.',
        question_instruction='Judge whether the same reviewed answer fits both questions.',
        parent_threshold=0.75,
        question_threshold=0.85,
        min_answer_margin=0.05,
    )


POLICY = ReusePolicy(
    intent_id='deadline',
    query_must_include=[['신청 기한', '신청 기간']],
    query_must_not_include=['제재'],
    required_slots={'staff': 'gte-five'},
    allowed_optional_slots={'period': ['current']},
    slot_terms={
        'staff': {'상시 근로자 5인 이상': 'gte-five', '상시 근로자 5인 미만': 'lt-five'},
        'period': {'현재': 'current', '작년': 'past'},
    },
    supported_query_patterns=[
        '{staff} 사업장의 신청 기한은 언제인가요?',
        '{period} {staff} 사업장의 신청 기간은 언제인가요?',
        '사업장의 신청 기한은 언제인가요?',
    ],
)
QUESTION = '상시 근로자 5인 이상 사업장의 신청 기한은 언제인가요?'


def candidate(name='b', *, answer='answer-b', policy=POLICY, version='1', text=QUESTION, parent_name=None):
    parent_name = parent_name or name
    parent_text = f'합성 부모 조문 {parent_name}'
    return Candidate(
        name,
        f'atom-{name}',
        f'query-{name}',
        text,
        Parent('synthetic-snapshot', parent_name, digest(parent_text), parent_text),
        answer,
        'deadline',
        version,
        policy.model_dump_json(),
    )


def scores(entries):
    return PairScores(
        {c.parent.pair_id: parent for c, parent, _ in entries if parent is not None},
        {c.question_pair_id: question for c, _, question in entries},
    )


@pytest.mark.parametrize(('parent_score', 'reason'), [(0.32, 'parent_score_low'), (None, 'parent_score_missing')])
def test_v2_05_06_only_the_selected_atoms_parent_counts(calibration, parent_score, reason):
    a, b = candidate('a', answer='answer-a'), candidate()
    result = assess(QUESTION, [a, b], scores([(a, 0.93, 0.80), (b, parent_score, 0.91)]), calibration)
    assert not result.would_hit and result.reason == reason
    assert result.linked_parent_score == parent_score and result.candidate == b


def test_v2_07_opposite_condition_wins_over_high_scores(calibration):
    b = candidate()
    opposite = QUESTION.replace('이상', '미만')
    result = assess(opposite, [b], scores([(b, 0.99, 0.99)]), calibration)
    assert not result.would_hit and result.reason == 'condition_conflict'
    evidence = next(item for item in result.condition.evidence if item.name == 'staff')
    assert opposite[evidence.start : evidence.end] == '상시 근로자 5인 미만'


@pytest.mark.parametrize(
    ('question', 'status'),
    [
        ('사업장의 신청 기한은 언제인가요?', 'missing'),
        (QUESTION.replace('5인 이상', '4인 초과'), 'unsupported'),
        (QUESTION.replace('5인 이상', '5인 이상 아닌'), 'unsupported'),
        (QUESTION.replace('상시 근로자 5인 이상', '직원 4명'), 'unsupported'),
        (QUESTION.replace('5인 이상', '“5인 이상”'), 'unsupported'),
        ('작년 ' + QUESTION.replace('기한', '기간'), 'unsupported'),
        (QUESTION + ' 제재는 무엇인가요?', 'unsupported'),
        (QUESTION + ' 제출 서류도 알려주세요.', 'unsupported'),
    ],
)
def test_v2_08_09_missing_unsupported_or_compound_questions(calibration, question, status):
    b = candidate()
    result = assess(question, [b], scores([(b, 0.99, 0.99)]), calibration)
    assert not result.would_hit and result.condition.status == status


def test_v2_10_same_answer_aliases_keep_the_highest_scoring_policy_and_question(calibration):
    b, alias, other = candidate(), candidate('alias', parent_name='b'), candidate('a', answer='answer-a')
    result = assess(
        QUESTION, [b, alias, other], scores([(b, 0.9, 0.96), (alias, 0.9, 0.95), (other, 0.9, 0.88)]), calibration
    )
    assert result.would_hit and result.candidate == b
    assert result.margin == pytest.approx(0.08)
    assert result.condition.status == 'known'
    assert check_conditions('현재 ' + QUESTION.replace('기한', '기간'), POLICY).status == 'known'


def test_overlapping_patterns_with_different_slot_coverage_are_ambiguous():
    policy = POLICY.model_copy(update={'supported_query_patterns': [QUESTION, POLICY.supported_query_patterns[0]]})
    assert check_conditions(QUESTION, policy).status == 'ambiguous'


def test_v2_11_close_competitor_and_top_k_alias_saturation(calibration):
    b, alias, other = candidate(), candidate('alias', parent_name='b'), candidate('a', answer='answer-a')
    ranked = scores([(b, 0.9, 0.95), (alias, 0.9, 0.94), (other, 0.9, 0.93)])
    close = assess(QUESTION, [b, other], ranked, calibration)
    assert close.reason == 'ambiguous_candidates' and close.margin == pytest.approx(0.02)
    saturated = assess(QUESTION, [b, alias], ranked, calibration, complete=False)
    assert saturated.reason == 'ambiguous_candidates' and saturated.margin is None
    assert assess(QUESTION, [b], ranked, calibration).margin is None


def test_v2_12_top_candidates_failed_policy_is_not_replaced(calibration):
    strict = POLICY.model_copy(update={'required_slots': {'staff': 'lt-five'}})
    b, alias = candidate(policy=strict), candidate('alias', version='2', parent_name='b')
    result = assess(QUESTION, [b, alias], scores([(b, 0.99, 0.97), (alias, 0.99, 0.94)]), calibration)
    assert not result.would_hit and result.reason == 'condition_conflict' and result.candidate == b


@pytest.mark.parametrize('fault', [None, 'missing', 'duplicate', 'string', 'nan'])
def test_v2_19_pairs_are_joined_by_id_after_validating_http_indexes(calibration, tokens, fault):
    a, b = candidate('a'), candidate()
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        items = [{'index': i, 'relevance_score': score} for i, score in enumerate([0.93, 0.32])]
        if body['instruction'] == calibration.question_instruction:
            items = [{'index': i, 'relevance_score': score} for i, score in enumerate([0.80, 0.91])]
            if fault == 'missing':
                items.pop()
            elif fault == 'duplicate':
                items[1]['index'] = 0
            elif fault in ('string', 'nan'):
                items[1]['relevance_score'] = '0.91' if fault == 'string' else 'NaN'
        return httpx.Response(200, json={'results': items[::-1]})

    with httpx.Client(base_url='http://testserver', transport=httpx.MockTransport(handle)) as http:
        client = api_client.ModelServerClient('http://testserver', 'synthetic', http=http)
        if fault:
            with pytest.raises(api_client.ModelServerError):
                score_pairs(QUESTION, [a, b], client, calibration, tokens)
        else:
            paired = score_pairs(QUESTION, [a, b], client, calibration, tokens)
            assert paired.parents == {a.parent.pair_id: 0.93, b.parent.pair_id: 0.32}
            assert paired.questions == {a.question_pair_id: 0.80, b.question_pair_id: 0.91}
    assert [r['instruction'] for r in requests] == [calibration.parent_instruction, calibration.question_instruction]


def test_v2_20_truncation_nan_and_wrong_dimension(calibration, tokens):
    assert not valid_vectors(np.array([[np.nan, 1]], dtype=np.float32), 1, 2)
    assert not valid_vectors(np.zeros((1, 2), dtype=np.float32), 1, 2)
    assert not valid_vectors(np.array([[1]], dtype=np.float32), 1, 2)
    assert valid_vectors(np.array([[1, 0]], dtype=np.float32), 1, 2)
    b = candidate(text='가 ' * 2700)

    class NeverCalled:
        def rerank(self, *args, **kwargs):
            pytest.fail('a potentially truncated pair reached the reranker')

    paired = score_pairs(QUESTION, [b], NeverCalled(), calibration, tokens)
    assert assess(QUESTION, [b], paired, calibration).reason == 'truncated_input'


def test_long_pair_does_not_discard_other_parent_candidates(calibration, tokens):
    a, b = candidate('a', answer='answer-a'), candidate()
    a = replace(a, parent=replace(a.parent, text='가 ' * 2700))
    b = replace(b, parent=replace(b.parent, text='나 ' * 900))

    class FixedScores:
        def rerank(self, model, query, documents, instruction, **kwargs):
            return [0.8, 0.97] if instruction == calibration.question_instruction else [0.98] * len(documents)

    paired = score_pairs(QUESTION, [a, b], FixedScores(), calibration, tokens)
    assert paired.parents == {b.parent.pair_id: 0.98}
    assert assess(QUESTION, [a, b], paired, calibration).would_hit
    reversed_rank = replace(paired, questions={a.question_pair_id: 0.99, b.question_pair_id: 0.80})
    assert assess(QUESTION, [a, b], reversed_rank, calibration).reason == 'truncated_input'


class ShadowModel:
    def __init__(self, calibration):
        self.calls = []
        self.revisions = [
            {'id': calibration.embedding_model, 'type': 'embedding', 'revision': calibration.embedding_revision},
            {'id': calibration.reranker_model, 'type': 'rerank', 'revision': calibration.reranker_revision},
        ]

    def models(self, *, timeout):
        return self.revisions

    def embed(self, model, texts, input_type, max_seq_length, *, timeout):
        self.calls.append(('embed', model, texts, input_type, max_seq_length))
        return np.array([[1.0, 0.0]], dtype=np.float32)

    def rerank(self, model, query, documents, instruction, *, timeout, strict):
        self.calls.append(('rerank', model, query, documents, instruction))
        return [0.97] * len(documents)


def seed_binding(store, snapshot, draft, calibration, index_file, question, policy, *, dimension=2):
    article = snapshot.articles[ID]
    source = article.text
    ns, at = snapshot.namespace.id, now()
    vector = np.zeros((1, dimension), dtype=np.float32)
    vector[0, 0] = 1.0
    np.save(index_file, vector, allow_pickle=False)
    event = store.control().activation_event
    with connect(store.path, write=True) as conn:
        conn.execute(
            'INSERT INTO atoms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                'atom-1',
                'atom',
                ns,
                snapshot.release_id,
                ID,
                article.text_hash,
                source,
                0,
                len(source),
                'approved',
                at,
                'tester',
                at,
                'tester',
            ),
        )
        conn.execute(
            'INSERT INTO atomic_questions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (
                'query-1',
                'query',
                'atom-1',
                question,
                digest(question),
                'example',
                'approved',
                at,
                'tester',
                at,
                'tester',
            ),
        )
        conn.execute(
            'INSERT INTO semantic_reuse_policies VALUES (?,?,?,?,?)',
            ('policy', '1', policy.model_dump_json(), at, 'tester'),
        )
        conn.execute(
            'INSERT INTO atom_answer_bindings '
            '(binding_id,namespace_id,atom_revision_id,query_revision_id,answer_id,policy_id,policy_version,'
            'candidate_query_id,activation_event,semantic_enabled,reviewed_at,reviewed_by,carry_status) '
            'VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)',
            (
                'binding',
                ns,
                'atom-1',
                'query-1',
                draft.answer_id(ns),
                'policy',
                '1',
                draft.query_id(ns),
                event,
                at,
                'tester',
                'keep',
            ),
        )
        conn.execute(
            'INSERT INTO semantic_index_manifests VALUES (?,?,?,?,?,?,?,?,?)',
            (
                calibration.index_revision,
                ns,
                snapshot.release_id,
                calibration.embedding_fingerprint,
                '["query-1"]',
                1,
                dimension,
                sha256(index_file.read_bytes()).hexdigest(),
                at,
            ),
        )


@pytest.fixture
def shadow_site(env, tmp_path, calibration, tokens):
    env.setenv('LAWBOT_PROFILE', 'v5')
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(FakeLLM, 'chunks', ['【결론】\n가짜 RAG 답변입니다.\n【근거 조문】\n근로기준법 제26조'])
    env.setattr(cache_runtime, 'active_token', lambda *a, **kw: ('release', 1))
    env.setattr(semantic_runtime, 'active_token', lambda *a, **kw: ('release', 1))
    app = service.load_app()
    request_log = tmp_path / 'requests.jsonl'
    app.settings = replace(app.settings, request_log=request_log)
    store = Store(tmp_path / 'private' / 'cache.db')
    init(store.path)
    source = '합성 원문'
    article = Article('근로기준법', '제26조', '해고의 예고', '법률', source, digest(source))
    snapshot = Snapshot('release', Namespace('corpus', 'policy', 'v5:law'), {ID: article})
    citations = [(ID, article.text_hash)]
    draft = Draft(
        'fixture',
        '승인한 합성 질문의 답변은 무엇인가요?',
        'key',
        digest(RAW),
        snapshot.render(RAW, citations),
        citations,
        {},
    )
    store.stage(snapshot.namespace, [draft], 'tester', 'synthetic')
    activate(store, snapshot, draft)
    app.exact_cache = cache_runtime.ExactCache(store, store.path, snapshot)
    index_file = tmp_path / 'vectors.npy'
    seed_binding(store, snapshot, draft, calibration, index_file, QUESTION, POLICY)
    model = ShadowModel(calibration)
    app.semantic_shadow = semantic_runtime.SemanticShadow.load(
        ShadowSettings(calibration, index_file, tokens), store.path, store.path, snapshot, model, timeout=1
    )
    return app, request_log, store, model, draft


def test_v2_01_and_28_exact_hit_skips_all_inference_and_keeps_approved_revision(shadow_site):
    app, request_log, _, model, draft = shadow_site

    class NeverLookup:
        def lookup(self, *args, **kwargs):
            pytest.fail('an exact hit must skip even a shadow lookup')

    app.semantic_shadow = NeverLookup()
    remembered = []
    emitted = list(web.chat_events(app, draft.question, 'pro', 'concise', remembered.append))
    assert [name for name, _ in emitted] == ['sources', 'answer', 'stats', 'done']
    assert emitted[-1][1]['text'] == draft.answer_text
    assert emitted[0][1]['groups'][0]['items'][0]['title'].startswith(ID)
    row = json.loads(request_log.read_bytes())
    assert row['response_source'] == 'exact_cache' and all(count == 0 for count in row['call_counts'].values())
    assert remembered[0]['cache_answer_id'] == row['cache_answer_id'] == emitted[-1][1]['cache_answer_id']
    assert remembered[0]['question'] == draft.question
    assert not model.calls and not app.models.llms


@pytest.mark.parametrize(
    ('change', 'reason'),
    [
        (None, 'hit'),
        ("UPDATE cache_answers SET status='draft'", 'not_approved'),
        ('UPDATE atom_answer_bindings SET activation_event=NULL', 'not_released'),
        ("UPDATE cache_control SET mode='off'", 'not_released'),
        ('UPDATE atom_answer_bindings SET semantic_enabled=0', 'semantic_disabled'),
        ("UPDATE atom_answer_bindings SET carry_status='pending'", 'semantic_disabled'),
        ("UPDATE cache_queries SET status='revoked',exact_enabled=0", 'revoked'),
        ('UPDATE cache_queries SET exact_enabled=0', 'semantic_disabled'),
    ],
)
def test_v2_02_03_04_shadow_records_but_does_not_serve_approved_answer(shadow_site, change, reason):
    app, request_log, store, model, _ = shadow_site
    if change:
        with connect(store.path, write=True) as conn:
            conn.execute(change)
    with make_client(app) as client:
        assert login(client).status_code == 200
        result = events(chat(client, question=QUESTION, model='pro', fmt='concise'))
    assert result[-1][0] == 'done' and '가짜 RAG 답변' in result[-1][1]['text']
    row = json.loads(request_log.read_bytes())
    assert row['response_source'] == 'rag' and row['call_counts']['llm'] == 1
    assert (row['semantic_reason'], row['semantic_would_hit']) == (reason, change is None)
    assert [call[0] for call in model.calls] == (['embed', 'rerank', 'rerank'] if change is None else [])


def test_shadow_rejects_missing_approval_with_mode_still_on(shadow_site, monkeypatch):
    from lawbot import semantic_store

    app, _, store, model, _ = shadow_site
    original = semantic_store.read_control
    monkeypatch.setattr(semantic_store, 'read_control', lambda conn: replace(original(conn), approval_id=None))
    assert store.control().mode == 'on' and store.control().approval_id is not None
    assert app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')['semantic_reason'] == 'not_released'
    assert not model.calls


def test_shadow_starts_after_terminal_sse_frame(shadow_site, env):
    app, request_log, _, model, _ = shadow_site
    finished = False
    shadow_started, release = Event(), Event()
    embed = model.embed

    def observe_embedding(*args, **kwargs):
        assert finished, 'shadow began before the final SSE frame'
        shadow_started.set()
        assert release.wait(2)
        return embed(*args, **kwargs)

    env.setattr(model, 'embed', observe_embedding)
    api = web.create_app(app, (USER, PW), dist=None)

    async def observed_app(scope, receive, send):
        async def observe(message):
            nonlocal finished
            await send(message)
            if (
                scope['path'] == '/api/chat'
                and message['type'] == 'http.response.body'
                and not message.get('more_body')
            ):
                finished = True

        await api(scope, receive, observe)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=observed_app), base_url='http://testserver'
        ) as client:
            assert (await login(client)).status_code == 200
            first = asyncio.create_task(chat(client, question=QUESTION, model='pro', fmt='concise'))
            try:
                assert await asyncio.to_thread(shadow_started.wait, 2)
                second = await asyncio.wait_for(chat(client, question=QUESTION, model='pro', fmt='concise'), 2)
                streamed = events(second)
                assert streamed[0] == ('status', {'text': view.searching_stats(app, 'pro')})
                assert streamed[-1][0] == 'done'
            finally:
                release.set()
                assert events(await asyncio.wait_for(first, 2))[-1][0] == 'done'

    asyncio.run(run())
    rows = [json.loads(line) for line in request_log.read_bytes().splitlines()]
    assert [row['semantic_reason'] for row in rows] == ['shadow_busy', 'hit']


def test_shadow_does_not_start_next_stage_after_deadline(shadow_site, env):
    app, _, _, model, _ = shadow_site
    tick = 0.0
    embed = model.embed

    def slow_embedding(*args, **kwargs):
        nonlocal tick
        tick = 60.0
        return embed(*args, **kwargs)

    env.setattr(model, 'embed', slow_embedding)
    env.setattr(semantic_runtime, 'time', SimpleNamespace(perf_counter=lambda: tick, monotonic=lambda: tick))
    result = app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')
    assert result['semantic_reason'] == 'deadline_exceeded'
    assert [call[0] for call in model.calls] == ['embed']


def test_failed_second_rerank_still_counts_both_attempts(shadow_site, env, calibration):
    app, request_log, _, model, _ = shadow_site
    rerank = model.rerank

    def fail_second(model_id, query, documents, instruction, **kwargs):
        score = rerank(model_id, query, documents, instruction, **kwargs)
        if instruction == calibration.question_instruction:
            raise api_client.ModelServerTimeout('synthetic')
        return score

    env.setattr(model, 'rerank', fail_second)
    with make_client(app) as client:
        assert login(client).status_code == 200
        assert events(chat(client, question=QUESTION, model='pro', fmt='concise'))[-1][0] == 'done'
    row = json.loads(request_log.read_bytes())
    assert row['semantic_reason'] == 'deadline_exceeded'
    assert row['semantic_rerank_http_calls'] == row['call_counts']['rerank'] == 2
    assert row['call_counts']['embedding'] == 2


def test_shadow_rerank_uses_remaining_time_and_discards_late_scores(shadow_site, env):
    app, _, _, model, _ = shadow_site
    tick = 0.0
    limits = []
    rerank = model.rerank

    def delayed(*args, timeout, **kwargs):
        nonlocal tick
        limits.append(timeout)
        tick += 0.65
        return rerank(*args, timeout=timeout, **kwargs)

    env.setattr(model, 'rerank', delayed)
    env.setattr(semantic_runtime, 'time', SimpleNamespace(perf_counter=lambda: tick))
    result = app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')
    assert limits == pytest.approx([1.0, 0.35])
    assert result['semantic_reason'] == 'deadline_exceeded' and not result['semantic_would_hit']
    assert result['semantic_rerank_http_calls'] == 2 and result['semantic_model_list_calls'] == 1


def test_shadow_rejects_changed_model_revision_before_inference(shadow_site, env):
    app, _, _, model, _ = shadow_site
    env.setattr(model, 'models', lambda *, timeout: [])
    result = app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')
    assert result['semantic_reason'] == 'score_contract_error'
    assert not model.calls


def test_shadow_discards_scores_when_revision_changes_mid_lookup(shadow_site, env):
    app, _, _, model, _ = shadow_site
    revisions = iter([model.revisions, []])
    env.setattr(model, 'models', lambda *, timeout: next(revisions))
    result = app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')
    assert result['semantic_reason'] == 'score_contract_error' and not result['semantic_would_hit']
    assert result['semantic_model_list_calls'] == 2
    assert 'linked_parent_score' not in result


def test_unreadable_index_warns_at_startup(shadow_site, caplog):
    app, _, _, _, _ = shadow_site
    shadow = app.semantic_shadow
    settings = replace(shadow.settings, index_path=shadow.settings.index_path.with_name('missing.npy'))
    assert semantic_runtime.AtomicIndex.load(settings, shadow.store) is None
    assert '인덱스 파일을 읽을 수 없습니다' in caplog.text


def test_top_k_only_materializes_parents_after_embedding(shadow_site, env, tmp_path):
    app, _, store, model, _ = shadow_site
    shadow = app.semantic_shadow
    revision = 'synthetic-two-questions'
    calibration = shadow.settings.calibration.model_copy(update={'top_k': 1, 'index_revision': revision})
    lower_question = '합성 사례의 별도 표현은 무엇인가요?'
    path = tmp_path / 'two-vectors.npy'
    np.save(path, np.eye(2, dtype=np.float32), allow_pickle=False)
    with connect(store.path, write=True) as conn:
        conn.execute(
            "INSERT INTO atomic_questions SELECT 'query-2','query-2',atom_revision_id,?,?,expression_type,status,"
            "created_at,created_by,reviewed_at,reviewed_by FROM atomic_questions WHERE query_revision_id='query-1'",
            (lower_question, digest(lower_question)),
        )
        conn.execute(
            "INSERT INTO atom_answer_bindings SELECT 'binding-2',namespace_id,atom_revision_id,'query-2',answer_id,"
            'policy_id,policy_version,candidate_query_id,activation_event,semantic_enabled,reviewed_at,reviewed_by,'
            "valid_from,valid_until,carry_status FROM atom_answer_bindings WHERE binding_id='binding'"
        )
        conn.execute(
            'INSERT INTO semantic_index_manifests SELECT ?,namespace_id,corpus_snapshot_id,embedding_fingerprint,?,2,'
            'dimension,?,created_at FROM semantic_index_manifests WHERE index_revision=?',
            (
                revision,
                '["query-1","query-2"]',
                sha256(path.read_bytes()).hexdigest(),
                shadow.settings.calibration.index_revision,
            ),
        )
    new_shadow = semantic_runtime.SemanticShadow.load(
        replace(shadow.settings, calibration=calibration, index_path=path),
        store.path,
        shadow.corpus_db,
        shadow.store.snapshot,
        model,
        timeout=1,
    )
    parsed = []
    original = Article.hit

    def observe(article):
        assert model.calls[0][0] == 'embed'
        parsed.append(article)
        return original(article)

    env.setattr(Article, 'hit', observe)
    result = new_shadow.lookup(QUESTION, 'pro', 'concise')
    assert len(parsed) == result['semantic_candidate_query_count'] == 1
    assert result['semantic_query_revision'] == 'query-1'


def test_top_k_partial_selection_breaks_ties_in_index_order():
    index = semantic_runtime.AtomicIndex(
        (), ('a', 'b', 'c', 'd'), np.array([[0, 1], [1, 0], [1, 0], [1, 0]], dtype=np.float32)
    )
    assert index.top_queries(np.array([1, 0], dtype=np.float32), {'a', 'c', 'd'}, 1) == ['c']
    assert index.top_queries(np.array([1, 0], dtype=np.float32), {'a', 'b', 'c', 'd'}, 2) == ['b', 'c']


@pytest.mark.parametrize('vector', [np.array([[np.nan, 1]], dtype=np.float32), np.array([[1]], dtype=np.float32)])
def test_v2_20_bad_query_vectors_are_shadow_misses(shadow_site, env, vector):
    app, _, _, model, _ = shadow_site
    env.setattr(model, 'embed', lambda *args, **kwargs: vector)
    metrics = app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')
    assert not metrics['semantic_would_hit'] and metrics['semantic_reason'] == 'invalid_vector'
    assert not model.calls


def test_binding_carry_history_is_scoped_and_append_only(shadow_site):
    _, _, store, _, _ = shadow_site
    with connect(store.path, write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            conn.execute(
                'INSERT INTO semantic_binding_decisions VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('unknown', 2, None, 'release', 'new-release', 'pending', 'synthetic', '[]', 'tester', now()),
            )
        conn.execute(
            'INSERT INTO semantic_binding_decisions VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('binding', 2, None, 'release', 'new-release', 'pending', 'synthetic', '[]', 'tester', now()),
        )
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            conn.execute("UPDATE semantic_binding_decisions SET verdict='keep' WHERE binding_id='binding'")


def test_calculation_bypasses_both_caches(shadow_site):
    app, request_log, _, model, _ = shadow_site

    class NeverLookup:
        def lookup(self, *args, **kwargs):
            pytest.fail('calculation questions must bypass both caches')

    app.exact_cache = app.semantic_shadow = NeverLookup()
    emitted = list(web.chat_events(app, CALCULATION, 'pro', 'concise'))
    assert emitted[0][0] == 'status' and not model.calls
    row = json.loads(request_log.read_bytes())
    assert row['cache_reason'] == 'calculation_question' and 'semantic_reason' not in row


@pytest.mark.parametrize(
    ('question', 'intent_id', 'expected'),
    [
        ('새로운 표현으로 접수 마감은 언제인가요?', 'deadline', 'hit'),
        ('새로운 표현으로 접수 마감은 언제인가요?', 'procedure', 'intent_conflict'),
        ('새로운 표현으로 접수 마감은 언제인가요?', None, 'hit'),
        ('마감일이 궁금합니다.', 'deadline', 'hit'),
        ('제재 관련 마감일이 궁금합니다.', 'deadline', 'condition_conflict'),
    ],
)
def test_intent_policy_handles_exact_miss_with_paraphrases(calibration, question, intent_id, expected):
    policy_json = json.dumps({'kind': 'intent', 'intent_id': intent_id, 'query_must_not_include': ['제재']})
    item = replace(candidate(), policy_json=policy_json)
    assert item.question != question
    assert assess(question, [item], scores([(item, 0.98, 0.98)]), calibration).reason == expected
    if expected == 'hit':
        decision = assess(question, [item], scores([(item, 0.98, 0.80)]), calibration)
        assert decision.reason == 'question_score_low' and decision.condition.reason == 'matched'


def test_corrected_exact_answer_does_not_leave_the_old_semantic_revision_active(shadow_site):
    from lawbot.cache_store import insert_answer

    app, _, store, model, draft = shadow_site
    ns = app.exact_cache.snapshot.namespace.id
    with connect(store.path, write=True) as conn:
        new_id = insert_answer(conn, ns, replace(draft, answer_key='corrected'))
        conn.execute("UPDATE cache_answers SET status='approved',valid_from=? WHERE answer_id=?", (now(), new_id))
        conn.execute('UPDATE cache_queries SET answer_id=? WHERE query_id=?', (new_id, draft.query_id(ns)))
    result = app.semantic_shadow.lookup(QUESTION, 'pro', 'concise')
    assert result['semantic_reason'] == 'semantic_disabled' and not result['semantic_would_hit']
    assert not model.calls
