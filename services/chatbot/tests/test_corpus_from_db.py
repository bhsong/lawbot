import json

import numpy as np
import pytest

from lawbot import calc_corpus, check_deploy, deploy, embed_cache, feedback, manifest, service, settings, view
from lawbot.admin import Corpus
from lawbot.cache_store import init as init_cache
from lawbot.db import cli
from lawbot.db.connection import connect, transaction
from lawbot.db.csv_import import sha256
from test_migrate_csv_to_sqlite import db_path as db_path
from test_migrate_csv_to_sqlite import run as migrate

RELEASE = 'v5-test-release'
PROFILE = 'v5'
QUESTION = '해고 예고 없이 해고되면 어떻게 하나요?'


@pytest.fixture
def corpus_db(db_path):
    law, combined = deploy.article_paths(PROFILE)
    assert law.parent == combined.parent
    assert (
        migrate(db_path, 'corpus', '--dir', law.parent, '--profile', PROFILE, '--release-id', RELEASE, '--activate')
        == 0
    )
    return db_path


def configure(env, db, decrees=False, mode='db'):
    env.setenv('LAWBOT_PROFILE', PROFILE)
    env.setenv('LAWBOT_CORPUS', mode)
    env.setenv('LAWBOT_DB', str(db))
    env.setenv('LAWBOT_DECREE_ENABLED', '1' if decrees else '0')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1' if decrees else '0')


def corrupt_release(db):
    with connect(db, write=True) as conn, transaction(conn):
        conn.execute(
            'UPDATE article_versions SET text=text || ? WHERE version_id=('
            'SELECT version_id FROM corpus_release_articles WHERE release_id=? AND position=0)',
            (' synthetic corruption', RELEASE),
        )


def test_runtime_calculator_compatibility_reads_active_db_dependencies(corpus_db):
    medical, minimum = 'medical_expense_credit', 'monthly_minimum_wage'
    decree = '소득세법 시행령 제118조의5'
    minimum_decree = '최저임금법 시행령 제5조'
    enabled = dict.fromkeys(calc_corpus.ARTICLE_HASHES, ())
    assert len(enabled) == 7
    assert calc_corpus.database_calculator_compatibility(corpus_db, PROFILE) == enabled
    assert all(calc_corpus.database_calculator_compatibility(corpus_db, 'v4').values())
    corrupt_release(corpus_db)
    assert calc_corpus.database_calculator_compatibility(corpus_db, PROFILE) == enabled
    with connect(corpus_db, write=True) as conn, transaction(conn):
        conn.execute('UPDATE article_versions SET text=text || ? WHERE article_id=?', (' synthetic edit', decree))
    assert calc_corpus.database_calculator_compatibility(corpus_db, PROFILE) == enabled | {medical: (decree,)}
    with connect(corpus_db, write=True) as conn, transaction(conn):
        conn.execute('UPDATE article_versions SET text=? WHERE article_id=?', (b'not text', decree))
        conn.execute(
            'UPDATE article_versions SET text=text || ? WHERE article_id=?', (' synthetic edit', minimum_decree)
        )
    assert calc_corpus.database_calculator_compatibility(corpus_db, PROFILE) == enabled | {
        medical: (decree,),
        minimum: (minimum_decree,),
    }


