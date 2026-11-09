from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from admin_test_data import seed_qa
from conftest import ADMIN_PW, ADMIN_USER
from lawbot import auth, cache_admin, cache_release_store, cache_review_store, cache_runtime, corpus_store, web
from lawbot.cache_carry import classify, corpus_changes
from lawbot.cache_release_store import apply_release
from lawbot.cache_seed import Bundle, Protection, load_protection
from lawbot.cache_store import Draft, Store, init
from lawbot.db.connection import connect
from lawbot.db.migrate import init as init_corpus
from lawbot.exact_cache import DISPLAY_VERSION, Snapshot, digest
from test_exact_cache import ID, QUESTION, RAW

BASE = {
    'a': ('법 A', '제1조', '조문', '법률', '본문', '0'),
    'b': ('법 A', '제2조', '다른 조문', '법률', '본문', '0'),
    'c': ('법 B', '제3조', '참고', '법률', '본문', '0'),
}


@pytest.mark.parametrize(
    ('updates', 'removed', 'expected'),
    [
        ({}, (), 'keep'),
        ({'a': (*BASE['a'][:4], '바뀜', '0')}, (), 'off'),
        ({'c': (*BASE['c'][:5], '1')}, (), 'off'),
        ({'b': (*BASE['b'][:2], '바뀐 제목', *BASE['b'][3:])}, (), 'pending'),
        ({'d': ('법 A', '제4조', '추가', '법률', '본문', '0')}, (), 'pending'),
        ({}, ('b',), 'pending'),
        ({}, ('a',), 'off'),
    ],
)
def test_classify_by_review_and_display_dependencies(updates, removed, expected):
    current = {key: row for key, row in (BASE | updates).items() if key not in removed}
    assert classify({'a', 'c'}, BASE, corpus_changes(BASE, current), None, 'same', 'same')[0] == expected


@pytest.mark.parametrize(
    ('guard', 'shown'),
    [
        ('policy_changed', 'same'),
        ('protection_changed', 'same'),
        ('bundle_missing', 'same'),
        (None, 'changed'),
        (None, None),
    ],
)
def test_safety_gates_prevent_carry(guard, shown):
    assert classify({'a'}, BASE, {}, guard, 'same', shown)[0] == 'pending'


def release(release_id, other='이전 다른 조문', display='근거 본문', review='검토 참고 본문'):
    text = (
        'law,article_id,article_no,title,kind,text,deleted\n'
        f'근로기준법,{ID},제26조,해고의 예고,법률,{display},0\n'
        f'근로기준법,근로기준법 제27조,제27조,서면통지,법률,{other},0\n'
        f'최저임금법,최저임금법 제6조,제6조,최저임금의 효력,법률,{review},0\n'
    )
    part = corpus_store.CorpusPart('synthetic.csv', text.encode(), {'articles_sha256': digest(text)}, b'')
    return corpus_store.CorpusRelease(release_id, part, part)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    path = tmp_path / 'cache.db'
    init(path)
    store = Store(path)
    old = release('before')
    snapshot = Snapshot.load(old, system='synthetic', combined=True, attach_decrees=True)
    citations = [(ID, snapshot.articles[ID].text_hash)]
    draft = Draft(
        'fixture',
        QUESTION,
        'fixture-answer',
        digest(RAW),
        snapshot.render(RAW, citations),
        citations,
        {'review_evidence_article_ids': ['최저임금법 제6조']},
        RAW,
    )
    store.stage(snapshot.namespace, [draft], 'test', 'draft')
    ns = snapshot.namespace.id
    approval = store.approve(
        ns, 1, 'protected', {draft.query_id(ns): draft.answer_id(ns)}, actor='test', reason='review'
    )
    store.set_mode('shadow', 'test', 'check', approval)
    store.set_mode('on', 'test', 'enable', approval)
    protected = Protection(frozenset(), {'fixture': digest(QUESTION)}, 'protected')
    monkeypatch.setattr(cache_release_store, 'verified_inputs', lambda *a: (protected, {}))
    monkeypatch.setattr(corpus_store, 'release_at_event', lambda *a: old)
    return store, old, snapshot, draft


