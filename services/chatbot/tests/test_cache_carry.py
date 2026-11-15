import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from admin_test_data import seed_qa
from conftest import ADMIN_PW, ADMIN_USER
from lawbot import (
    auth,
    cache_admin,
    cache_carry,
    cache_release_store,
    cache_review_store,
    cache_runtime,
    cache_store,
    corpus_store,
    web,
)
from lawbot.cache_carry import classify, corpus_changes
from lawbot.cache_release_store import apply_release
from lawbot.cache_seed import Protection, load_protection
from lawbot.cache_store import Draft, Store, init, revoke_in
from lawbot.db.connection import connect, transaction
from lawbot.db.migrate import init as init_corpus
from lawbot.exact_cache import DISPLAY_VERSION, Snapshot, digest
from test_exact_cache import CALCULATION, ID, QUESTION, RAW

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
        ('protection_unavailable', 'same'),
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
    monkeypatch.setattr(cache_release_store, 'verified_inputs', lambda *a: protected)
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
    counts = apply_release(store.path, tmp_path / 'corpus.db', token, target, snapshot)
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
    assert apply_release(store.path, tmp_path / 'corpus.db', token, target, snapshot) == counts


@pytest.mark.parametrize('mixed', [False, True])
def test_release_projection_does_not_restore_legacy_calculation_links(prepared, tmp_path, monkeypatch, mixed):
    store, _, previous, ordinary = prepared
    namespace = previous.namespace.id
    calculation = replace(ordinary, source_id='calculation', question=CALCULATION)
    store.set_mode('off', 'test', 'legacy review')
    with monkeypatch.context() as before_policy:
        before_policy.setattr(cache_store, 'is_calculation_question', lambda question: False)
        store.stage(previous.namespace, [calculation], 'test', 'legacy draft')
        selected = (ordinary, calculation) if mixed else (calculation,)
        store.approve(
            namespace,
            1,
            'protected',
            {draft.query_id(namespace): draft.answer_id(namespace) for draft in selected},
            actor='test',
            reason='legacy review',
        )
    if not mixed:
        store.revoke('query', ordinary.query_id(namespace), 'test', 'ordinary link withdrawn')
    target = release('after')
    snapshot = Snapshot.load(target, system='synthetic', combined=True, attach_decrees=True)
    token = ('after', 2)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *args: token)
    counts = apply_release(store.path, tmp_path / 'corpus.db', token, target, snapshot)
    assert counts == {'keep': int(mixed), 'pending': 0, 'off': int(not mixed)}
    with connect(store.path) as conn:
        rows = dict(
            conn.execute(
                'SELECT source_id,exact_enabled FROM cache_queries WHERE namespace_id=?', (snapshot.namespace.id,)
            )
        )
    assert rows == {'calculation': 0, 'fixture': int(mixed)}


def test_failed_projection_leaves_control_and_rows_unchanged(prepared, tmp_path, monkeypatch):
    store, _, previous, _ = prepared
    target = release('after')
    snapshot = replace(previous, release_id='after')
    control = store.control()
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('unrelated', 3))
    with pytest.raises(ValueError, match='판정 중'):
        apply_release(store.path, tmp_path / 'corpus.db', ('after', 2), target, snapshot)
    assert store.control() == control
    with connect(store.path) as conn:
        assert conn.execute('SELECT count(*) FROM cache_release_decisions').fetchone() == (0,)


def test_rollback_does_not_restore_withdrawn_question(prepared, tmp_path, monkeypatch):
    store, old, previous, draft = prepared
    target = release('after')
    snapshot = Snapshot.load(target, system='synthetic', combined=True, attach_decrees=True)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('after', 2))
    apply_release(store.path, tmp_path, ('after', 2), target, snapshot)
    store.revoke('query', draft.query_id(snapshot.namespace.id), 'test', 'withdraw')
    monkeypatch.setattr(corpus_store, 'release_at_event', lambda *a: target)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('before', 3))
    apply_release(store.path, tmp_path, ('before', 3), old, previous)
    assert store.lookup(previous.namespace.id, QUESTION)[1] is None


