import numpy as np
import pytest


def test_threshold_self_and_duplicate_answers():
    from lawbot.suggestions import Candidate, rank

    candidates = [
        Candidate('1', 'a', 'self', np.array([1.0, 0.0])),
        Candidate('2', 'a', 'first', np.array([1.0, 0.0])),
        Candidate('3', 'a', 'second', np.array([1.0, 0.0])),
        Candidate('4', 'b', 'boundary', np.array([0.0, 1.0])),
        Candidate('5', 'c', 'below', np.array([-1.0, 0.0])),
    ]
    assert [(row.question, score) for row, score in rank(candidates, np.array([1.0, 0.0]), ' self ', 0)] == [
        ('first', 1.0),
        ('boundary', 0.0),
    ]


def test_only_current_revision_and_hash_are_used_and_generation_reloads(tmp_path, monkeypatch):
    from lawbot import cache_runtime
    from lawbot.cache_question_store import question_catalog, save_vectors
    from lawbot.cache_store import Draft, Store, init
    from lawbot.db.connection import connect, transaction
    from lawbot.exact_cache import Article, Namespace, Snapshot, digest
    from lawbot.suggestions import Suggestions

    path = tmp_path / 'private' / 'cache.db'
    init(path)
    store = Store(path)
    aid = '근로기준법 제26조'
    article = Article('근로기준법', '제26조', '합성 근거', '법률', '합성 조문', digest('합성 조문'))
    snapshot = Snapshot('release', Namespace('corpus', 'policy', 'v5'), {aid: article})
    draft = Draft(
        'source', '검토한 합성 질문', 'answer', digest('원문'), '표시 답변', [(aid, article.text_hash)], {}, '원문'
    )
    store.stage(snapshot.namespace, [draft], 'test', 'stage')
    ns = snapshot.namespace.id
    approval = store.approve(ns, 1, 'split', {draft.query_id(ns): draft.answer_id(ns)}, actor='test', reason='approve')
    monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: ('release', 1))
    suggestions = Suggestions(cache_runtime.ExactCache(store, path, snapshot), 'embed', 'rev', 0, 100)
    control, (approved,), _ = question_catalog(path, snapshot, 'embed', 'rev', 100)
    vector = np.array([1.0, 0.0], dtype=np.float32)
    save_vectors(path, control, 'embed', 'old-rev', 100, [(approved, vector)], lambda: None)
    store.set_mode('off', 'test', 'off')
    assert suggestions.recommend('새 질문', vector).matches == []
    store.set_mode('shadow', 'test', 'shadow', approval)
    assert suggestions.recommend('새 질문', vector).matches == []
    store.set_mode('on', 'test', 'on', approval)
    assert suggestions.recommend('새 질문', vector).matches == []
    save_vectors(path, store.control(), 'embed', 'rev', 100, [(approved, vector)], lambda: None)
    save_vectors(path, store.control(), 'embed', 'rev', 99, [(approved, vector)], lambda: None)
    assert suggestions.recommend('새 질문', vector).matches == []
    save_vectors(path, store.control(), 'embed', 'rev', 100, [(approved, vector)], lambda: None)
    assert [item.question for item, _ in suggestions.recommend('새 질문', vector).matches] == [draft.question]
    assert suggestions.recommend(' ' + draft.question + ' ', vector).matches == []
    with connect(path, write=True) as conn, transaction(conn):
        conn.execute("UPDATE cache_question_vectors SET normalized_sha256='changed'")
        conn.execute('UPDATE cache_control SET generation=generation+1 WHERE singleton=1')
    assert suggestions.recommend('새 질문', vector).matches == []
    save_vectors(path, store.control(), 'embed', 'rev', 100, [(approved, vector)], lambda: None)

    def unexpected_lookup(*a):
        pytest.fail('recommendation read the catalog again for a candidate')

    monkeypatch.setattr(cache_runtime.ExactCache, 'lookup', unexpected_lookup)
    assert len(suggestions.recommend('새 질문', vector).matches) == 1
    store.revoke('query', approved.id, 'test', 'revoke')
    assert suggestions.recommend('새 질문', vector).matches == []
    suggestions.observe_revision('changed-rev')
    suggestions.observe_revision('rev')
    assert suggestions.recommend('새 질문', vector).error == 'revision_mismatch'