@pytest.mark.parametrize('decrees', [False, True])
def test_db_and_csv_use_same_search_and_legacy_cache(corpus_db, tmp_path, env, model_server, decrees):
    configure(env, corpus_db, decrees, mode='csv')
    env.setenv('LAWBOT_EMBED_CACHE', '1')
    cache_dir = tmp_path / 'embeddings'
    env.setattr(embed_cache, 'CACHE_DIR', cache_dir)
    csv_app = service.load_app()
    with connect(corpus_db) as conn:
        assert conn.execute('SELECT count(*) FROM embedding_sets').fetchone() == (0,)
    law, combined = deploy.article_paths(PROFILE)
    assert csv_app.profile.articles == (combined if decrees else law)
    expected_sha = manifest.sha256(csv_app.profile.articles)
    csv_retriever = csv_app.retriever
    csv_hits = service.retrieve(csv_app, QUESTION, 3, {}, 'pro')
    parts = [('law', csv_app.profile.max_seq, csv_retriever.docs[: csv_retriever.n_law], csv_retriever.D)]
    if decrees:
        parts.append(('decree', csv_app.decree.max_seq, csv_retriever.docs[csv_retriever.n_law :], csv_retriever.D_dec))
    expected_keys = {}
    for part, limit, docs, vectors in parts:
        key = embed_cache.cache_key(csv_app.profile.emb_model, limit, docs)
        path = cache_dir / f'{key}.npz'
        with np.load(path) as cached:
            assert np.array_equal(cached['vectors'], vectors)
            legacy = {field: cached[field] for field in ('vectors', 'probe')}
        np.savez(path, **legacy)
        expected_keys[part] = key

    available = model_server.instances[-1].models()
    model_server.available = [item | {'revision': 'synthetic-embedding-revision'} for item in available]
    env.setenv('LAWBOT_CORPUS', 'db')
    env.setenv('LAWBOT_V5_ARTICLES', str(tmp_path / 'absent-law.csv'))
    env.setenv('LAWBOT_V5_DECREE_ARTICLES', str(tmp_path / 'absent-combined.csv'))
    db_app = service.load_app()
    assert db_app.corpus_release_id == RELEASE
    assert db_app.dataset_version == csv_app.dataset_version
    assert db_app.articles_sha256 == expected_sha
    assert view.data_notice(db_app) == view.data_notice(csv_app)
    for name in ('docs', 'ids', 'laws', 'kinds', 'titles', 'D'):
        assert np.array_equal(getattr(db_app.retriever, name), getattr(csv_retriever, name)), name
    assert np.array_equal(db_app.retriever.D_dec, csv_retriever.D_dec)
    assert model_server.instances[-1].calls == [
        ('embed', csv_app.profile.emb_model, 1, 'document', max_seq) for _, max_seq, _, _ in parts
    ]
    assert {p.stem for p in cache_dir.glob('*.npz')} == set(expected_keys.values())
    for key in expected_keys.values():
        with np.load(cache_dir / f'{key}.npz') as legacy:
            assert set(legacy.files) == {'vectors', 'probe'}
    assert service.retrieve(db_app, QUESTION, 3, {}, 'pro') == csv_hits
    admin = Corpus(db_app)
    assert ('근로기준법', '제26조') in admin.articles
    assert admin.sha == expected_sha and admin.version == csv_app.dataset_version
    assert (
        service.request_record(csv_app, {'model': 'pro', 'request_id': 'csv'}, None, 'ok', None)['corpus_release_id']
        is None
    )

    with connect(corpus_db) as conn:
        saved = conn.execute(
            'SELECT r.part,r.cache_key,e.model_id,e.model_revision,e.max_seq,e.dim,e.n_docs,e.docs_sha256 '
            'FROM release_embedding_sets r JOIN embedding_sets e USING (cache_key) WHERE r.release_id=?',
            (RELEASE,),
        ).fetchall()
    expected = {
        (
            part,
            expected_keys[part],
            csv_app.profile.emb_model,
            'synthetic-embedding-revision',
            limit,
            vectors.shape[1],
            len(docs),
            sha256(b''.join(doc.encode() + b'\0' for doc in docs)),
        )
        for part, limit, docs, vectors in parts
    }
    assert set(saved) == expected


@pytest.mark.parametrize(
    ('problem', 'message'),
    [
        ('inactive', '활성 코퍼스 릴리스'),
        ('missing', 'lawbot-db init'),
        ('old', 'lawbot-db migrate'),
        ('bad-sha', '복원 해시'),
    ],
)
def test_db_startup_errors_precede_model_connection(problem, message, db_path, request, env, model_server):
    if problem == 'missing':
        db_path = db_path.with_name('missing.db')
    elif problem == 'old':
        with connect(db_path, write=True) as conn, transaction(conn):
            conn.execute('DELETE FROM schema_migrations WHERE version=2')
    elif problem == 'bad-sha':
        assert request.getfixturevalue('corpus_db') == db_path
        corrupt_release(db_path)
    configure(env, db_path)
    with pytest.raises((RuntimeError, ValueError), match=message):
        service.load_app()
    assert model_server.instances == []