def test_unreviewed_answer_keeps_its_approval_baseline(prepared, tmp_path, monkeypatch):
    store, old, _, _ = prepared
    second = release('second', review='변경된 검토 조문')
    snapshot = Snapshot.load(second, system='synthetic', combined=True, attach_decrees=True)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('second', 2))
    assert apply_release(store.path, tmp_path, ('second', 2), second, snapshot)['off'] == 1
    third = replace(second, release_id='third')
    snapshot = Snapshot.load(third, system='synthetic', combined=True, attach_decrees=True)
    monkeypatch.setattr(corpus_store, 'release_at_event', lambda *a: second)
    monkeypatch.setattr(corpus_store, 'release_by_id', lambda *a: old)
    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: ('third', 3))
    assert apply_release(store.path, tmp_path, ('third', 3), third, snapshot)['off'] == 1
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
        prior = load_protection(db, [extra])
        base = load_protection(db, [])
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
        monkeypatch.setattr(cache_release_store, 'verified_inputs', lambda *a: protection)
        monkeypatch.setattr(
            cache_admin,
            'scope',
            lambda args: cache_admin.CorpusContext(db, snapshot, token[1], load_protection(db, args.protected_csv)),
        )
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: protection)
    assert apply_release(store.path, db, token, target, snapshot)['pending'] == 1
    cache = cache_runtime.ExactCache(store, db, snapshot)
    state = SimpleNamespace(
        exact_cache=cache,
        settings=SimpleNamespace(),
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


@pytest.mark.parametrize('mixed', [False, True])
def test_calculation_links_are_off_and_only_other_links_can_be_approved(review_api, monkeypatch, mixed):
    app, store, answer_id, state = review_api
    namespace = state.exact_cache.snapshot.namespace
    snapshot = state.exact_cache.snapshot
    citations = [(ID, snapshot.articles[ID].text_hash)]
    calculation = Draft(
        'calculation', CALCULATION, 'fixture-answer', digest(RAW), snapshot.render(RAW, citations), citations, {}, RAW
    )
    with monkeypatch.context() as before_policy:
        before_policy.setattr(cache_store, 'is_calculation_question', lambda question: False, raising=False)
        assert store.stage(namespace, [calculation], 'test', 'legacy') == 1
    if not mixed:
        with connect(store.path, write=True) as conn, transaction(conn):
            revoke_in(
                conn,
                'query',
                conn.execute(
                    'SELECT query_id FROM cache_queries WHERE answer_id=? AND source_id=?', (answer_id, 'fixture')
                ).fetchone()[0],
                'test',
                'remove ordinary query',
            )
    protection = Protection(frozenset(), {'fixture': digest(QUESTION), 'calculation': digest(CALCULATION)}, 'protected')
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *args: protection)
    origin = {'Origin': 'http://testserver'}
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        data = client.get('/api/admin/cache').json()
        assert data['calculation_excluded_count'] == 1
        item = next(row for row in data['items'] if row['answer_id'] == answer_id)
        assert item['status'] == ('pending' if mixed else 'off') and item['calculation_excluded_count'] == 1
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        assert next(row for row in detail['questions'] if row['question'] == CALCULATION)['status'] == 'off'
        path = '/api/admin/cache/actions'
        action = dict(
            action='approve',
            target=answer_id,
            generation=detail['generation'],
            preview_sha256=detail['preview_sha256'] or '0' * 64,
            idempotency_key='legacy',
            reason='표시본 확인',
        )
        result = client.post(path, json=action, headers=origin)
        if mixed:
            assert result.status_code == 200, result.json()
            assert store.lookup(namespace.id, QUESTION)[1] is not None
        else:
            assert detail['preview_sha256'] is None
            assert result.status_code == 409 and result.json()['code'] == 'calculation_question'
        assert store.lookup(namespace.id, CALCULATION)[1] is None