@pytest.mark.parametrize(
    ('changes', 'verdict'),
    [
        ({}, 'keep'),
        ({'other': '변경된 다른 조문'}, 'pending'),
        ({'display': '변경된 근거 본문'}, 'off'),
        ({'review': '변경된 검토 근거 본문'}, 'off'),
    ],
)
def test_release_projection_and_runtime(prepared, tmp_path, monkeypatch, changes, verdict):
    store, _, previous, draft = prepared
    target = release('after', **changes)
    snapshot = Snapshot.load(target, system='synthetic', combined=True, attach_decrees=True)
    token = ('after', 2)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a, **kw: token)
    monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: token)
    monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: token)
    counts = apply_release(store.path, tmp_path / 'corpus.db', token, target, snapshot, tmp_path / 'fixture.zip')
    assert counts[verdict] == 1 and sum(counts.values()) == 1
    assert store.control().activation_event == 2 and store.control().mode == 'on'
    exact = cache_runtime.ExactCache(store, tmp_path / 'corpus.db', snapshot)
    hit = exact.lookup(QUESTION, 'pro', 'concise').match
    if verdict == 'keep':
        assert hit and hit.answer_id != draft.answer_id(previous.namespace.id)
        assert hit.text == draft.answer_text
    else:
        assert hit is None
    with connect(store.path) as conn:
        assert conn.execute('SELECT verdict,previous_answer_id FROM cache_release_decisions').fetchone() == (
            verdict,
            draft.answer_id(previous.namespace.id),
        )
    assert (
        apply_release(store.path, tmp_path / 'corpus.db', token, target, snapshot, tmp_path / 'fixture.zip') == counts
    )


def test_failed_projection_leaves_control_and_rows_unchanged(prepared, tmp_path, monkeypatch):
    store, _, previous, _ = prepared
    target = release('after')
    snapshot = replace(previous, release_id='after')
    control = store.control()
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('unrelated', 3))
    with pytest.raises(ValueError, match='판정 중'):
        apply_release(store.path, tmp_path / 'corpus.db', ('after', 2), target, snapshot, tmp_path / 'fixture.zip')
    assert store.control() == control
    with connect(store.path) as conn:
        assert conn.execute('SELECT count(*) FROM cache_release_decisions').fetchone() == (0,)


def test_rollback_does_not_restore_withdrawn_question(prepared, tmp_path, monkeypatch):
    store, old, previous, draft = prepared
    target = release('after')
    snapshot = Snapshot.load(target, system='synthetic', combined=True, attach_decrees=True)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('after', 2))
    apply_release(store.path, tmp_path, ('after', 2), target, snapshot, tmp_path / 'fixture.zip')
    store.revoke('query', draft.query_id(snapshot.namespace.id), 'test', 'withdraw')
    monkeypatch.setattr(corpus_store, 'release_at_event', lambda *a: target)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('before', 3))
    apply_release(store.path, tmp_path, ('before', 3), old, previous, tmp_path / 'fixture.zip')
    assert store.lookup(previous.namespace.id, QUESTION)[1] is None


def test_unreviewed_answer_keeps_its_approval_baseline(prepared, tmp_path, monkeypatch):
    store, old, _, _ = prepared
    second = release('second', review='변경된 검토 조문')
    snapshot = Snapshot.load(second, system='synthetic', combined=True, attach_decrees=True)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('second', 2))
    assert apply_release(store.path, tmp_path, ('second', 2), second, snapshot, None)['off'] == 1
    third = replace(second, release_id='third')
    snapshot = Snapshot.load(third, system='synthetic', combined=True, attach_decrees=True)
    monkeypatch.setattr(corpus_store, 'release_at_event', lambda *a: second)
    monkeypatch.setattr(corpus_store, 'release_by_id', lambda *a: old)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('third', 3))
    assert apply_release(store.path, tmp_path, ('third', 3), third, snapshot, None)['off'] == 1
    with connect(store.path) as conn:
        assert conn.execute(
            'SELECT previous_release_id FROM cache_release_decisions WHERE activation_event=3'
        ).fetchone() == ('before',)


