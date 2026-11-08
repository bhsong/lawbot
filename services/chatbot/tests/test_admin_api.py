"""Administrator API through the real ASGI app and temporary SQLite."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from admin_test_data import (
    EVAL_QUESTION,
    GOLD,
    SETS,
    TEST_QUESTION,
    VAL_QUESTION,
    add_feedback,
    add_review,
    seed_qa,
)
from conftest import ADMIN_PW, ADMIN_USER
from lawbot import auth, web
from lawbot.db import migrate
from lawbot.db.connection import connect

ROOT = '/api/admin'
ORIGIN = {'Origin': 'http://testserver'}
USER = ('fixture-user', 'fixture-password')


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    seed_qa(path)
    return path


@pytest.fixture
def app(db, admin_hash):
    retriever = SimpleNamespace(
        ids=['제26조'],
        laws=['근로기준법'],
        titles=['해고 예고'],
        kinds=['법률'],
        docs=['근로기준법 제26조(해고 예고)\n현재 본문'],
        articles_sha256='new-hash',
    )
    return web.create_app(
        SimpleNamespace(retriever=retriever, dataset_version='current'),
        USER,
        dist=None,
        admin=auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash)),
        db=db,
    )


def login(client, credentials=(ADMIN_USER, ADMIN_PW)):
    user, password = credentials
    assert client.post('/api/login', json={'user': user, 'password': password}).status_code == 200


@pytest.fixture
def admin(app):
    with TestClient(app) as client:
        login(client)
        yield client


def review_body(key='review', **fields):
    return dict(idempotency_key=key, verdict='correct', decision='approved', gold_articles=GOLD) | fields


def post(client, path, body):
    return client.post(ROOT + path, json=body, headers=ORIGIN)


def test_admin_routes_require_role(app):
    routes = [
        ('GET', '/feedbacks'),
        ('GET', '/feedbacks/f1'),
        ('GET', '/articles'),
        ('GET', '/calendar/law-calendar'),
        ('POST', '/feedbacks/f1/review'),
        ('POST', '/feedbacks/promote-up'),
        ('POST', '/feedbacks/f1/promote-down'),
        ('POST', '/promotions/1/revoke'),
    ]
    with TestClient(app) as client:
        for method, path in routes:
            res = client.request(method, ROOT + path)
            assert (res.status_code, res.json()['code']) == (401, 'login_required')
        login(client, USER)
        for method, path in routes:
            res = client.request(method, ROOT + path)
            assert (res.status_code, res.json()['code']) == (403, 'admin_required')
        login(client)
        assert client.get(ROOT + '/feedbacks').status_code == 200


def test_admin_post_guards_preserve_general_415(admin):
    path = ROOT + '/feedbacks/f1/review'
    for headers in ({}, {'Origin': 'http://elsewhere'}):
        res = admin.post(path, json=review_body(), headers=headers)
        assert (res.status_code, res.json()['code']) == (403, 'bad_origin')
    res = admin.post(path, content='form', headers=ORIGIN)
    assert res.status_code == 415 and res.json()['code'] == 'unsupported_media_type'
    assert set(admin.post('/api/feedback', content='form').json()) == {'detail'}
    res = admin.post(path, content=b' ' * 65537, headers=ORIGIN | {'Content-Type': 'application/json'})
    assert (res.status_code, res.json()['code']) == (413, 'body_too_large')


def test_admin_post_rate_is_enforced(db, admin):
    add_feedback(db, 'f1')
    for i in range(30):
        assert post(admin, '/feedbacks/f1/review', review_body(f'k{i}')).status_code == 201
    res = post(admin, '/feedbacks/f1/review', review_body('last'))
    assert (res.status_code, res.json()['code']) == (429, 'rate_limited')


def test_list_filters_and_cursor(db, admin):
    for i in range(120):
        add_feedback(db, f'f{i:03}', question='찾을 문장' if i == 2 else '해고 예고가 필요한가요?')
    add_feedback(db, 'older', turn_id='f000', ts='2026-11-07T00:00:00+0000')
    add_feedback(db, 'clear', turn_id='f001', label='cleared')
    add_feedback(db, 'flag', label='flag', reason='answer')
    add_review(db, 'f002', decision='hold')
    ids, cursor = [], None
    for _ in range(8):
        res = admin.get(
            ROOT + '/feedbacks', params={'status': 'all', 'limit': 17} | ({'cursor': cursor} if cursor else {})
        )
        assert res.status_code == 200
        page = res.json()
        ids.extend(x['feedback_id'] for x in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert ids == sorted([f'f{i:03}' for i in range(120) if i != 1] + ['flag'], reverse=True)
    items = admin.get(ROOT + '/feedbacks', params={'status': 'hold', 'q': '찾을'}).json()['items']
    assert [x['feedback_id'] for x in items] == ['f002']
    items = admin.get(ROOT + '/feedbacks', params={'label': 'flag', 'reason': 'answer'}).json()['items']
    assert [x['feedback_id'] for x in items] == ['flag']
    assert admin.get(ROOT + '/feedbacks', params={'cursor': 'invalid'}).status_code == 422


def test_detail_reads_in_memory_corpus_and_previous_review(db, admin):
    add_feedback(db, 'prior', turn_id='turn', ts='2026-11-07')
    add_review(db, 'prior')
    add_feedback(
        db,
        'current',
        turn_id='turn',
        hits=[{'law': '근로기준법', 'article_id': '제26조', 'doc': '이전 본문'}],
        citations=['fixture'],
        timing={'total': 1},
        system='fixture',
    )
    detail = admin.get(ROOT + '/feedbacks/current').json()
    assert detail['feedback']['citations'] == ['fixture'] and detail['feedback']['hits'][0]['doc'] == '이전 본문'
    assert detail['reviews'][0]['feedback_id'] == 'prior'
    assert detail['checks']['corpus'] == {
        'feedback_dataset_version': 'old',
        'feedback_articles_sha256': 'old-hash',
        'current_dataset_version': 'current',
        'current_articles_sha256': 'new-hash',
        'changed_hits': [{'law': '근로기준법', 'article_id': '제26조', 'status': 'changed'}],
    }
    assert admin.get(ROOT + '/articles', params={'q': '제26조'}).json()['items'] == [
        {'law': '근로기준법', 'article_no': '제26조', 'title': '해고 예고', 'kind': '법률'}
    ]


@pytest.mark.parametrize(
    ('fields', 'code'),
    [
        ({'verdict': 'uncertain'}, 'invalid_verdict'),
        ({'verdict': 'incorrect'}, 'missing_corrected_answer'),
        ({'gold_articles': [{'law': '존재하지않는법', 'article_no': '제1조'}]}, 'unknown_article'),
        ({'corrected_question': 'x' * 2001}, 'invalid_body'),
    ],
)
def test_review_rejects_invalid_fields(db, admin, fields, code):
    add_feedback(db, 'f1')
    response = post(admin, '/feedbacks/f1/review', review_body(**fields))
    assert (response.status_code, response.json().get('code')) == (422, code)


def test_review_appends_and_replays(db, admin):
    add_feedback(db, 'f1')
    body = review_body()
    first = post(admin, '/feedbacks/f1/review', body)
    assert first.status_code == 201 and first.json()['status'] == 'reviewed'
    assert post(admin, '/feedbacks/f1/review', body).content == first.content
    conflict = post(admin, '/feedbacks/f1/review', body | {'decision': 'hold'})
    assert (conflict.status_code, conflict.json()['code']) == (409, 'idempotency_conflict')
    second = post(admin, '/feedbacks/f1/review', review_body('hold', decision='hold'))
    assert second.status_code == 201 and second.json()['status'] == 'hold'
    assert [r['decision'] for r in admin.get(ROOT + '/feedbacks/f1').json()['reviews']] == ['hold', 'approved']


def up(client, key, *ids):
    return post(client, '/feedbacks/promote-up', {'idempotency_key': key, 'feedback_ids': list(ids)})


def down(client, fid, key, verdict='incorrect', **fields):
    return post(
        client,
        f'/feedbacks/{fid}/promote-down',
        dict(
            idempotency_key=key,
            corrected_question='교정한 질문',
            corrected_answer='교정 답변',
            gold_articles=GOLD,
            review_verdict=verdict,
        )
        | fields,
    )


@pytest.mark.parametrize(
    ('fixture', 'code'),
    [
        ('missing', 'not_found'),
        ('unreviewed', 'not_reviewed'),
        ('uncertain', 'verdict_not_correct'),
        ('hold', 'not_approved'),
        ('rejected', 'not_approved'),
        ('no-gold', 'missing_gold'),
        ('unknown-gold', 'unknown_article'),
        ('flag', 'not_like'),
        ('older', 'superseded'),
    ],
)
def test_promote_up_failure_codes(db, admin, fixture, code):
    if fixture != 'missing':
        add_feedback(db, 'f1', **({'label': 'flag', 'reason': 'other'} if fixture == 'flag' else {}))
    if fixture == 'older':
        add_feedback(db, 'latest', turn_id='f1')
    if fixture not in ('missing', 'unreviewed'):
        fields = {
            'uncertain': {'verdict': 'uncertain'},
            'hold': {'decision': 'hold'},
            'rejected': {'decision': 'rejected'},
            'no-gold': {'gold_articles': []},
            'unknown-gold': {'gold_articles': [{'law': '없는 법', 'article_no': '제1조'}]},
        }.get(fixture, {})
        add_review(db, 'f1', **fields)
    res = up(admin, 'attempt', 'f1')
    assert res.status_code == 200
    assert [(row['ok'], row['code']) for row in res.json()['results']] == [(False, code)]


def test_promote_up_batch_and_idempotency(db, admin):
    add_feedback(db, 'f1')
    add_review(db, 'f1', corrected_answer='확인한 답')
    res = up(admin, 'batch', 'missing', 'f1')
    assert res.status_code == 200
    failed, ok = res.json()['results']
    assert failed['code'] == 'not_found' and ok['qa_uid'] == 'feedback:FB-f1-r1'
    assert up(admin, 'batch', 'missing', 'f1').content == res.content
    conflict = up(admin, 'batch', 'f1')
    assert (conflict.status_code, conflict.json()['code']) == (409, 'idempotency_conflict')
    assert up(admin, 'new', 'f1').json()['results'][0]['code'] == 'already_promoted'
    with connect(db) as conn:
        assert conn.execute("SELECT answer_key,answer FROM answer_keys WHERE key_set='feedback'").fetchall() == [
            ('FB-f1-r1', '확인한 답')
        ]
        assert conn.execute("SELECT qa_uid FROM qa_items WHERE dataset='feedback'").fetchall() == [
            ('feedback:FB-f1-r1',)
        ]
        assert conn.execute('SELECT law,article_no FROM qa_gold_articles').fetchall() == [('근로기준법', '제26조')]
        assert conn.execute(
            "SELECT purpose,split FROM qa_splits WHERE split_set='feedback' ORDER BY purpose"
        ).fetchall() == [('emb', 'unassigned'), ('sllm', 'unassigned')]
        assert conn.execute('SELECT count(*) FROM feedback_promotions').fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='promote_up'").fetchone() == (1,)
    assert admin.get(ROOT + '/feedbacks/f1').json()['promotions'][0]['promotion_id'] == ok['promotion_id']


@pytest.mark.parametrize(
    ('question', 'correction', 'code'),
    [
        (TEST_QUESTION, '완전히 별개의 교정 문장', 'leak_test'),
        (TEST_QUESTION + '요', None, 'leak_test'),
        ('완전히 별개의 원 질문', TEST_QUESTION, 'leak_test'),
        (VAL_QUESTION, None, 'leak_val'),
        (EVAL_QUESTION, None, 'leak_eval'),
    ],
)
def test_leakage_blocks_original_and_corrected(db, admin, question, correction, code):
    add_feedback(db, 'f1', question=question)
    add_review(db, 'f1', corrected_question=correction)
    res = up(admin, 'leak', 'f1')
    assert res.status_code == 200 and res.json()['results'][0]['code'] == code
    listed = admin.get(ROOT + '/feedbacks', params={'status': 'quarantined'}).json()['items']
    assert listed[0]['warnings'] == [code]


def test_db_and_csv_protect_same_questions(db):
    from collections import Counter

    from lawbot.dataset_store import protection_rows
    from lawbot.db.dataset_import import prepare_dataset, write_dataset
    from lawbot_tools.feedback_review import eval_sets

    real_db = db.parent / 'imported.db'
    migrate.init(real_db)
    with connect(real_db, write=True) as conn:
        for name in SETS:
            write_dataset(conn, prepare_dataset(name))
    csv = Counter(
        (('decree_v4' + name[6:]) if name.startswith('decree_') else name, str(i), q) for name, i, q in eval_sets()
    )
    actual = Counter(
        (row.split_set if row.split == 'eval' else f'{row.split_set}_{row.split}', row.source_id, row.question)
        for row in protection_rows(real_db)
        if row.split != 'train'
    )
    assert actual == csv


@pytest.mark.parametrize('verdict', ['incorrect', 'correct', 'uncertain'])
def test_promote_down_with_preference_only_for_wrong_answer(db, admin, verdict):
    add_feedback(db, 'f1', label='flag', reason='retrieval')
    res = down(admin, 'f1', 'down', verdict)
    if verdict == 'uncertain':
        assert res.status_code == 422
        return
    assert res.status_code == 201
    assert res.json()['qa_uid'] == 'feedback:FB-f1-r1'
    with connect(db) as conn:
        assert conn.execute('SELECT label,reason FROM feedbacks').fetchone() == ('flag', 'retrieval')
        assert conn.execute('SELECT chosen,rejected FROM qa_preferences').fetchall() == (
            [('교정 답변', '답변 원문')] if verdict == 'incorrect' else []
        )
    assert (res.json()['preference_id'] is not None) is (verdict == 'incorrect')


def test_concurrent_promotions_only_allow_one_active(db, app):
    add_feedback(db, 'f1')
    add_review(db, 'f1')
    barrier = Barrier(2)
    with TestClient(app) as client:
        login(client)

        def compete(key):
            barrier.wait(timeout=5)
            return up(client, key, 'f1').json()['results'][0]

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(compete, ('first', 'second')))
    assert sorted(row['ok'] for row in results) == [False, True]
    assert next(row for row in results if not row['ok'])['code'] == 'already_promoted'
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM feedback_promotions WHERE state='active'").fetchone() == (1,)


def test_revoke_and_repromote_use_new_id_and_keep_old_assignment(db, admin):
    add_feedback(db, 'f1')
    add_review(db, 'f1')
    first = up(admin, 'first', 'f1').json()['results'][0]
    with connect(db, write=True) as conn:
        conn.execute("UPDATE qa_splits SET split='train' WHERE qa_uid=? AND purpose='emb'", (first['qa_uid'],))
    revoke_path = f'/promotions/{first["promotion_id"]}/revoke'
    revoke = post(admin, revoke_path, {'idempotency_key': 'revoke', 'note': '취소'})
    assert revoke.status_code == 200 and revoke.json()['state'] == 'revoked'
    overlap = admin.get(ROOT + '/feedbacks/f1').json()['checks']['overlap']
    assert overlap == dict(qa_uid=first['qa_uid'], split_set='feedback', split='train', how='exact')
    second = up(admin, 'second', 'f1').json()['results'][0]
    assert second['qa_uid'] == 'feedback:FB-f1-r2'
    with connect(db) as conn:
        assert conn.execute(
            'SELECT q.qa_uid,q.status,s.split FROM qa_items q JOIN qa_splits s USING(qa_uid) '
            "WHERE q.dataset='feedback' AND s.purpose='emb' ORDER BY q.qa_uid"
        ).fetchall() == [('feedback:FB-f1-r1', 'retired', 'train'), ('feedback:FB-f1-r2', 'active', 'unassigned')]


def test_failed_promotion_rolls_back_qa_review_audit_and_receipt(db, admin):
    add_feedback(db, 'f1', label='flag', reason='answer')
    with connect(db, write=True) as conn:
        conn.execute(
            'CREATE TRIGGER reject_promotion BEFORE INSERT ON feedback_promotions '
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    res = down(admin, 'f1', 'broken')
    assert res.status_code == 500
    with connect(db) as conn:
        for table in (
            'answer_keys',
            'qa_gold_articles',
            'qa_preferences',
            'feedback_promotions',
            'feedback_reviews',
            'idempotency_keys',
        ):
            assert conn.execute(f'SELECT count(*) FROM {table}').fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM qa_items WHERE dataset='feedback'").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM qa_splits WHERE split_set='feedback'").fetchone() == (0,)
        assert conn.execute('SELECT action FROM audit_events').fetchall() == [('login_ok',)]


def test_calendar_proxy_filters_paths_and_credentials(db, admin_hash, monkeypatch):
    import httpx

    from lawbot import admin as api

    requests = []
    client_type = httpx.AsyncClient

    def upstream(request):
        requests.append(request)
        return httpx.Response(202, content=b'fixture-body', headers={'content-type': 'image/png'})

    monkeypatch.setattr(
        api, 'AsyncClient', lambda **kw: client_type(transport=httpx.MockTransport(upstream), **kw), raising=False
    )
    retriever = SimpleNamespace(ids=[], laws=[], titles=[], kinds=[], docs=[])
    application = web.create_app(
        SimpleNamespace(retriever=retriever),
        USER,
        dist=None,
        calendar_url='http://127.0.0.1:8765',
        admin=auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash)),
        db=db,
    )
    with TestClient(application) as client:
        login(client)
        for path in (
            'law-calendar',
            'law-calendar/status',
            'law-events',
            'law-changes/notice:42',
            'law-changes/notice:42/notice-comparison/2',
        ):
            res = client.get(ROOT + '/calendar/' + path + '?q=a%2Bb&q=c', headers={'Authorization': 'Bearer fixture'})
            assert (res.status_code, res.content, res.headers['content-type']) == (202, b'fixture-body', 'image/png')
            assert requests[-1].url.path == '/api/' + path
            assert requests[-1].url.query == b'q=a%2Bb&q=c'
            assert 'cookie' not in requests[-1].headers and 'authorization' not in requests[-1].headers
        for path in (
            'law-holds',
            'law-changes/42/anything',
            'law-changes/42/notice-comparison/wrong',
            '%2E%2E%2Fsecret',
        ):
            assert client.get(ROOT + '/calendar/' + path).status_code == 404
        res = client.post(ROOT + '/calendar/law-calendar', json={})
        assert (res.status_code, res.json()['code']) == (405, 'method_not_allowed')
        assert len(requests) == 5 and all(req.method == 'GET' for req in requests)


@pytest.mark.parametrize('timeout', [False, True])
def test_calendar_upstream_failures(db, admin_hash, monkeypatch, timeout):
    import httpx

    from lawbot import admin as api

    client_type = httpx.AsyncClient

    def upstream(request):
        raise (httpx.ReadTimeout if timeout else httpx.ConnectError)('fixture', request=request)

    monkeypatch.setattr(
        api, 'AsyncClient', lambda **kw: client_type(transport=httpx.MockTransport(upstream), **kw), raising=False
    )
    application = web.create_app(
        SimpleNamespace(retriever=SimpleNamespace(ids=[], laws=[], titles=[], kinds=[], docs=[])),
        USER,
        dist=None,
        calendar_url='http://127.0.0.1:8765',
        db=db,
        admin=auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash)),
    )
    with TestClient(application) as client:
        login(client)
        response = client.get(ROOT + '/calendar/law-events')
    assert (response.status_code, response.json()['code']) == (
        (504, 'calendar_timeout') if timeout else (502, 'calendar_unavailable')
    )


def test_calendar_unconfigured_and_disallowed(admin):
    res = admin.get(ROOT + '/calendar/law-events')
    assert (res.status_code, res.json()['code']) == (503, 'calendar_not_configured')
    res = admin.get(ROOT + '/calendar/law-holds')
    assert (res.status_code, res.json()['code']) == (404, 'not_found')


@pytest.mark.parametrize(
    'url',
    [
        'http://localhost:8765',
        'https://127.0.0.1:8765',
        'http://127.0.0.1:8765/a',
        'http://x:fixture@127.0.0.1:8765',
        'http://127.0.0.1:8765?q=fixture',
        'http://[',
    ],
)
def test_calendar_configuration_rejects_non_loopback_url(url):
    from lawbot import settings
    from test_settings import BASE

    with pytest.raises(settings.SettingsError, match='LAWBOT_CALENDAR_API_URL') as error:
        settings.load(BASE | {'LAWBOT_CALENDAR_API_URL': url}, web=True)
    assert url not in str(error.value)


def test_missing_import_sets_reject_startup_before_loading_models(tmp_path, monkeypatch, admin_hash):
    from conftest import set_auth

    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    set_auth(monkeypatch, *USER)
    monkeypatch.setenv('LAWBOT_DB', str(path))
    monkeypatch.setenv('LAWBOT_ADMIN_USER', ADMIN_USER)
    monkeypatch.setenv('LAWBOT_ADMIN_PW_HASH', admin_hash)
    monkeypatch.setattr(web, 'require_build', lambda _: None)
    monkeypatch.setattr(web, 'load_app', lambda _: pytest.fail('Model app loaded before QA startup validation'))
    with pytest.raises(RuntimeError, match='migrate_csv_to_sqlite qa'):
        web.main([])


def test_batch_retry_resumes_after_partial_commit(db, admin):
    for fid in ('first', 'second'):
        add_feedback(db, fid)
        add_review(db, fid)
    with connect(db, write=True) as conn:
        conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON feedback_promotions WHEN NEW.feedback_id='second' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    failed = up(admin, 'retry', 'first', 'second')
    assert (failed.status_code, failed.json()['code']) == (500, 'storage_error')
    with connect(db, write=True) as conn:
        assert conn.execute('SELECT feedback_id FROM feedback_promotions').fetchall() == [('first',)]
        conn.execute('DROP TRIGGER fail_second')
    res = up(admin, 'retry', 'first', 'second')
    assert res.status_code == 200 and [row['ok'] for row in res.json()['results']] == [True, True]
    assert up(admin, 'retry', 'first', 'second').content == res.content
    with connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM feedback_promotions').fetchone() == (2,)


def test_train_overlap_is_reported_but_can_be_promoted(db, admin):
    from admin_test_data import TRAIN_QUESTION

    add_feedback(db, 'f1', question=TRAIN_QUESTION + '요')
    add_review(db, 'f1')
    assert admin.get(ROOT + '/feedbacks/f1').json()['checks']['overlap']['split'] == 'train'
    assert up(admin, 'allowed', 'f1').json()['results'][0]['ok']


def test_rejected_review_key_replays_and_conflicts(db, admin):
    add_feedback(db, 'f1')
    path = '/feedbacks/f1/review'
    body = review_body(key='rejected', verdict='uncertain')
    first = post(admin, path, body)
    assert first.status_code == 422
    assert post(admin, path, body).json() == first.json()
    conflict = post(admin, path, review_body(key='rejected'))
    assert (conflict.status_code, conflict.json().get('code')) == (409, 'idempotency_conflict')


def test_legacy_detail_keeps_hits_without_an_article_identity(db, admin):
    hits = [{'doc': '과거 참고 조문'}]
    add_feedback(db, 'legacy', hits=hits)
    response = admin.get(ROOT + '/feedbacks/legacy')
    assert response.status_code == 200
    assert response.json()['feedback']['hits'] == hits
    assert response.json()['checks']['corpus']['changed_hits'] == []