def forbid_zip(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('runtime opened a ZIP archive')

    monkeypatch.setattr(zipfile.ZipFile, '__init__', forbidden)


def test_carry_inputs_come_from_the_corpus_db_only(tmp_path, monkeypatch):
    db = tmp_path / 'corpus.db'
    init_corpus(db)
    seed_qa(db)
    forbid_zip(monkeypatch)
    assert cache_carry.verified_inputs(db) == load_protection(db, [])


@pytest.fixture
def db_transfer(tmp_path, monkeypatch):
    import json

    from lawbot.cache_seed_store import Answer, Handoff, Question, Reference, import_seed
    from lawbot.db.backup import backup

    corpus = tmp_path / 'corpus.db'
    source = tmp_path / 'source.db'
    source_copy = tmp_path / 'source-copy.db'
    target = tmp_path / 'target.db'
    init_corpus(corpus)
    seed_qa(corpus)
    assert init(source) == init(target) == 2
    old = release('before')
    snapshot = Snapshot.load(old, system='synthetic', combined=True, attach_decrees=True)
    ref = Reference(ID, snapshot.articles[ID].text_hash)
    dependency = '최저임금법 제6조'
    evidence = Reference(dependency, snapshot.articles[dependency].text_hash)
    variant = '검토를 마친 합성 질문입니다.'
    seed = Handoff(
        'a' * 64,
        (Answer('seed', 'unit', ID, RAW, digest(RAW), (ref,), (evidence,), {}, ref.sha256),),
        (Question('fixture', 'seed', QUESTION, {}), Question('variant', 'seed', variant, {})),
    )
    shown = snapshot.render(RAW, [(ID, ref.sha256)])
    assert shown is not None
    protection = load_protection(corpus, [])
    draft = Draft('fixture', QUESTION, 'seed', digest(RAW), shown, [(ID, ref.sha256)], {}, RAW)
    Store(source).stage(snapshot.namespace, [draft], 'test', 'existing')
    ns = snapshot.namespace.id
    Store(source).approve(
        ns, 1, protection.fingerprint, {draft.query_id(ns): draft.answer_id(ns)}, actor='test', reason='existing'
    )
    assert import_seed(source, seed, snapshot, 1, protection)['approved_questions'] == 2
    with connect(source) as conn:
        assert json.loads(conn.execute('SELECT source FROM cache_answers').fetchone()[0]) == {}
        assert conn.execute("SELECT count(*) FROM cache_audit_events WHERE action='review_evidence'").fetchone() == (1,)
    Store(source).stage(
        snapshot.namespace,
        [replace(draft, source_id='draft', question='검토 대기 질문', answer_key='draft')],
        'test',
        'draft',
    )
    backup(source, source_copy)
    token = ('before', 1)
    monkeypatch.setattr(cache_admin, 'active_token', lambda *a: token)
    monkeypatch.setattr(cache_admin, 'active_release', lambda *a: old)
    monkeypatch.setattr(cache_admin, 'cache_snapshot', lambda *a: snapshot)
    forbid_zip(monkeypatch)
    args = cache_admin.parser().parse_args(
        ['import-db', '--from', str(source_copy), '--db', str(target), '--corpus-db', str(corpus)]
    )
    return args, snapshot, draft, protection, evidence, seed.sha256


def test_import_db_uses_only_approved_catalog_rows_without_opening_zip(db_transfer):
    import json
    from hashlib import sha256
    from pathlib import Path

    from lawbot.exact_cache import canonical

    args, _, _, _, evidence, bundle_hash = db_transfer
    source, target = Path(args.source_db), Path(args.db)
    source_hash = sha256(source.read_bytes()).hexdigest()
    result = cache_admin.run(args)
    assert result['approved_answers'] == 1
    assert result['approved_questions'] == result['input_questions'] == 2
    control = Store(target).control()
    assert control.mode == 'off' and control.approval_id == result['approval_id']
    with connect(target) as conn:
        receipt, raw = conn.execute(
            "SELECT target,detail FROM cache_audit_events WHERE action='cache_db_import'"
        ).fetchone()
        detail = json.loads(raw)
        assert detail['source_db_sha256'] == source_hash and detail['bundle_sha256'] == bundle_hash
        assert control.approval_id == digest(canonical([None, receipt]))
        evidence_hashes = json.loads(conn.execute('SELECT source FROM cache_answers').fetchone()[0])[
            'review_evidence_hashes'
        ]
        assert evidence_hashes[evidence.article_id] == evidence.sha256
        count = conn.execute('SELECT count(*) FROM cache_audit_events').fetchone()
    assert cache_admin.run(args) == result
    assert Store(target).control() == control
    assert sha256(source.read_bytes()).hexdigest() == source_hash
    with connect(target) as conn:
        assert conn.execute('SELECT count(*) FROM cache_audit_events').fetchone() == count


def test_import_db_excludes_a_conflicting_revision_and_rotates_the_approval(db_transfer):
    import json
    from pathlib import Path

    from lawbot.exact_cache import canonical

    args, snapshot, staged, protection, _, _ = db_transfer
    target = Path(args.db)
    store = Store(target)
    draft = replace(staged, answer_key='conflicting-revision')
    namespace = snapshot.namespace.id
    store.stage(snapshot.namespace, [draft], 'test', 'existing')
    previous = store.approve(
        namespace,
        1,
        protection.fingerprint,
        {draft.query_id(namespace): draft.answer_id(namespace)},
        actor='test',
        reason='prior',
    )
    result = cache_admin.run(args)
    assert result['existing_conflicts'] == result['approved_questions'] == 1
    with connect(target) as conn:
        receipt, detail = conn.execute(
            "SELECT target,detail FROM cache_audit_events WHERE action='cache_db_import'"
        ).fetchone()
        assert json.loads(detail)['previous_approval_id'] == previous
        assert conn.execute(
            'SELECT answer_id FROM cache_queries WHERE query_id=?', (draft.query_id(namespace),)
        ).fetchone() == (draft.answer_id(namespace),)
    assert result['approval_id'] == digest(canonical([previous, receipt]))
    with pytest.raises(ValueError, match='현재 승인 묶음'):
        store.set_mode('on', 'test', 'old approval', previous)
    store.set_mode('on', 'test', 'new approval', result['approval_id'])
    with pytest.raises(ValueError, match='off로 전환'):
        cache_admin.run(args)


@pytest.mark.parametrize(
    'alteration',
    [
        "UPDATE cache_render_inputs SET raw_answer='changed'",
        'DELETE FROM cache_citations',
        "UPDATE cache_queries SET raw_sha256='changed' WHERE status='approved'",
    ],
)
def test_import_db_rolls_back_on_corrupt_source_rows(db_transfer, alteration):
    from pathlib import Path

    args, _, _, _, _, _ = db_transfer
    with connect(Path(args.source_db), write=True) as conn:
        conn.execute(alteration)
    target = Path(args.db)
    control = Store(target).control()
    with pytest.raises(ValueError, match=r'원천 캐시.*해시'):
        cache_admin.run(args)
    assert Store(target).control() == control
    with connect(target) as conn:
        assert conn.execute('SELECT count(*) FROM cache_answers').fetchone() == (0,)
        assert conn.execute('SELECT count(*) FROM cache_audit_events').fetchone() == (0,)


def test_import_db_rechecks_target_protection_and_review_evidence(db_transfer, tmp_path, monkeypatch):
    from pathlib import Path

    args, snapshot, _, _, evidence, _ = db_transfer
    protected = tmp_path / 'protected.csv'
    protected.write_text(f'question\n  {QUESTION}  \n', encoding='utf-8')
    args.protected_csv = [protected]
    article = snapshot.articles[evidence.article_id]
    current = replace(
        snapshot, articles=snapshot.articles | {evidence.article_id: replace(article, text_hash='changed')}
    )
    monkeypatch.setattr(cache_admin, 'cache_snapshot', lambda *a: current)
    result = cache_admin.run(args)
    assert result['seed_protected_exact'] == result['blocked_evidence_questions'] == 1
    assert result['approved_questions'] == 0
    with connect(Path(args.db)) as conn:
        assert conn.execute('SELECT status,exact_enabled FROM cache_queries').fetchall() == [('draft', 0)]


def test_cache_review_approves_without_any_zip(review_api, monkeypatch):
    app, store, answer_id, _ = review_api
    forbid_zip(monkeypatch)
    origin = {'Origin': 'http://testserver'}
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        detail = client.get(f'/api/admin/cache/answers/{answer_id}').json()
        assert detail['disabled_reason'] is None and detail['preview'] is not None
        action = dict(
            action='approve',
            target=answer_id,
            generation=detail['generation'],
            preview_sha256=detail['preview_sha256'],
            idempotency_key='zip-free',
            reason='표시본 확인',
        )
        assert client.post('/api/admin/cache/actions', json=action, headers=origin).status_code == 200


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
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: protection)
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
            monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: protection)
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


