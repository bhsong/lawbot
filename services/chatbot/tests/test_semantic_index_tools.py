"""Validate and publish immutable generations with synthetic model responses."""

import json

import numpy as np
import pytest

from lawbot.cache_seed import Protection
from lawbot.cache_store import init
from lawbot.db.connection import connect
from lawbot.semantic_import_store import import_seed
from lawbot.semantic_index_tools import IndexContract, build_index
from lawbot.semantic_val import source_query_id
from test_semantic_seed import handoff as handoff


@pytest.fixture
def catalog(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected'))
    output = tmp_path / 'private' / 'index'
    contract = IndexContract('synthetic', 'revision-1', 1024, 2)
    return db, output, snapshot, contract


class FakeEmbedding:
    def __init__(self, fault=None):
        self.fault = fault
        self.calls = []

    def revision(self):
        if self.fault == 'revision' and self.calls:
            return 'unexpected'
        return 'revision-1'

    def fits(self, question):
        return self.fault != 'truncated'

    def encode(self, questions):
        self.calls.append(len(questions))
        if self.fault == 'shape':
            return np.ones((len(questions), 1), dtype=np.float32)
        return np.tile([np.nan, 1.0] if self.fault == 'nan' else [1.0, 0.0], (len(questions), 1)).astype(np.float32)


@pytest.mark.parametrize('fault', ['shape', 'nan', 'truncated', 'revision'])
def test_index_rejects_bad_results_and_retains_active_generation(catalog, fault):
    db, output, snapshot, contract = catalog
    first = build_index(db, output, snapshot, 1, contract, FakeEmbedding(), batch_size=1)
    pointer = output / 'active.json'
    current = pointer.read_bytes()
    with pytest.raises(ValueError):
        build_index(db, output, snapshot, 1, contract, FakeEmbedding(fault), batch_size=1)
    assert pointer.read_bytes() == current
    with connect(db) as conn:
        assert conn.execute('SELECT index_revision,row_count,dimension FROM semantic_index_manifests').fetchall() == [
            (first['index_revision'], 2, 2)
        ]


def test_holdout_omits_only_the_designated_question_and_keeps_old_generation(catalog):
    db, output, snapshot, contract = catalog
    first = build_index(db, output, snapshot, 1, contract, FakeEmbedding())
    second = build_index(db, output, snapshot, 1, contract, FakeEmbedding(), held_out={source_query_id('pass', 'q1')})
    assert first['index_revision'] != second['index_revision'] and second['rows'] == 1
    assert json.loads((output / 'active.json').read_bytes())['index_revision'] == second['index_revision']
    assert (output / f'{first["index_revision"]}.npy').is_file()


def test_pointer_failure_retains_previous_readable_manifest(catalog, monkeypatch):
    from lawbot.semantic_pointer import active_revision

    db, output, snapshot, contract = catalog
    first = build_index(db, output, snapshot, 1, contract, FakeEmbedding())

    def fail(*args):
        raise OSError('simulated pointer swap failure')

    monkeypatch.setattr('lawbot.semantic_index_tools.os.replace', fail)
    with pytest.raises(OSError, match='simulated pointer'):
        build_index(db, output, snapshot, 1, contract, FakeEmbedding(), held_out={source_query_id('pass', 'q1')})
    assert active_revision(output) == first['index_revision']
    assert (output / f'{first["index_revision"]}.npy').is_file()


def test_generation_with_existing_file_corruption_does_not_replace_current(catalog):
    from lawbot.semantic_pointer import active_revision

    db, output, snapshot, contract = catalog
    first = build_index(db, output, snapshot, 1, contract, FakeEmbedding())
    held = {source_query_id('pass', 'q1')}
    second = build_index(db, output, snapshot, 1, contract, FakeEmbedding(), held_out=held)
    (output / f'{first["index_revision"]}.npy').write_bytes(b'damaged')
    with pytest.raises(ValueError, match='corrupted'):
        build_index(db, output, snapshot, 1, contract, FakeEmbedding())
    assert active_revision(output) == second['index_revision']


def test_pointer_reads_once_until_atomic_replacement(tmp_path, monkeypatch):
    from pathlib import Path

    from lawbot.semantic_pointer import active_revision

    pointer = tmp_path / 'active.json'
    replacement = tmp_path / 'next.json'
    pointer.write_text(json.dumps({'index_revision': 'a' * 64}))
    replacement.write_text(json.dumps({'index_revision': 'b' * 64}))
    actual = Path.read_bytes
    calls = []

    def read(path):
        calls.append(path)
        return actual(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    assert active_revision(tmp_path) == active_revision(tmp_path) == 'a' * 64
    assert calls == [pointer]
    replacement.replace(pointer)
    assert active_revision(tmp_path) == active_revision(tmp_path) == 'b' * 64
    assert calls == [pointer, pointer]


def test_pointer_retries_after_transient_read_error(tmp_path, monkeypatch):
    from pathlib import Path

    from lawbot.semantic_pointer import active_revision

    pointer = tmp_path / 'active.json'
    pointer.write_text(json.dumps({'index_revision': 'a' * 64}))
    read_bytes = Path.read_bytes
    calls = []

    def read(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError('temporary failure')
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    assert active_revision(tmp_path) is None
    assert active_revision(tmp_path) == active_revision(tmp_path) == 'a' * 64
    assert calls == [pointer, pointer]
