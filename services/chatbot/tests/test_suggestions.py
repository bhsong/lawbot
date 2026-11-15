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
    current = 'rev'
    suggestions = Suggestions(cache_runtime.ExactCache(store, path, snapshot), 'embed', 'rev', 0, lambda: current)
    control, (approved,), _ = question_catalog(path, snapshot, 'embed', 'rev')
    vector = np.array([1.0, 0.0], dtype=np.float32)
    save_vectors(path, control, 'embed', 'old-rev', [(approved, vector)], lambda: None)
    store.set_mode('off', 'test', 'off')
    assert suggestions.recommend('새 질문', vector) == []
    store.set_mode('shadow', 'test', 'shadow', approval)
    assert suggestions.recommend('새 질문', vector) == []
    store.set_mode('on', 'test', 'on', approval)
    assert suggestions.recommend('새 질문', vector) == []
    save_vectors(path, store.control(), 'embed', 'rev', [(approved, vector)], lambda: None)
    assert [item.question for item, _ in suggestions.recommend('새 질문', vector)] == [draft.question]
    assert suggestions.recommend(' ' + draft.question + ' ', vector) == []
    current = 'changed-rev'
    assert suggestions.recommend('새 질문', vector) == []
    current = 'rev'
    with connect(path, write=True) as conn, transaction(conn):
        conn.execute("UPDATE cache_question_vectors SET normalized_sha256='changed'")
        conn.execute('UPDATE cache_control SET generation=generation+1 WHERE singleton=1')
    assert suggestions.recommend('새 질문', vector) == []
    save_vectors(path, store.control(), 'embed', 'rev', [(approved, vector)], lambda: None)
    store.revoke('query', approved.id, 'test', 'revoke')
    assert suggestions.recommend('새 질문', vector) == []


def test_val_curve_selects_the_highest_exposure_under_the_risk_limit():
    from lawbot_tools.eval_suggestions import curve

    scored = [[(0.9, 'reusable'), (0.6, 'not_reusable')], [(0.8, 'unlabeled')], []]
    rows, selected = curve(scored, [0.6, 0.8, 0.9])
    assert selected == 0.8
    assert rows[0]['not_reusable'] == 0.5
    assert rows[1]['exposure'] == pytest.approx(2 / 3)
    assert rows[1]['unlabeled'] == 0.5


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
    monkeypatch.setattr(question_vectors, 'DATA', tmp_path)
    monkeypatch.setattr(question_vectors, 'require_available_window', lambda: None)

    def save(path, expected, model, revision, vectors, check):
        assert (expected, model, revision) == (control, 'fixture', 'rev')
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