def test_cache_api_batch_rendering_allows_revocation_and_rechecks_generation(review_api, monkeypatch):
    app, store, _, state = review_api
    drafts, _ = stage_review_drafts(review_api, monkeypatch, 2)
    namespace = state.exact_cache.snapshot.namespace.id
    control = store.control()
    with TestClient(app) as client:
        assert client.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        body = batch_payload(client, drafts, namespace, control.generation)
        preview = cache_review_store.preview
        revoked = drafts[1].answer_id(namespace)

        def preview_with_revocation(*args):
            if store.control().generation == control.generation:
                with connect(store.path, write=True, timeout=0) as conn, transaction(conn):
                    revoke_in(conn, 'answer', revoked, 'test', 'concurrent withdrawal')
            return preview(*args)

        monkeypatch.setattr(cache_review_store, 'preview', preview_with_revocation)
        response = client.post(BATCH_PATH, json=body, headers=ORIGIN)
        assert (response.status_code, response.json()['code']) == (409, 'stale_generation')
        assert client.post(BATCH_PATH, json=body, headers=ORIGIN).json() == response.json()
    assert store.control().generation == control.generation + 1
    assert store.control().approval_id == control.approval_id
    with connect(store.path) as conn:
        assert conn.execute('SELECT status FROM cache_answers WHERE answer_id=?', (revoked,)).fetchone() == ('revoked',)
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
    monkeypatch.setattr(cache_review_store, 'verified_inputs', lambda *a: protection)
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