def test_renderer_snapshot_is_tied_to_display_version():
    snapshot = Snapshot.load(release('before'), system='synthetic', combined=True, attach_decrees=True)
    citations = [(ID, snapshot.articles[ID].text_hash)]
    actual = [snapshot.render(RAW, citations), snapshot.render(RAW.replace('제26조', '제99조'), citations)]
    expected = {'exact-display-v2': ['합성 결론입니다.\n\n근거: 근로기준법 제26조(해고의 예고)', None]}
    assert actual == expected[DISPLAY_VERSION]


@pytest.fixture
def review_api(prepared, tmp_path, monkeypatch, admin_hash, request):
    store, old, previous, draft = prepared
    target = release('after', other='바뀐 다른 조문')
    snapshot = Snapshot.load(target, system='synthetic', combined=True, attach_decrees=True)
    db = tmp_path / 'corpus.db'
    init_corpus(db)
    seed_qa(db)
    token = ('after', 2)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a, **kw: token)
    monkeypatch.setattr(cache_runtime, 'active_token', lambda *a, **kw: token)
    monkeypatch.setattr(corpus_store, 'release_by_id', lambda *args: old if args[2] == 'before' else target)
    protection = Protection(frozenset(), {'fixture': digest(QUESTION)}, 'protected')
    if getattr(request, 'param', None) == 'extra_csv':
        extra = tmp_path / 'extra.csv'
        extra.write_text('question\n추가 보호 합성 문항\n', encoding='utf-8')
        bundle_fixture = Bundle([], frozenset())
        prior = load_protection(db, bundle_fixture, [extra])
        base = load_protection(db, bundle_fixture, [])
        assert prior.fingerprint != base.fingerprint and prior.blocked > base.blocked
        namespace = previous.namespace.id
        store.set_mode('off', 'test', 'review additional protection')
        approval = store.approve(
            namespace,
            1,
            prior.fingerprint,
            {draft.query_id(namespace): draft.answer_id(namespace)},
            actor='test',
            reason='review',
        )
        store.set_mode('shadow', 'test', 'check', approval)
        store.set_mode('on', 'test', 'enable', approval)
        protection = replace(base, v4_train={'fixture': digest(QUESTION)})
        monkeypatch.setattr(cache_release_store, 'verified_inputs', lambda *a: (protection, {}))
        monkeypatch.setattr(
            cache_admin,
            'context',
            lambda args: (
                snapshot,
                None,
                dict(
                    namespace_id=snapshot.namespace.id,
                    activation_event=token[1],
                    protection_hash=load_protection(db, bundle_fixture, args.protected_csv).fingerprint,
                ),
            ),
        )
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: (protection, {}))
    bundle = tmp_path / 'bundle.zip'
    bundle.write_bytes(b'synthetic bundle fixture')
    assert apply_release(store.path, db, token, target, snapshot, bundle)['pending'] == 1
    cache = cache_runtime.ExactCache(store, db, snapshot)
    state = SimpleNamespace(
        exact_cache=cache,
        settings=SimpleNamespace(exact_cache_bundle=bundle),
        retriever=SimpleNamespace(ids=[], laws=[], kinds=[], titles=[], docs=[]),
    )
    credentials = auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash))
    app = web.create_app(state, ('regular', 'test-only'), db=db, admin=credentials, dist=None)
    return app, store, draft.answer_id(snapshot.namespace.id), state


def test_cache_api_guards_approval_and_idempotency(review_api):
    app, store, answer_id, _ = review_api
    origin = {'Origin': 'http://testserver'}
    path = '/api/admin/cache/actions'
    with TestClient(app) as client:
        assert client.get('/api/admin/cache').status_code == 401
        assert client.post('/api/login', json={'user': 'regular', 'password': 'test-only'}).status_code == 200
        assert client.get('/api/admin/cache').status_code == 403
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        assert detail['status'] == 'pending' and detail['changes'][0]['after']['text'] == '바뀐 다른 조문'
        action = dict(
            action='approve',
            target=answer_id,
            generation=detail['generation'],
            preview_sha256=detail['preview_sha256'],
            idempotency_key='review',
            reason='표시본 확인',
        )
        assert client.post(path, json=action, headers={'Origin': 'http://invalid.test'}).status_code == 403
        assert client.post(path, json=action | {'generation': 0}, headers=origin).status_code == 409
        assert client.post(path, json=action | {'preview_sha256': '0' * 64}, headers=origin).status_code == 409
        first = client.post(path, json=action, headers=origin)
        assert first.status_code == 200, first.json()
        assert client.post(path, json=action, headers=origin).json() == first.json()
        assert client.post(path, json=action | {'reason': '다른 본문'}, headers=origin).status_code == 409
        assert store.lookup(store.control().namespace_id, QUESTION)[1] is not None
    with connect(store.path) as conn:
        assert conn.execute(
            "SELECT reason FROM cache_audit_events WHERE actor=? AND action='approve'", (f'admin:{ADMIN_USER}',)
        ).fetchall() == [('표시본 확인',)]


