import fcntl
import json
from argparse import Namespace

import pandas as pd
import pytest

from fakes import FakeServerClient
from lawbot import api_client, embed_cache, manifest
from lawbot.corpus_store import active_release
from lawbot.db.connection import connect, transaction
from lawbot.db.corpus_import import prepare_corpus, release_bytes, write_corpus
from lawbot_tools import articles_update as au
from lawbot_tools import build_articles as ba
from lawbot_tools import reindex
from test_articles_update import NEW, OLD, World, files_of, last_line
from test_articles_update import live as live
from test_build_articles import blob_clones as blob_clones
from test_migrate_csv_to_sqlite import db_path as db_path

BASE = 'baseline'
ASOF = '2026-11-27'
NEW_ID = f'v4-live-{ASOF}'


@pytest.fixture(autouse=True)
def db_mode(env, db_path, tmp_path, model_server):
    env.setenv('LAWBOT_CORPUS', 'db')
    env.setenv('LAWBOT_DB', str(db_path))
    env.setattr(au.ac.Dataset, 'live_dir', property(lambda _: tmp_path / 'unused-live'))
    env.setattr(api_client, 'ModelServerClient', model_server)
    env.setattr(au, 'restart_web', lambda: (True, 'synthetic restart'))
    env.setattr(au, 'wait_web', lambda port: True)


def import_baseline(db, folder, files, asof, source_ref=None):
    folder.mkdir(exist_ok=True)
    args = Namespace(dataset_version='v4', reference_date=asof, collected_date=asof, source_ref=source_ref)
    for name, raw in files.items():
        (folder / name).write_bytes(raw)
    for name, metadata in au.MANIFESTS.items():
        errors, live = reindex.validate(folder / name)
        assert not errors
        data = reindex.new_manifest(folder / name, live, {'trained_on': {'embedding': 'v4'}}, args)
        (folder / metadata).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    with connect(db, write=True) as conn:
        write_corpus(conn, prepare_corpus(folder, 'v4', BASE), activate=True)


@pytest.fixture
def seeded(db_path, tmp_path, live):
    import_baseline(db_path, tmp_path / 'baseline', files_of(OLD), '2026-10-20')
    return db_path


def run(tmp_path, *args, asof=ASOF, repo=None):
    return au.main(
        [
            '--repo',
            str(repo or tmp_path),
            '--asof',
            asof,
            '--live-dir',
            str(tmp_path / 'live'),
            '--report-dir',
            str(tmp_path / 'reports'),
            *args,
        ]
    )


def state(db):
    with connect(db) as conn:
        active = conn.execute("SELECT release_id FROM active_corpus WHERE profile='v4'").fetchone()
        statuses = dict(conn.execute('SELECT release_id,status FROM corpus_releases'))
        events = conn.execute(
            'SELECT kind,release_id,previous_release_id,detail FROM corpus_events ORDER BY event_id'
        ).fetchall()
    return active[0] if active else None, statuses, events


def test_db_same_exit_and_report_without_new_release(seeded, live, tmp_path, env, capsys):
    world = World(env, live, OLD)
    before = state(seeded)
    assert run(tmp_path, '--restart') == 0
    line = last_line(capsys)
    assert line.startswith('articles updated=no same=yes changed=0 pending=0 next=- new=0')
    assert state(seeded) == before and world.calls == ['build']
    assert BASE in (tmp_path / 'reports' / f'{ASOF}.md').read_text()


def test_db_validation_error_records_failed_without_release(seeded, live, tmp_path, env, capsys):
    world = World(env, live, NEW.assign(text=['', '처음 정의', '영 목적']))
    assert run(tmp_path, '--restart') == 2
    assert '본문이 빈' in last_line(capsys)
    active, statuses, events = state(seeded)
    assert active == BASE and statuses == {BASE: 'validated'} and world.calls == ['build']
    kind, release, previous, detail = events[-1]
    assert (kind, release, previous) == ('failed', None, BASE)
    assert '본문이 빈' in json.loads(detail)['error']


def inject_update_failure(env, live, stage, error):
    World(env, live, NEW)
    env.setattr(au, 'make_retriever', hit_all)

    def fail(*args, **kwargs):
        raise error

    if stage == 'gate':
        env.setattr(au, 'gate', fail)
    else:
        env.setattr(au.ac, 'check_with_files', fail)