@pytest.mark.parametrize('review_changed', [False, True])
def test_pass_import_preserves_legacy_exact_review_and_release_paths(tmp_path, monkeypatch, review_changed):
    from lawbot.cache_seed import P07_SHA256
    from lawbot.cache_seed_store import Answer, Handoff, Question, Reference, import_seed

    corpus = tmp_path / 'corpus.db'
    init_corpus(corpus)
    variant = '합성 대체 표현의 해고 예고 절차는 무엇인가요?'
    review_question = '별도로 검수할 합성 질문입니다.'
    seed_qa(corpus, v4_train=(QUESTION, variant, review_question))
    path = tmp_path / 'cache.db'
    init(path)
    store = Store(path)
    old = release('before')
    snapshot = Snapshot.load(old, system='synthetic', combined=True, attach_decrees=True)
    token = ('before', 1)
    ref = Reference(ID, snapshot.articles[ID].text_hash)
    protection = load_protection(corpus, [])
    shown = snapshot.render(RAW, [(ID, ref.sha256)])
    assert shown is not None
    ns = snapshot.namespace.id
    draft = Draft('1', QUESTION, 'old-key', digest(RAW), shown, [(ID, ref.sha256)], {'bundle_sha256': P07_SHA256}, RAW)
    review = replace(draft, source_id='3', question=review_question, answer_key='review')
    store.stage(snapshot.namespace, [draft, review], 'test', 'stage')
    approval = store.approve(
        ns, 1, protection.fingerprint, {draft.query_id(ns): draft.answer_id(ns)}, actor='test', reason='legacy review'
    )
    dependency = '최저임금법 제6조'
    evidence = (Reference(dependency, snapshot.articles[dependency].text_hash),)
    seed_answer = Answer('old-key', 'seed', ID, RAW, digest(RAW), (ref,), evidence, {}, ref.sha256)
    seed = Handoff(
        'a' * 64, (seed_answer,), (Question('1', 'old-key', QUESTION, {}), Question('2', 'old-key', variant, {}))
    )
    result = import_seed(path, seed, snapshot, 1, load_protection(corpus, []))
    assert result['approved_questions'] == 2

    monkeypatch.setattr(corpus_store, 'active_token', lambda *a: token)
    monkeypatch.setattr(cache_admin, 'active_token', lambda *a: token)
    monkeypatch.setattr(cache_admin, 'active_release', lambda *a: old)
    monkeypatch.setattr(cache_admin, 'cache_snapshot', lambda *a: snapshot)
    monkeypatch.setattr(
        cache_admin.settings,
        'load',
        lambda: SimpleNamespace(db=corpus, corpus='db', profile=SimpleNamespace(name='v5'), decree=None),
    )
    if not review_changed:
        old_mode = cache_admin.parser().parse_args(
            ['set-mode', 'on', '--db', str(path), '--approval', approval, '--reason', 'previous review']
        )
        with pytest.raises(ValueError, match='현재 승인 묶음'):
            cache_admin.run(old_mode)

    catalog = cache_review_store.ReviewCatalog(path, corpus, snapshot)
    detail = catalog.detail(review.answer_id(ns))
    assert detail['preview_sha256'] == digest(shown) and detail['disabled_reason'] is None
    with connect(path, write=True) as conn, transaction(conn):
        catalog.change_mode(conn, store.control(), 'shadow', 'test', 'review')
    assert store.control().mode == 'shadow'

    def unexpected_scan(*a):
        pytest.fail('activation repeated the near-duplicate scan')

    monkeypatch.setattr(Protection, 'match', unexpected_scan)
    args = cache_admin.parser().parse_args(
        [
            'set-mode',
            'shadow',
            '--db',
            str(path),
            '--approval',
            result['approval_id'],
            '--reason',
            'verified seed',
        ]
    )
    assert cache_admin.run(args) == {'mode': 'shadow'}
    extra = tmp_path / 'additional-protected.csv'
    extra.write_text('question\n별도 보호 대상 합성 문항\n', encoding='utf-8')
    args.protected_csv = [extra]
    with pytest.raises(ValueError, match='보호 분할이 변경'):
        cache_admin.run(args)
    assert store.control().approval_id == result['approval_id']

    monkeypatch.setattr(corpus_store, 'release_at_event', lambda *a: old)
    target = release('after', review='검토 참고 조문 변경') if review_changed else release('after')
    token = ('after', 2)
    current = Snapshot.load(target, system='synthetic', combined=True, attach_decrees=True)
    assert apply_release(path, corpus, token, target, current) == {
        'keep': int(not review_changed),
        'pending': 0,
        'off': int(review_changed),
    }
