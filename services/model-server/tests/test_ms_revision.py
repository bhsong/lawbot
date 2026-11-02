# services/model-server/tests/test_ms_revision.py  →  모델 폴더 지문(revision)·서버 git 커밋 (모델 파일 불필요)
# 설계: docs/plans/e2e-playwright.md 5-1절
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from lawbot_model_server import api, revision
from lawbot_model_server.config import ModelEntry
from lawbot_model_server.registry import Registry

from .ms_fakes import AUTH, TOKEN, FakeChat, FakeEmbedder, FakeReranker

BIG = 3 * revision.EDGE  # 앞뒤 1MB 사이에 읽지 않는 가운데가 있는 크기


def model_dir(tmp_path, name='m'):
    d = tmp_path / name
    d.mkdir()
    (d / 'config.json').write_text('{"a": 1}')
    (d / 'model.safetensors').write_bytes(bytes(BIG))
    return d


# ---- 폴더 지문 ----
def test_same_folder_same_value(tmp_path):
    d = model_dir(tmp_path)
    v = revision.fingerprint(d)
    assert v is not None and v.startswith('sha256:') and len(v) == len('sha256:') + 16
    assert revision.fingerprint(d) == v


def test_copy_has_same_value(tmp_path):
    a, b = model_dir(tmp_path, 'a'), model_dir(tmp_path, 'b')
    assert revision.fingerprint(a) == revision.fingerprint(b)  # 경로는 폴더 기준 상대 경로


def test_content_change_at_edges_changes_value(tmp_path):
    d = model_dir(tmp_path)
    before = revision.fingerprint(d)
    data = bytearray(BIG)
    data[0] = 1
    (d / 'model.safetensors').write_bytes(bytes(data))
    first = revision.fingerprint(d)
    data[0], data[-1] = 0, 1
    (d / 'model.safetensors').write_bytes(bytes(data))
    assert len({before, first, revision.fingerprint(d)}) == 3


def test_size_and_name_change_value(tmp_path):
    d = model_dir(tmp_path)
    before = revision.fingerprint(d)
    (d / 'config.json').write_text('{"a": 10}')
    sized = revision.fingerprint(d)
    (d / 'config.json').rename(d / 'config2.json')
    assert len({before, sized, revision.fingerprint(d)}) == 3


def test_hidden_files_ignored(tmp_path):
    d = model_dir(tmp_path)
    before = revision.fingerprint(d)
    (d / '.DS_Store').write_bytes(b'x')
    assert revision.fingerprint(d) == before


def test_missing_folder_is_none(tmp_path):
    assert revision.fingerprint(tmp_path / 'none') is None
    assert revision.fingerprint(None) is None  # 경로 없는 항목 (테스트 대역)


# ---- /v1/models·/health ----
def registry_with(path):
    entries = (
        ModelEntry('chat-a', 'chat', path),
        ModelEntry('emb', 'embedding', Path('/models/none-emb')),
        ModelEntry('rr', 'rerank', Path('/models/none-rr')),
    )
    reg = Registry(entries, {'chat': FakeChat, 'embedding': FakeEmbedder, 'rerank': FakeReranker}, 1)
    reg.load_fixed()
    return reg


def test_models_listing_has_revision(tmp_path):
    d = model_dir(tmp_path)
    with TestClient(api.create_app(TOKEN, registry_with(d))) as c:
        data = {m['id']: m for m in c.get('/v1/models', headers=AUTH).json()['data']}
    assert data['chat-a']['revision'] == revision.fingerprint(d)
    assert data['emb']['revision'] is None  # 폴더 없음 (가짜 모델)


def test_health_has_commit(tmp_path):
    with TestClient(api.create_app(TOKEN, registry_with(tmp_path), commit='abc+dirty')) as c:
        assert c.get('/health', headers=AUTH).json() == {'ready': True, 'commit': 'abc+dirty'}


# ---- git 커밋 ----
def git(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def test_git_commit_clean_dirty_none(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / 'f.txt').write_text('1')
    git(repo, 'add', 'f.txt')
    git(repo, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', 'x')
    clean = revision.git_commit(repo)
    assert clean is not None and len(clean) == 40
    (repo / 'f.txt').write_text('2')
    assert revision.git_commit(repo) == clean + '+dirty'
    other = tmp_path / 'plain'
    other.mkdir()
    assert revision.git_commit(other) is None