def test_running_app_pins_corpus_for_search_feedback_and_request_log(corpus_db, tmp_path, env):
    configure(env, corpus_db)
    app = service.load_app()
    before = app.retriever.search(QUESTION)
    source = tmp_path / 'replacement'
    source.mkdir()
    for path in deploy.article_paths(PROFILE):
        raw = path.read_bytes().replace('해고의 예고'.encode(), '합성 조문 변경'.encode(), 1)
        assert raw != path.read_bytes()
        (source / path.name).write_bytes(raw)
        metadata = manifest.load(path) | {'articles_sha256': sha256(raw), 'dataset_version': 'v5-next'}
        (source / manifest.path_for(path).name).write_text(json.dumps(metadata), encoding='utf-8')
    assert (
        migrate(corpus_db, 'corpus', '--dir', source, '--profile', PROFILE, '--release-id', 'next', '--activate') == 0
    )
    restarted = service.load_app()
    assert restarted.corpus_release_id == 'next' and restarted.retriever.docs != app.retriever.docs
    assert app.retriever.search(QUESTION) == before
    assert '(데이터 v5)' in view.data_notice(app) and '(데이터 v5-next)' in view.data_notice(restarted)

    stats = {'model': 'pro', 'request_id': 'synthetic-request', '_system': 'synthetic system'}
    record = feedback.turn_record(app, QUESTION, before, '합성 답변', '합성 답변', 'pro', 'concise', stats)
    assert record['corpus_release_id'] == RELEASE
    assert record['dataset_version'] == app.dataset_version == 'v5'
    assert record['articles_sha256'] == app.articles_sha256 != restarted.articles_sha256
    assert service.request_record(app, stats, None, 'ok', None)['corpus_release_id'] == RELEASE


def test_embedding_metadata_failure_only_warns(corpus_db, env, capsys):
    configure(env, corpus_db)
    with connect(corpus_db, write=True) as conn, transaction(conn):
        conn.execute(
            'CREATE TRIGGER fail_embedding_link BEFORE INSERT ON release_embedding_sets '
            "BEGIN SELECT RAISE(ABORT, 'synthetic error'); END"
        )
    assert service.load_app().retriever.search(QUESTION)
    assert '임베딩 메타데이터를 기록하지 못했습니다' in capsys.readouterr().out
    with connect(corpus_db) as conn:
        assert conn.execute('SELECT count(*) FROM embedding_sets').fetchone() == (0,)


def test_embedding_revision_is_optional_if_lookup_has_no_match(corpus_db, env, model_server):
    configure(env, corpus_db)
    model_server.available = []
    env.setattr(service, 'missing_models', lambda *args: [])
    assert service.load_app().corpus_release_id == RELEASE
    with connect(corpus_db) as conn:
        assert conn.execute('SELECT model_revision FROM embedding_sets').fetchall() == [(None,)]


def test_check_deploy_reports_configured_exact_cache(corpus_db, env, tmp_path):
    configure(env, corpus_db)
    path = tmp_path / 'private' / 'cache.db'
    env.setenv('LAWBOT_EXACT_CACHE_DB', str(path))
    checks = check_deploy.run()
    assert ('WARN', '정확 일치 캐시: 사용 안 함 (DB 파일 없음)') in checks
    init_cache(path)
    checks = check_deploy.run()
    assert any(
        level == 'OK' and '정확 일치 캐시:' in text and '스키마 2 · 모드 off · namespace ' in text
        for level, text in checks
    )
    assert ('OK', '계산기 medical_expense_credit 켜짐 (근거 조문 본문 일치)') in checks
    assert ('INFO', '계산 질문 캐시 제외 0개 · 초안 0 · 승인 0') in checks


@pytest.mark.parametrize('bundle_state', ['unset', 'missing', 'present'])
def test_check_deploy_warns_when_carry_bundle_is_missing(corpus_db, env, tmp_path, bundle_state):
    configure(env, corpus_db)
    cache = tmp_path / 'private' / 'cache.db'
    init_cache(cache)
    env.setenv('LAWBOT_EXACT_CACHE_DB', str(cache))
    bundle = tmp_path / 'p07.zip'
    if bundle_state == 'present':
        bundle.touch()
    if bundle_state != 'unset':
        env.setenv('LAWBOT_EXACT_CACHE_BUNDLE', str(bundle))
    warning = ('WARN', '정확 일치 캐시: 자동 유지 안 됨 (LAWBOT_EXACT_CACHE_BUNDLE 필요)')
    assert (warning in check_deploy.run()) == (bundle_state != 'present')