def test_cache_api_missing_bundle_allows_off_and_withdrawal(review_api):
    app, store, answer_id, state = review_api
    state.settings.exact_cache_bundle = None
    origin = {'Origin': 'http://testserver'}
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        assert 'LAWBOT_EXACT_CACHE_BUNDLE' in detail['disabled_reason'] and detail['preview'] is None
        path = '/api/admin/cache/actions'
        action = dict(generation=detail['generation'], idempotency_key='one', reason='검토')
        assert (
            client.post(path, json=action | dict(action='approve', target=answer_id), headers=origin).status_code == 409
        )
        assert client.post(path, json=action | dict(action='mode', target='off'), headers=origin).status_code == 200
        action = action | dict(generation=store.control().generation, idempotency_key='two')
        assert (
            client.post(path, json=action | dict(action='revoke_answer', target=answer_id), headers=origin).status_code
            == 200
        )
        assert client.get(f'/api/admin/cache/answers/{answer_id}').json()['status'] == 'revoked'


@pytest.mark.parametrize('review_api', ['extra_csv'], indirect=True)
def test_release_preserves_extra_csv_protection_and_requires_cli(review_api, tmp_path):
    app, store, answer_id, _ = review_api
    control = store.control()
    origin = {'Origin': 'http://testserver'}
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        result = client.post(
            '/api/admin/cache/actions',
            headers=origin,
            json=dict(
                action='approve',
                target=answer_id,
                generation=detail['generation'],
                reason='검토',
                preview_sha256=digest(detail['text']),
                idempotency_key='additional-protection',
            ),
        )
        assert result.status_code == 409 and result.json()['code'] == 'protection_changed'
        assert detail['preview_sha256'] is None and 'CLI' in detail['disabled_reason']
        mode = client.post(
            '/api/admin/cache/actions',
            headers=origin,
            json=dict(
                action='mode', target='shadow', generation=control.generation, reason='관찰', idempotency_key='mode'
            ),
        )
        assert (mode.status_code, mode.json()['code']) == (409, 'protection_changed')
        assert store.control() == control
    args = cache_admin.parser().parse_args(
        [
            'set-mode',
            'shadow',
            '--db',
            str(store.path),
            '--bundle',
            str(tmp_path / 'bundle.zip'),
            '--protected-csv',
            str(tmp_path / 'extra.csv'),
            '--approval',
            control.approval_id,
            '--reason',
            'verified original scope',
        ]
    )
    assert cache_admin.run(args) == {'mode': 'shadow'}


def stage_review_drafts(review_api, monkeypatch, count):
    _, store, _, state = review_api
    snapshot = state.exact_cache.snapshot
    citations = [(ID, snapshot.articles[ID].text_hash)]
    shown = snapshot.render(RAW, citations)
    drafts = [
        Draft(str(i), f'합성 초안 질문 {i}인가요?', f'draft-{i}', digest(RAW), shown, citations, {}, RAW)
        for i in range(count)
    ]
    store.stage(snapshot.namespace, drafts, 'test', 'draft')
    protection = Protection(
        frozenset(), {d.source_id: digest(d.question) for d in drafts}, store.control().protection_hash
    )
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: (protection, {}))
    return drafts, protection