def test_val_curve_selects_the_highest_exposure_under_the_risk_limit():
    from lawbot_tools.eval_suggestions import curve

    scored = [[(0.9, 'reusable'), (0.6, 'not_reusable')], [(0.8, 'unlabeled')], []] * 100
    rows, selected = curve(scored, [0.6, 0.8, 0.9])
    assert selected == 0.8
    assert rows[0]['not_reusable'] == 0.5
    assert rows[1]['exposure'] == pytest.approx(2 / 3)
    assert rows[1]['unlabeled'] == 0.5
    rows, selected = curve([[(0.9, 'reusable')]] * 99, [0.8])
    assert rows[0]['labeled'] == 99 and selected is None


def test_unmatched_labels_only_inherit_confirmed_query_wide_negatives():
    from lawbot_tools.eval_suggestions import recommendation_label

    for nonnegative in ('reusable', 'uncertain'):
        assert recommendation_label({None: nonnegative}, 'other', query_wide_negative=True) == 'unlabeled'
    assert recommendation_label({None: 'not_reusable'}, 'other', query_wide_negative=False) == 'unlabeled'
    assert recommendation_label({None: 'not_reusable'}, 'other', query_wide_negative=True) == 'not_reusable'
    assert (
        recommendation_label({None: 'not_reusable', 'known': 'reusable'}, 'known', query_wide_negative=True)
        == 'reusable'
    )


def test_embedding_lock_is_shared_across_worktrees(tmp_path, monkeypatch):
    from lawbot import question_vectors

    first, second = tmp_path / 'lawbot', tmp_path / 'lawbot-prod'
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    shared_path = question_vectors.embedding_lock_path()
    monkeypatch.chdir(second)
    assert shared_path.is_absolute() and question_vectors.embedding_lock_path() == shared_path
    monkeypatch.setattr(question_vectors, 'require_available_window', lambda: None)
    monkeypatch.setattr(question_vectors, 'embedding_lock_path', lambda: tmp_path / 'shared.lock')
    monkeypatch.chdir(first)
    with question_vectors.embedding_budget(1, 1, 1):
        monkeypatch.chdir(second)
        with pytest.raises(ValueError, match='실행 중'):
            with question_vectors.embedding_budget(1, 1, 1):
                pytest.fail('second worktree acquired a held lock')
    with question_vectors.embedding_budget(1, 1, 1):
        assert (tmp_path / 'shared.lock').is_file()


def test_vector_generation_dry_run_and_matching_vectors(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from lawbot import question_vectors
    from lawbot.cache_store import Control

    cfg = NS(profile=NS(emb_model='fixture', max_seq=100), model_server='', model_token='', read_timeout=1)
    ctx = NS(snapshot=NS(namespace=NS(id='ns')), event=1, protection=NS(fingerprint='split'), check_import=lambda: None)
    control = Control('off', 2, 'ns', 1, 'split', 'approval')
    questions = [NS(id=str(i), normalized=f'질문 {i}') for i in range(18)]
    stored = {'0': np.array([1.0, 0.0])}
    calls = []

    def embed(model, batch, input_type, limit, timeout):
        assert (model, input_type, limit) == ('fixture', 'query', 100)
        calls.append(tuple(batch))
        return np.tile([1.0, 0.0], (len(batch), 1))

    client = NS(
        models=lambda **_: [{'id': 'fixture', 'type': 'embedding', 'revision': 'rev'}],
        embed=embed,
        http=NS(close=lambda: None),
    )
    monkeypatch.setattr(question_vectors, 'ModelServerClient', lambda *_: client)
    monkeypatch.setattr(question_vectors, 'question_catalog', lambda *a, **kw: (control, questions, stored))
    monkeypatch.setattr(question_vectors, 'embedding_lock_path', lambda: tmp_path / 'shared.lock')
    monkeypatch.setattr(question_vectors, 'require_available_window', lambda: None)

    def save(path, expected, model, revision, max_seq_length, vectors, check):
        assert (expected, model, revision, max_seq_length) == (control, 'fixture', 'rev', 100)
        stored.update((question.id, vector) for question, vector in vectors)

    monkeypatch.setattr(question_vectors, 'save_vectors', save)
    args = NS(dry_run=True, approved_requests=None, max_runtime_seconds=1)
    assert question_vectors.generate_vectors(tmp_path, ctx, cfg, args)['embedding_requests'] == 2
    assert not calls
    args.dry_run = False
    with pytest.raises(ValueError, match='approved-requests'):
        question_vectors.generate_vectors(tmp_path, ctx, cfg, args)
    assert not calls
    args.approved_requests = 2
    assert question_vectors.generate_vectors(tmp_path, ctx, cfg, args)['generated_vectors'] == 17
    assert [len(batch) for batch in calls] == [16, 1]
    assert calls[0][0] == '질문 1'
    assert question_vectors.generate_vectors(tmp_path, ctx, cfg, args)['embedding_requests'] == 0
    assert len(calls) == 2