@pytest.mark.parametrize(('stage', 'candidate'), [('check', None), ('gate', NEW_ID)])
def test_db_unexpected_failure_is_audited_and_keeps_original_error(
    seeded, live, tmp_path, env, capsys, stage, candidate
):
    message = 'synthetic unexpected error'
    inject_update_failure(env, live, stage, TypeError(message))
    assert run(tmp_path, '--restart') == 2
    assert last_line(capsys) == f'articles updated=no error={message}'
    active, statuses, events = state(seeded)
    assert active == BASE
    assert statuses == ({BASE: 'validated', candidate: 'failed'} if candidate else {BASE: 'validated'})
    [(kind, release, previous, detail)] = [row for row in events if row[0] == 'failed']
    assert (kind, release, previous) == ('failed', candidate, BASE)
    assert json.loads(detail) == {'error_type': 'TypeError', 'error': message}


@pytest.mark.parametrize('stage', ['check', 'gate'])
def test_db_audit_failure_does_not_mask_original_error(seeded, live, tmp_path, env, capsys, stage):
    with connect(seeded, write=True) as conn, transaction(conn):
        conn.execute(
            "CREATE TRIGGER reject_failed_event BEFORE INSERT ON corpus_events WHEN NEW.kind='failed' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic audit error'); END"
        )
    message = 'synthetic original error'
    inject_update_failure(env, live, stage, au.UpdateError(message))
    assert run(tmp_path) == 2
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == f'articles updated=no error={message}'
    assert any('실패 이벤트를 기록하지 못했습니다' in line and 'IntegrityError' in line for line in lines[:-1])
    active, statuses, events = state(seeded)
    assert active == BASE and all(row[0] != 'failed' for row in events)
    assert statuses == ({BASE: 'validated', NEW_ID: 'staged'} if stage == 'gate' else {BASE: 'validated'})


def test_db_gate_failure_reserves_id_and_same_day_uses_suffixes(seeded, live, tmp_path, env, capsys):
    world = World(env, live, NEW, found=False)
    env.setattr(au, 'make_retriever', lambda data: hit_all(data, world.found))
    legacy = {path.name: path.read_bytes() for path in live.iterdir()}
    assert run(tmp_path, '--restart') == 2
    assert '검색되지 않음' in last_line(capsys)
    active, statuses, events = state(seeded)
    assert active == BASE and statuses[NEW_ID] == 'failed' and 'restart' not in world.calls
    assert [row[:2] for row in events[-2:]] == [('staged', NEW_ID), ('failed', NEW_ID)]
    assert '검색되지 않음' in json.loads(events[-1][3])['error']

    world.found = True
    assert run(tmp_path) == 1
    assert active_release(seeded, 'v4').release_id == f'{NEW_ID}_2'
    world.new_df = OLD
    assert run(tmp_path) == 1
    active, statuses, events = state(seeded)
    assert active == f'{NEW_ID}_3'
    assert statuses == {BASE: 'validated', NEW_ID: 'failed', f'{NEW_ID}_2': 'validated', f'{NEW_ID}_3': 'validated'}
    assert [row[:3] for row in events[-3:]] == [
        ('staged', active, None),
        ('validated', active, None),
        ('activated', active, f'{NEW_ID}_2'),
    ]
    assert {path.name: path.read_bytes() for path in live.iterdir()} == legacy


def test_db_skipped_restart_keeps_new_release(seeded, live, tmp_path, env, capsys):
    World(env, live, NEW)
    env.setattr(au, 'restart_web', lambda: (False, 'launchd 작업 없음'))
    env.setattr(au, 'wait_web', lambda port: pytest.fail('Unexpected readiness probe'))
    env.setattr(au, 'make_retriever', lambda data: hit_all(data))
    assert run(tmp_path, '--restart') == 1
    assert f'archive={BASE} restart=skipped(' in last_line(capsys)
    assert state(seeded)[0] == NEW_ID
    assert [row[0] for row in state(seeded)[2][-3:]] == ['staged', 'validated', 'activated']