@pytest.mark.parametrize(('mode', 'reason'), [('on', {}), ('shadow', {'reason': '   '})])
def test_new_draft_approval_keeps_the_current_mode(review_api, monkeypatch, mode, reason):
    app, store, _, state = review_api
    (draft,), _ = stage_review_drafts(review_api, monkeypatch, 1)
    snapshot = state.exact_cache.snapshot
    store.set_mode(mode, 'test', 'check', store.control().approval_id)
    answer_id = draft.answer_id(snapshot.namespace.id)
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        assert detail['preview_sha256'] == digest(draft.answer_text) and detail['disabled_reason'] is None
        approved = client.post(
            '/api/admin/cache/actions',
            headers={'Origin': 'http://testserver'},
            json=dict(
                action='approve',
                target=answer_id,
                generation=detail['generation'],
                preview_sha256=detail['preview_sha256'],
                idempotency_key='draft',
            )
            | reason,
        )
        assert approved.status_code == 200, approved.json()
        audit = client.get(f'/api/admin/cache/answers/{answer_id}').json()['audit']
        assert audit[0]['action'] == 'approve' and audit[0]['reason'] == '표시본 확인 승인'
    result = state.exact_cache.lookup(draft.question, 'pro', 'concise')
    assert result.mode == mode and result.match is not None, result.reason
    assert result.match.answer_id == answer_id
    assert result.metrics()['would_hit'] == (mode == 'shadow')


@pytest.mark.parametrize('action', ['mode', 'revoke_answer', 'revoke_query'])
def test_cache_api_requires_reason_for_mode_and_withdrawal(review_api, action):
    app, store, answer_id, _ = review_api
    control = store.control()
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        target = {
            'mode': 'off',
            'revoke_answer': answer_id,
            'revoke_query': client.get(f'/api/admin/cache/answers/{answer_id}').json()['questions'][0]['query_id'],
        }[action]
        response = client.post(
            '/api/admin/cache/actions',
            headers={'Origin': 'http://testserver'},
            json=dict(action=action, target=target, generation=control.generation, reason=' ', idempotency_key='empty'),
        )
        assert (response.status_code, response.json()['code']) == (400, 'reason_required')
    assert store.control() == control


BATCH_PATH = '/api/admin/cache/approvals'
ORIGIN = {'Origin': 'http://testserver'}


def batch_payload(client, drafts, namespace, generation):
    items = []
    for draft in drafts:
        answer_id = draft.answer_id(namespace)
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        assert detail['generation'] == generation and detail['preview_sha256']
        items.append(dict(answer_id=answer_id, preview_sha256=detail['preview_sha256']))
    return dict(generation=generation, items=items, idempotency_key='batch')


def test_cache_api_batch_approves_once_and_replays_receipt(review_api, monkeypatch):
    app, store, _, state = review_api
    drafts, _ = stage_review_drafts(review_api, monkeypatch, 2)
    store.set_mode('off', 'test', 'pause')
    namespace = state.exact_cache.snapshot.namespace.id
    control = store.control()
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        body = batch_payload(client, drafts, namespace, control.generation)
        response = client.post(BATCH_PATH, json=body, headers={'Origin': 'http://invalid.test'})
        assert (response.status_code, response.json()['code']) == (403, 'bad_origin')
        stale = client.post(
            BATCH_PATH, json=body | dict(generation=control.generation - 1, idempotency_key='stale'), headers=ORIGIN
        )
        assert (stale.status_code, stale.json()['code']) == (409, 'stale_generation')
        first = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert first.status_code == 200 and first.json() == {'generation': control.generation + 1}
        replay = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert replay.status_code == first.status_code and replay.json() == first.json()
        new_control = store.control()
        assert new_control.generation == control.generation + 1 and new_control.approval_id != control.approval_id
        direct = client.post(
            '/api/admin/cache/actions',
            headers=ORIGIN,
            json=dict(
                action='mode', target='on', generation=new_control.generation, reason='활성화', idempotency_key='on'
            ),
        )
        assert direct.status_code == 200
        for draft in drafts:
            match = state.exact_cache.lookup(draft.question, 'pro', 'concise').match
            assert match is not None and match.answer_id == draft.answer_id(namespace)
        monkeypatch.setattr(app.state.admin_post_limit, 'allowed', lambda _: False)
        limited = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert (limited.status_code, limited.json()['code']) == (429, 'rate_limited')
    with connect(store.path) as conn:
        events = conn.execute(
            "SELECT target,reason,json_extract(detail,'$.approval_id') FROM cache_audit_events "
            "WHERE actor=? AND action='approve' ORDER BY event_id",
            (f'admin:{ADMIN_USER}',),
        ).fetchall()
    assert events == [(d.answer_id(namespace), '표시본 확인 승인', new_control.approval_id) for d in drafts]


