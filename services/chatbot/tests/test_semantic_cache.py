"""Synthetic candidates and model responses for the V2 semantic cache contract."""

import json
import sqlite3
from dataclasses import replace
from hashlib import sha256

import httpx
import numpy as np
import pytest

from fakes import FakeLLM, FakeRetriever
from lawbot import api_client, cache_runtime, semantic_runtime, service, web
from lawbot.cache_store import Draft, Store, init, now
from lawbot.db.connection import connect
from lawbot.exact_cache import Article, Namespace, Snapshot, digest
from lawbot.semantic_gate import Candidate, PairScores, Parent, assess, score_pairs, valid_vectors
from lawbot.semantic_policy import ReusePolicy, check_conditions
from lawbot.semantic_settings import Calibration, ShadowSettings
from test_exact_cache import CALCULATION, ID, RAW, activate
from test_web import chat, events, login, make_client


@pytest.fixture
def calibration():
    return Calibration(
        calibration_id='synthetic',
        embedding_model='stub-embedding',
        embedding_revision='stub-embedding-revision',
        reranker_model='stub-reranker',
        reranker_revision='stub-reranker-revision',
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
def test_v2_19_pairs_are_joined_by_id_after_validating_http_indexes(calibration, fault):
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
                score_pairs(QUESTION, [a, b], client, calibration)
        else:
            paired = score_pairs(QUESTION, [a, b], client, calibration)
            assert paired.parents == {a.parent.pair_id: 0.93, b.parent.pair_id: 0.32}
            assert paired.questions == {a.question_pair_id: 0.80, b.question_pair_id: 0.91}
    assert [r['instruction'] for r in requests] == [calibration.parent_instruction, calibration.question_instruction]


def test_v2_20_truncation_nan_and_wrong_dimension(calibration):
    assert not valid_vectors(np.array([[np.nan, 1]], dtype=np.float32), 1, 2)
    assert not valid_vectors(np.zeros((1, 2), dtype=np.float32), 1, 2)
    assert not valid_vectors(np.array([[1]], dtype=np.float32), 1, 2)
    assert valid_vectors(np.array([[1, 0]], dtype=np.float32), 1, 2)
    b = candidate(text='가' * 1024)

    class NeverCalled:
        def rerank(self, *args, **kwargs):
            pytest.fail('a potentially truncated pair reached the reranker')

    with pytest.raises(ValueError, match='truncated_input'):
        score_pairs(QUESTION, [b], NeverCalled(), calibration)


class ShadowModel:
    def __init__(self):
        self.calls = []

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
def shadow_site(env, tmp_path, calibration):
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
    model = ShadowModel()
    app.semantic_shadow = semantic_runtime.SemanticShadow.load(
        ShadowSettings(calibration, index_file), store.path, store.path, snapshot, model, timeout=1
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
        ('UPDATE atom_answer_bindings SET semantic_enabled=0', 'semantic_disabled'),
        ("UPDATE atom_answer_bindings SET carry_status='pending'", 'semantic_disabled'),
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
