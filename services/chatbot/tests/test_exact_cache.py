import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lawbot import cache_runtime
from lawbot.cache_eval import compare
from lawbot.cache_seed import Bundle, Protection, load_bundle, prepare
from lawbot.cache_store import Draft, Store, init, migrate, valid
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import Article, Namespace, Snapshot, digest, normalize

QUESTION = '합성 질문인가요?'
ID = '근로기준법 제26조'
RAW = '【결론】\n합성 결론입니다.\n【근거 조문】\n근로기준법 제26조\n【설명】\n합성 설명\n【참고】\n합성 참고'


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [('  한\r\n  글?  ', '한\n  글?'), (' Ａ 2일 아닌가요? ', 'Ａ 2일 아닌가요?'), ('A\rB', 'A\rB')],
)
def test_minimal_normalization(raw, expected):
    assert normalize(raw) == expected
    assert digest(normalize(raw)) != digest(raw) or raw == expected


@pytest.mark.parametrize('variant', ['2일인가요', '2 일인가요?', '3일인가요?', '2일이 아닌가요?'])
def test_distinct_questions_remain_distinct(variant):
    assert normalize(variant) != normalize('2일인가요?')


def test_namespace_changes_with_scope_and_policy():
    namespace = Namespace('corpus', 'policy', 'v5:law')
    for changes in (
        {'corpus_version': 'updated'},
        {'answer_policy_version': 'updated'},
        {'domain_scope': 'v5:combined'},
        {'normalizer_version': 'v2'},
        {'locale': 'en'},
        {'visibility_scope': 'private'},
    ):
        assert replace(namespace, **changes).id != namespace.id


def test_new_cache_is_off_and_independently_versioned(tmp_path):
    path = tmp_path / 'private' / 'cache.db'
    assert init(path) == 1
    store = Store(path)
    assert store.control().mode == 'off'
    assert store.control().namespace_id is None
    assert migrate(path) == 1


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'cache.db'
    init(path)
    store = Store(path)
    namespace = Namespace('corpus', 'policy', 'v5:law')
    article = Article('근로기준법', '제26조', '해고의 예고', '법률', '합성 원문', digest('합성 원문'))
    snapshot = Snapshot('release', namespace, {ID: article})
    citations = [(ID, article.text_hash)]
    draft = Draft('sample', QUESTION, 'key', digest(RAW), snapshot.render(RAW, citations), citations, {})
    monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: ('release', 1))
    return store, snapshot, draft


def activate(store, snapshot, draft, mode='on'):
    namespace = snapshot.namespace.id
    approval = store.approve(
        namespace,
        1,
        'protected',
        {draft.query_id(namespace): draft.answer_id(namespace)},
        actor='tester',
        reason='synthetic approval',
    )
    store.set_mode('shadow', 'tester', 'synthetic shadow', approval)
    if mode == 'on':
        store.set_mode('on', 'tester', 'synthetic activation', approval)
    return approval


def test_modes_and_explicit_approval(catalog):
    store, snapshot, draft = catalog
    namespace = snapshot.namespace.id
    assert store.stage(snapshot.namespace, [draft], 'tester', 'draft') == 1
    cache = cache_runtime.ExactCache(store, store.path, snapshot)
    assert cache.lookup(QUESTION, 'pro', 'concise').reason == 'off'
    with pytest.raises(ValueError, match='승인 묶음'):
        store.set_mode('shadow', 'tester', 'without approval')
    approval = activate(store, snapshot, draft, 'shadow')
    shadow = cache.lookup(QUESTION, 'pro', 'concise')
    assert shadow.mode == 'shadow' and shadow.metrics()['would_hit']
    store.set_mode('on', 'tester', 'enable', approval)
    assert cache.lookup('  ' + QUESTION, 'pro', 'concise').match.answer_id == draft.answer_id(namespace)
    assert cache.lookup(QUESTION.removesuffix('?'), 'pro', 'concise').reason == 'no_match'
    assert cache.lookup(QUESTION, 'pro', 'full').reason == 'unsupported_scope'
    assert (
        store.lookup(replace(snapshot.namespace, answer_policy_version='other').id, QUESTION)[2] == 'namespace_mismatch'
    )


def test_stage_is_idempotent_and_conflict_rolls_back(catalog):
    store, snapshot, draft = catalog
    assert store.stage(snapshot.namespace, [draft], 'tester', 'draft') == 1
    assert store.stage(snapshot.namespace, [draft], 'tester', 'retry') == 0
    with pytest.raises(ValueError, match='충돌'):
        store.stage(
            snapshot.namespace,
            [replace(draft, question='새 질문'), replace(draft, answer_text='다른 답변')],
            'tester',
            'conflict',
        )
    with connect(store.path) as conn:
        assert conn.execute('SELECT count(*) FROM cache_queries').fetchone() == (1,)
        assert conn.execute('SELECT count(*) FROM cache_answers').fetchone() == (1,)


def test_revoked_shared_answer_and_reimport_stay_inactive(catalog):
    store, snapshot, draft = catalog
    store.stage(snapshot.namespace, [draft], 'tester', 'draft')
    activate(store, snapshot, draft)
    store.revoke('answer', draft.answer_id(snapshot.namespace.id), 'tester', 'withdraw')
    assert store.stage(snapshot.namespace, [draft], 'tester', 'retry') == 0
    assert store.lookup(snapshot.namespace.id, QUESTION)[2] == 'inactive'
    store.set_mode('off', 'tester', 'recheck')
    with pytest.raises(ValueError, match='철회'):
        activate(store, snapshot, draft)