def test_db_activation_rechecks_the_active_release_after_the_gate(seeded, live, tmp_path, env, capsys):
    world = World(env, live, NEW)
    external = prepare_corpus(tmp_path / 'baseline', 'v4', 'external')

    def retriever(data):
        with connect(seeded, write=True) as conn:
            write_corpus(conn, external, activate=True)
        return hit_all(data)

    env.setattr(au, 'make_retriever', retriever)
    assert run(tmp_path, '--restart') == 2
    assert '활성 코퍼스 릴리스가 바뀌었습니다' in last_line(capsys)
    active, statuses, events = state(seeded)
    assert active == 'external' and statuses[NEW_ID] == 'failed'
    assert events[-1][:2] == ('failed', NEW_ID) and 'restart' not in world.calls


def hit_all(data, found=True):
    from io import BytesIO

    assert isinstance(data, bytes)
    docs = pd.read_csv(BytesIO(data), encoding='utf-8-sig')

    class Search:
        def search(self, query, k):
            return [{'law': row.law, 'article_id': row.article_no} for row in docs.itertuples()] if found else []

    return Search()


def test_db_rollback_needs_previous_activation(seeded, tmp_path, capsys):
    before = state(seeded)
    assert au.main(['--rollback', '--live-dir', str(tmp_path / 'live')]) == 2
    assert '되돌릴' in last_line(capsys)
    assert state(seeded) == before
    assert not (tmp_path / 'live' / '.update.lock').exists()