@pytest.mark.parametrize(
    ('failure', 'code'),
    [
        ('hash', 'review_changed'),
        ('revoked_answer', 'invalid_state'),
        ('revoked_query', 'review_changed'),
        ('protected', 'review_changed'),
    ],
)
def test_cache_api_batch_rolls_back_and_reports_failed_answer(review_api, monkeypatch, failure, code):
    app, store, _, state = review_api
    drafts, protection = stage_review_drafts(review_api, monkeypatch, 2)
    namespace = state.exact_cache.snapshot.namespace.id
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        body = batch_payload(client, drafts, namespace, store.control().generation)
        failed = body['items'][1]
        if failure == 'hash':
            failed['preview_sha256'] = '0' * 64
        elif failure == 'revoked_answer':
            store.revoke('answer', failed['answer_id'], 'test', 'withdrawn')
        elif failure == 'revoked_query':
            store.revoke('query', drafts[1].query_id(namespace), 'test', 'withdrawn')
        else:
            protection = replace(protection, blocked=frozenset({digest(drafts[1].question)}))
            monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: (protection, {}))
        control = store.control()
        body['generation'] = control.generation
        response = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert response.status_code == 409, response.json()
        assert response.json()['answer_id'] == failed['answer_id'] and response.json()['code'] == code
        assert response.json()['detail']
        replay = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert replay.status_code == response.status_code and replay.json() == response.json()
        assert client.get(f'/api/admin/cache/answers/{body["items"][0]["answer_id"]}').json()['status'] == 'draft'
    assert store.control() == control
    assert all(store.lookup(namespace, draft.question)[1] is None for draft in drafts)
    with connect(store.path) as conn:
        assert conn.execute(
            'SELECT count(*) FROM cache_audit_events WHERE actor=?', (f'admin:{ADMIN_USER}',)
        ).fetchone() == (0,)


def test_cache_api_batch_rolls_back_when_release_changes_during_approval(review_api, monkeypatch):
    app, store, _, state = review_api
    drafts, _ = stage_review_drafts(review_api, monkeypatch, 2)
    namespace = state.exact_cache.snapshot.namespace.id
    control = store.control()
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        body = batch_payload(client, drafts, namespace, control.generation)
        tokens = iter([('after', 2), ('newer', 3)])
        monkeypatch.setattr(corpus_store, 'active_token', lambda *_: next(tokens))
        response = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert (response.status_code, response.json()['code']) == (409, 'stale_release')
        assert store.control() == control
        assert all(store.lookup(namespace, draft.question)[2] == 'inactive' for draft in drafts)
        assert client.post(BATCH_PATH, json=body, headers=ORIGIN).json() == response.json()
    with connect(store.path) as conn:
        assert conn.execute(
            'SELECT count(*) FROM cache_audit_events WHERE actor=?', (f'admin:{ADMIN_USER}',)
        ).fetchone() == (0,)


def test_pending_reapproval_in_on_leaves_new_question_inactive(review_api, prepared, monkeypatch):
    app, store, answer_id, state = review_api
    *_, previous = prepared
    snapshot = state.exact_cache.snapshot
    question = '검토 대기 답변에 새로 연결한 합성 질문인가요?'
    draft = replace(previous, source_id='new', question=question)
    assert draft.answer_id(snapshot.namespace.id) == answer_id
    store.stage(snapshot.namespace, [draft], 'test', 'draft')
    protection = Protection(frozenset(), {'fixture': digest(QUESTION), 'new': digest(question)}, 'protected')
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: (protection, {}))
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        response = client.post(
            '/api/admin/cache/actions',
            headers={'Origin': 'http://testserver'},
            json=dict(
                action='approve',
                target=answer_id,
                generation=detail['generation'],
                reason='검토',
                preview_sha256=detail['preview_sha256'],
                idempotency_key='pending',
            ),
        )
    assert response.status_code == 200
    assert store.lookup(snapshot.namespace.id, QUESTION)[2] == 'hit'
    assert store.lookup(snapshot.namespace.id, question)[2] == 'inactive'
