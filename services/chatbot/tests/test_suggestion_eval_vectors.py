from types import SimpleNamespace as NS

import numpy as np
import pytest

from lawbot import question_vectors
from lawbot_tools import eval_suggestions as evaluate


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    results = tmp_path / 'results'
    results.mkdir()
    monkeypatch.setattr(evaluate, 'RESULTS', results, raising=False)
    corpus, cache = tmp_path / 'corpus.db', tmp_path / 'cache.db'
    corpus.touch()
    cache.touch()
    revision = NS(value='revision')
    profile = NS(emb_model='model', max_seq=100)
    settings = NS(profile=profile, model_server='', model_token='', read_timeout=1)
    ctx = NS(snapshot=NS(), protection=NS(fingerprint='split'), event=1, check_import=lambda: None)
    query_ids = [f'{i:064x}' for i in range(100)]
    queries = {uid: f'synthetic private question {i}' for i, uid in enumerate(query_ids)}
    labels = {uid: {'answer': 'reusable'} for uid in query_ids}
    candidate = NS(
        id='q', answer_id='a', source_id='source', original='검토한 합성 후보', query_bounds=(), answer_bounds=()
    )
    control = NS(protection_hash='split', activation_event=1)
    calls = []

    def embed(model, batch, input_type, limit, timeout):
        calls.append(len(batch))
        return np.tile(np.array([1, 0], dtype=np.float32), (len(batch), 1))

    client = NS(
        models=lambda **_: [{'id': 'model', 'type': 'embedding', 'revision': revision.value}],
        embed=embed,
        http=NS(close=lambda: None),
    )
    monkeypatch.setattr(evaluate, 'ModelServerClient', lambda *_: client)
    monkeypatch.setattr(evaluate.settings, 'load', lambda: settings)
    monkeypatch.setattr(evaluate, 'corpus_context', lambda *a: ctx)
    monkeypatch.setattr(evaluate, 'read_pass', lambda _: None)
    monkeypatch.setattr(evaluate, 'read_deferred', lambda _: None)
    monkeypatch.setattr(
        evaluate, 'validation_input', lambda *a: (queries, labels, {'source': 'answer'}, 0, set(queries), set())
    )
    monkeypatch.setattr(
        evaluate, 'question_catalog', lambda *a, **kw: (control, [candidate], {'q': np.array([1, 0], dtype=np.float32)})
    )
    monkeypatch.setattr(question_vectors, 'require_available_window', lambda: None)
    monkeypatch.setattr(question_vectors, 'embedding_lock_path', lambda: tmp_path / 'shared.lock')
    args = NS(
        corpus_db=corpus,
        cache_db=cache,
        pass_bundle=tmp_path / 'pass.zip',
        deferred_bundle=tmp_path / 'deferred.zip',
        labels=tmp_path / 'labels.jsonl',
        out=results / 'curve.md',
        save_vectors=results / 'queries.npz',
        load_vectors=None,
        tau=[0.0, 0.5],
        dry_run=False,
        approved_requests=7,
        max_runtime_seconds=10,
    )
    return args, revision, profile, calls


def replay(args):
    return NS(
        **(
            vars(args)
            | {
                'out': args.out.with_name('replayed.md'),
                'save_vectors': None,
                'load_vectors': args.save_vectors,
                'approved_requests': None,
                'max_runtime_seconds': None,
            }
        )
    )


def test_saved_val_vectors_reproduce_both_curves_without_embedding(evaluation, monkeypatch):
    args, _, _, calls = evaluation
    original = evaluate.run(args)
    assert original['embedding_requests'] == len(calls) == 7
    assert original['chosen_tau'] == 0.5
    with np.load(args.save_vectors, allow_pickle=False) as archive:
        assert archive['query_ids'].shape == (100,)
        assert archive['vectors'].shape == (100, 2)
        assert 'synthetic private question' not in repr(dict(archive))

    def unexpected_embedding(*args):
        pytest.fail('replay started embedding')

    monkeypatch.setattr(evaluate, 'encode_questions', unexpected_embedding)
    rerun = replay(args)
    result = evaluate.run(rerun)
    assert result['embedding_requests'] == 0 and len(calls) == 7
    assert result['chosen_tau'] == original['chosen_tau']
    assert rerun.out.read_text().split('## 전체', 1)[1] == args.out.read_text().split('## 전체', 1)[1]


@pytest.mark.parametrize('changed', ['model_revision', 'max_seq_length', 'labels_sha256', 'query_ids'])
def test_val_replay_rejects_mismatched_metadata(evaluation, monkeypatch, changed):
    args, revision, profile, calls = evaluation
    evaluate.run(args)
    rerun = replay(args)
    if changed == 'model_revision':
        revision.value = 'different-revision'
    elif changed == 'max_seq_length':
        profile.max_seq += 1
    elif changed == 'labels_sha256':
        monkeypatch.setattr(evaluate, 'LABELS_SHA256', 'a' * 64)
    else:
        with np.load(args.save_vectors, allow_pickle=False) as archive:
            contents = dict(archive)
        contents['query_ids'][0] = 'f' * 64
        rerun.load_vectors = args.out.with_name('different-ids.npz')
        np.savez(rerun.load_vectors, **contents)
    with pytest.raises(ValueError, match=r'저장.*일치'):
        evaluate.run(rerun)
    assert len(calls) == 7 and not rerun.out.exists()


def test_val_leaves_vectors_and_result_in_place_when_result_writing_fails(evaluation, monkeypatch):
    args, _, _, calls = evaluation

    def interrupted(path, *_):
        assert args.save_vectors.is_file()
        path.write_text('already written')
        raise OSError('write failed')

    monkeypatch.setattr(evaluate, 'write_curve', interrupted)
    with pytest.raises(OSError, match='write failed'):
        evaluate.run(args)
    assert len(calls) == 7 and args.save_vectors.is_file()
    assert args.out.read_text() == 'already written'


@pytest.mark.parametrize('flag', ['out', 'save_vectors', 'load_vectors'])
def test_val_rejects_paths_outside_results_including_symlinks(evaluation, flag):
    args, _, _, calls = evaluation
    outside = args.out.parent.parent / 'outside'
    outside.mkdir()
    escape = args.out.parent / 'escape'
    escape.symlink_to(outside, target_is_directory=True)
    if flag == 'load_vectors':
        args.save_vectors = None
        (outside / 'report').touch()
    setattr(args, flag, escape / 'report')
    with pytest.raises(ValueError, match='results/'):
        evaluate.run(args)
    assert calls == []