def test_db_lock_blocks_second_command(seeded, tmp_path, capsys):
    before = state(seeded)
    with (seeded.parent / '.articles-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(tmp_path) == 2
    assert '같은 조문 폴더의 갱신 또는 복구가 진행 중입니다' in last_line(capsys)
    assert state(seeded) == before
    assert not (tmp_path / 'live' / '.update.lock').exists()


@pytest.mark.parametrize('problem', ['unconfigured', 'inactive', 'bad-sha'])
def test_db_preflight_fails_before_generation(problem, db_path, tmp_path, live, env, request, capsys):
    world = World(env, live, NEW)
    if problem == 'unconfigured':
        env.delenv('LAWBOT_DB')
    if problem == 'bad-sha':
        assert request.getfixturevalue('seeded') == db_path
        with connect(db_path, write=True) as conn, transaction(conn):
            conn.execute('UPDATE article_versions SET text=? WHERE version_id=1', ('synthetic corruption',))
    before = state(db_path)
    assert run(tmp_path) == 2
    hint = {'unconfigured': 'LAWBOT_DB', 'inactive': '활성 코퍼스 릴리스', 'bad-sha': '복원 해시'}[problem]
    assert hint in last_line(capsys)
    assert not world.calls and state(db_path) == before


def test_db_init_imports_v5_defaults_and_refuses_active_profile(db_path, env, tmp_path, capsys):
    env.setenv('LAWBOT_PROFILE', 'v5')
    dataset = au.ac.Dataset('v5')
    _, combined = ba.csv_names('v5')
    initial_date = manifest.load(dataset.source_dir / combined)['reference_date']
    args = ['--init', '--live-dir', str(tmp_path / 'live')]
    assert au.main(args) == 0
    current = active_release(db_path, 'v5')
    assert current.release_id == f'v5-live-{initial_date}'
    with connect(db_path) as conn:
        files = release_bytes(conn, current.release_id)
    assert files == {name: (dataset.source_dir / name).read_bytes() for name in files}
    assert len(files) == 4 and not (tmp_path / 'live').exists()
    assert au.main(args) == 2 and '이미' in last_line(capsys)


def test_db_git_generation_gate_restart_recovery_and_manual_rollback(blob_clones, db_path, tmp_path, env, capsys):
    from lawbot import cache_carry, cache_release_store, corpus_store
    from lawbot.cache_store import Draft, Store, init
    from lawbot.exact_cache import Snapshot, digest

    _, repo, _ = blob_clones
    source = '2020-01-01'
    files, _, stats = ba.build(repo.path, source)
    import_baseline(db_path, tmp_path / 'baseline', files, source, stats['head'])
    cache_path = tmp_path / 'cache.db'
    init(cache_path)
    catalog = Store(cache_path)
    initial = Snapshot.load(active_release(db_path, 'v4'), system='synthetic', combined=True, attach_decrees=True)
    article_id, article = next(iter(initial.articles.items()))
    draft = Draft('fixture', '합성 질문', 'answer', digest('answer'), 'answer', [(article_id, article.text_hash)], {})
    catalog.stage(initial.namespace, [draft], 'tester', 'draft')
    namespace = initial.namespace.id
    initial_token = corpus_store.active_token(db_path, 'v4')
    approval = catalog.approve(
        namespace,
        initial_token[1],
        'protected',
        {draft.query_id(namespace): draft.answer_id(namespace)},
        actor='tester',
        reason='synthetic approval',
    )
    catalog.set_mode('shadow', 'tester', 'check', approval)
    # Exercise the real cache transaction on this test's synthetic v4 corpus; production is scoped to v5.
    env.setattr(cache_release_store, 'PROFILE', 'v4')
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    env.setenv('LAWBOT_EMBED_CACHE', '1')
    cache = tmp_path / 'embeddings'
    env.setattr(embed_cache, 'CACHE_DIR', cache)
    embeddings = []

    class CheckedEncoder(FakeServerClient):
        def embed(self, *args, **kwargs):
            with connect(db_path, write=True) as conn:
                conn.execute('PRAGMA busy_timeout=0')
                with transaction(conn):
                    assert conn.execute("SELECT count(*) FROM corpus_releases WHERE status='staged'").fetchone() == (1,)
                    embeddings.append(args[2])
            return super().embed(*args, **kwargs)

    env.setattr(api_client, 'ModelServerClient', CheckedEncoder)
    restarted = []
    assessed = []
    ready = iter([True, False, True, True])

    def assess(db, profile):
        token = corpus_store.active_token(db, profile)
        release = active_release(db, profile)
        snapshot = Snapshot.load(release, system='synthetic', combined=True, attach_decrees=True)
        counts = cache_release_store.apply_release(cache_path, db, token, release, snapshot, None)
        assert counts == {'keep': 0, 'pending': 1, 'off': 0}
        assessed.append(token)
        return counts

    def restart():
        restarted.append(state(db_path)[0])
        assert [token[0] for token in assessed] == restarted
        assert catalog.control().activation_event == assessed[-1][1] and catalog.control().mode == 'shadow'
        return True, 'synthetic restart'

    env.setattr(cache_carry, 'transition', assess)
    env.setattr(au, 'restart_web', restart)
    env.setattr(au, 'wait_web', lambda port: next(ready))
    assert run(tmp_path, '--restart', asof='2021-01-01', repo=repo.path) == 1
    first = 'v4-live-2021-01-01'
    assert state(db_path)[0] == first
    assert last_line(capsys).startswith(f'articles updated=yes asof=2021-01-01 changed=6 archive={BASE} restart=ok')
    assert list(cache.glob('*.npz')) and {'document', 'query'} <= set(embeddings)
    report = (tmp_path / 'reports' / '2021-01-01.md').read_text()
    assert f'이전 릴리스 `{BASE}`' in report and 'lawbot-articles-update --rollback --restart' in report
    assert '캐시: 유지 0 · 검토 대기 1 · 끔 0' in report
    assert run(tmp_path, '--restart', asof='2022-01-01', repo=repo.path) == 2
    assert '이전 데이터로 되돌림 (웹앱 응답 확인)' in last_line(capsys)
    second = 'v4-live-2022-01-01'
    active, statuses, events = state(db_path)
    assert active == first and statuses == {BASE: 'validated', first: 'validated', second: 'validated'}
    assert [row[:3] for row in events[-2:]] == [('restart_failed', second, first), ('rolled_back', first, second)]
    assert au.main(['--rollback', '--restart', '--live-dir', str(tmp_path / 'live')]) == 0
    assert last_line(capsys) == f'articles rollback restored={BASE} archived={first} restart=ok'
    assert state(db_path)[0] == BASE and restarted == [first, second, first, BASE]
    assert not (tmp_path / 'live').exists() and not (tmp_path / 'unused-live').exists()


def test_cache_assessment_failure_does_not_fail_corpus_activation(seeded, live, tmp_path, env, capsys):
    from lawbot import cache_carry

    World(env, live, NEW)
    env.setattr(au, 'make_retriever', lambda data: hit_all(data))

    def unavailable(*args):
        raise ValueError('synthetic cache error')

    env.setattr(cache_carry, 'transition', unavailable)
    assert run(tmp_path) == 1
    assert state(seeded)[0] == NEW_ID
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-2] == '캐시 판정 실패' and lines[-1].startswith('articles updated=yes')
    assert '캐시 판정 실패' in (tmp_path / 'reports' / f'{ASOF}.md').read_text()