def test_startup_warns_when_carry_bundle_is_missing(corpus_db, env, tmp_path, model_server, caplog):
    from conftest import set_auth

    configure(env, corpus_db)
    set_auth(env)
    cache = tmp_path / 'private' / 'cache.db'
    init_cache(cache)
    env.setenv('LAWBOT_EXACT_CACHE_DB', str(cache))
    env.setenv('LAWBOT_EXACT_CACHE_BUNDLE', str(tmp_path / 'absent-p07.zip'))
    assert service.load_app(settings.load(web=True)).corpus_release_id == RELEASE
    assert '정확 일치 캐시: 자동 유지 안 됨 (LAWBOT_EXACT_CACHE_BUNDLE 필요)' in caplog.messages


def test_check_deploy_checks_db_bytes(corpus_db, env, tmp_path):
    configure(env, corpus_db, decrees=True)
    _, combined = deploy.article_paths(PROFILE)
    metadata = manifest.load(combined)
    env.setenv('LAWBOT_V5_ARTICLES', str(tmp_path / 'absent-law.csv'))
    env.setenv('LAWBOT_V5_DECREE_ARTICLES', str(tmp_path / 'absent-combined.csv'))
    checks = check_deploy.run()
    assert not any(level == 'FAIL' for level, _ in checks), checks
    summary = next(msg for level, msg in checks if level == 'PASS' and RELEASE in msg)
    assert all(
        text in summary
        for text in (metadata['dataset_version'], metadata['articles_sha256'][:12], str(metadata['articles_total']))
    )
    assert any(level == 'OK' and '하위법령 조문' in msg for level, msg in checks)
    assert any(level == 'OK' and '법률 검색·임베딩 그대로' in msg for level, msg in checks)
    assert any(level == 'OK' and '법령별 조문 수 일치' in msg for level, msg in checks)


@pytest.mark.parametrize('problem', ['inactive', 'bad-sha'])
def test_check_deploy_continues_non_corpus_checks_on_db_error(db_path, env, request, problem):
    if problem == 'bad-sha':
        assert request.getfixturevalue('corpus_db') == db_path
        corrupt_release(db_path)
    configure(env, db_path, decrees=True)
    checks = check_deploy.run()
    hint = f'{RELEASE} 조문 CSV의 복원 해시' if problem == 'bad-sha' else '활성 코퍼스 릴리스가 없습니다'
    assert any(level == 'FAIL' and hint in msg for level, msg in checks)
    for expected in (
        '기본 system',
        '임베딩 ',
        '리랭커 ',
        '첨부 켜짐',
        '웹앱 주소',
        '필요한 모델이 모델 서버에 모두 있음',
    ):
        assert any(expected in msg for _, msg in checks), expected
    assert not any(msg.startswith(('비삭제 ', '매니페스트:', '하위법령 조문 {')) for _, msg in checks)


def test_export_release_is_byte_exact_and_only_overwrites_on_request(corpus_db, tmp_path, capsys):
    out = tmp_path / 'export'
    args = ['export-release', '--db', str(corpus_db), '--release', RELEASE, '--out', str(out)]
    originals = {file for path in deploy.article_paths(PROFILE) for file in (path, manifest.path_for(path))}
    cli.main(args)
    assert {path.name: path.read_bytes() for path in out.iterdir()} == {p.name: p.read_bytes() for p in originals}
    before = {p.name: p.stat().st_mtime_ns for p in out.iterdir()}
    capsys.readouterr()
    cli.main(args)
    assert '같음' in capsys.readouterr().out
    assert {p.name: p.stat().st_mtime_ns for p in out.iterdir()} == before

    original = next(iter(sorted(originals)))
    changed = out / original.name
    changed.write_bytes(b'old')
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2 and changed.read_bytes() == b'old'
    cli.main([*args, '--overwrite'])
    assert changed.read_bytes() == original.read_bytes()
    message = capsys.readouterr().out
    assert all(value in message for value in (changed.name, sha256(b'old')[:12], manifest.sha256(original)[:12]))