@pytest.mark.parametrize('change', ['transition', 'rollback', 'changed_text', 'missing_card', 'missing_db', 'expired'])
def test_unusable_answer_fails_closed(catalog, monkeypatch, tmp_path, change):
    store, snapshot, draft = catalog
    store.stage(snapshot.namespace, [draft], 'tester', 'draft')
    activate(store, snapshot, draft)
    if change == 'transition':
        monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: ('next', 2))
    if change == 'rollback':
        monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: ('release', 3))
    if change == 'changed_text':
        snapshot = replace(snapshot, articles={ID: replace(snapshot.articles[ID], text_hash=digest('변경'))})
    if change in ('missing_card', 'expired'):
        with connect(store.path, write=True) as conn, transaction(conn):
            if change == 'missing_card':
                conn.execute('DELETE FROM cache_citations WHERE answer_id=?', (draft.answer_id(snapshot.namespace.id),))
            else:
                conn.execute('UPDATE cache_answers SET expires_at=?', (datetime(2000, 1, 1, tzinfo=UTC).isoformat(),))
    if change == 'missing_db':
        store = Store(tmp_path / 'missing.db')
    decision = cache_runtime.ExactCache(store, store.path, snapshot).lookup(QUESTION, 'pro', 'concise')
    assert decision.match is None
    assert (
        decision.reason
        == {
            'transition': 'activation_mismatch',
            'rollback': 'activation_mismatch',
            'changed_text': 'citation_mismatch',
            'missing_card': 'citation_mismatch',
            'missing_db': 'db_error',
            'expired': 'expired',
        }[change]
    )


def test_revocation_during_resolution_is_observed(catalog, monkeypatch):
    store, snapshot, draft = catalog
    store.stage(snapshot.namespace, [draft], 'tester', 'draft')
    activate(store, snapshot, draft)

    def revoke_before_final_read(*args, **kwargs):
        store.revoke('query', draft.query_id(snapshot.namespace.id), 'tester', 'withdraw')
        return ('release', 1)

    monkeypatch.setattr(cache_runtime, 'active_token', revoke_before_final_read)
    decision = cache_runtime.ExactCache(store, store.path, snapshot).lookup(QUESTION, 'pro', 'concise')
    assert decision.reason == 'state_changed' and decision.match is None


def test_exclusive_database_lock_falls_back(catalog):
    store, snapshot, draft = catalog
    store.stage(snapshot.namespace, [draft], 'tester', 'draft')
    activate(store, snapshot, draft)
    with closing(sqlite3.connect(store.path, autocommit=True)) as writer:
        assert writer.execute('PRAGMA journal_mode=DELETE').fetchone() == ('delete',)
        writer.execute('BEGIN EXCLUSIVE')
        decision = cache_runtime.ExactCache(store, store.path, snapshot).lookup(QUESTION, 'pro', 'concise')
        assert decision.match is None and decision.reason == 'db_timeout'


def test_time_window_is_half_open():
    assert valid('2026-01-01', '2026-01-01', '2026-01-02', None)
    assert not valid('2026-01-02', '2026-01-01', '2026-01-02', None)
    assert not valid('2026-01-02', '2026-01-01', None, '2026-01-02')


def test_split_protection_and_display_hold(catalog):
    _, snapshot, _ = catalog
    questions = ('합성 질문', '  보호 질문 ', '다른 질문', '근거 불명 질문')
    rows = [
        dict(
            id=f'q{i}',
            question=q,
            question_sha256=digest(q),
            answer=RAW,
            answer_sha256=digest(RAW),
            answer_key='key',
            display_citation_article_ids=[ID],
            display_citation_hashes={ID: snapshot.articles[ID].text_hash},
            batch_id='P01',
            review_unit_id='unit',
            source_report='report',
            source_report_sha256='sha',
            source_snapshot_id='snapshot',
            split_emb='train',
            split_sllm='train',
            review_evidence_article_ids=[ID],
        )
        for i, q in enumerate(questions)
    ]
    rows[3]['answer'] = RAW.replace('제26조\n', '제26조, 제999조\n')
    protected = Protection(
        frozenset({digest('보호 질문')}), {r['id']: r['question_sha256'] for r in (rows[0], rows[1], rows[3])}, 'hash'
    )
    seed = prepare(Bundle(rows, frozenset()), protected, snapshot)
    assert [d.source_id for d in seed.drafts] == ['q0']
    assert seed.summary['excluded_split_rows'] == 2
    assert seed.summary['incompatible_display_rows'] == 1


def test_altered_bundle_is_rejected_before_parsing(tmp_path):
    path = tmp_path / 'different.zip'
    path.write_bytes(b'different file')
    with pytest.raises(ValueError, match='해시'):
        load_bundle(path)


def test_paired_benchmark_keeps_hit_and_miss_costs_separate():
    base = dict(
        cache_mode='off',
        response_source='rag',
        status='ok',
        cache_eligible=True,
        would_hit=False,
        cache_reason='off',
        exact_lookup_ms=0.1,
        request_to_first_answer_ms=12,
        request_to_complete_ms=20,
        call_counts=dict(embedding=1, search=1, rerank=1, llm=1),
    )
    hit = base | dict(
        cache_mode='on',
        response_source='exact_cache',
        cache_reason='hit',
        exact_lookup_ms=0.2,
        request_to_first_answer_ms=0.5,
        request_to_complete_ms=1,
        call_counts=dict(embedding=0, search=0, rerank=0, llm=0),
    )
    miss = base | dict(cache_mode='on', cache_reason='no_match', request_to_complete_ms=22)
    result = compare(['hit', 'miss'], [base, base], [hit, miss])
    assert result['on']['hits'] == 1 and result['llm_call_reduction'] == 0.5
    assert result['on']['timing']['hit']['request_to_first_answer_ms']['p95'] == 0.5
    assert result['miss_added_overhead_ms']['p95'] == 2
