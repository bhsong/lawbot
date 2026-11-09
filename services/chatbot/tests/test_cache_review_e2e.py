"""Release review through Chromium, a real web app, and the model server with fakes."""

import json
import sys

import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from admin_test_data import seed_qa
from conftest import ADMIN_PW, ADMIN_USER
from lawbot import cache_release_store, corpus_store, deploy, manifest
from lawbot.cache_release_store import apply_release
from lawbot.cache_seed import Bundle, load_protection
from lawbot.cache_store import Draft, Store, init
from lawbot.db import cli
from lawbot.exact_cache import Snapshot, digest
from lawbot.service import system_text
from test_exact_cache import QUESTION, RAW
from test_exact_cache_e2e import request_row
from test_migrate_csv_to_sqlite import run as migrate

pytestmark = pytest.mark.e2e_stub
KEEP_QUESTION = '별개 법령의 합성 캐시 질문인가요?'


@pytest.fixture
def review_stack(tmp_path, page, request, admin_hash, monkeypatch):
    db = tmp_path / 'private' / 'corpus.db'
    cli.main(['init', '--db', str(db)])
    seed_qa(db, v4_train=[QUESTION, KEEP_QUESTION])
    profile = deploy.get_profile({'LAWBOT_PROFILE': 'v5'})
    assert (
        migrate(
            db, 'corpus', '--dir', profile.articles.parent, '--profile', 'v5', '--release-id', 'before', '--activate'
        )
        == 0
    )
    path = tmp_path / 'private' / 'cache.db'
    init(path)
    store = Store(path)
    system = system_text(profile, profile.models[0])
    old = corpus_store.active_release(db, 'v5')
    snapshot = Snapshot.load(old, system=system, combined=False, attach_decrees=False)
    drafts = []
    for source_id, question, article_id, key in (
        ('1', QUESTION, '근로기준법 제27조', '합성 검토 답변'),
        ('2', KEEP_QUESTION, '최저임금법 제6조', '합성 유지 답변'),
    ):
        raw = RAW.replace('근로기준법 제26조', article_id)
        citations = [(article_id, snapshot.articles[article_id].text_hash)]
        shown = snapshot.render(raw, citations)
        assert shown is not None
        drafts.append(
            Draft(
                source_id,
                question,
                key,
                digest(raw),
                shown,
                citations,
                {'review_evidence_article_ids': [article_id]},
                raw,
            )
        )
    store.stage(snapshot.namespace, drafts, 'tester', 'draft')
    ns = snapshot.namespace.id
    protection = load_protection(db, Bundle([], frozenset()), [])
    token = corpus_store.active_token(db, 'v5')
    approval = store.approve(
        ns,
        token[1],
        protection.fingerprint,
        {d.query_id(ns): d.answer_id(ns) for d in drafts},
        actor='tester',
        reason='approve',
    )
    store.set_mode('shadow', 'tester', 'check', approval)
    store.set_mode('on', 'tester', 'enable', approval)
    folder = tmp_path / 'next'
    folder.mkdir()
    for file in deploy.article_paths('v5'):
        original = file.read_bytes()
        changed = original.replace(',해고의 예고,'.encode(), ',해고의 예고 변경 후,'.encode(), 1)
        assert changed != original
        (folder / file.name).write_bytes(changed)
        updated = manifest.load(file) | {'articles_sha256': manifest.sha256(changed)}
        (folder / manifest.path_for(file).name).write_text(json.dumps(updated), encoding='utf-8')
    assert migrate(db, 'corpus', '--dir', folder, '--profile', 'v5', '--release-id', 'after', '--activate') == 0
    token = corpus_store.active_token(db, 'v5')
    current = corpus_store.active_release(db, 'v5')
    new_snapshot = Snapshot.load(current, system=system, combined=False, attach_decrees=False)
    monkeypatch.setattr(cache_release_store, 'verified_inputs', lambda *args: (protection, {}))
    bundle = tmp_path / 'synthetic.txt'
    bundle.write_text('synthetic P07 bundle', encoding='utf-8')
    assert apply_release(path, db, token, current, new_snapshot, bundle) == {'keep': 1, 'pending': 1, 'off': 0}
    stack = e2e.Stack(
        tmp_path,
        3,
        web_command=[sys.executable, str(e2e.TESTS / 'cache_review_test_server.py')],
        LAWBOT_PROFILE='v5',
        LAWBOT_CORPUS='db',
        LAWBOT_DB=str(db),
        LAWBOT_EXACT_CACHE_DB=str(path),
        LAWBOT_EXACT_CACHE_BUNDLE=str(bundle),
        LAWBOT_ADMIN_USER=ADMIN_USER,
        LAWBOT_ADMIN_PW_HASH=admin_hash,
        LAWBOT_DECREE_ENABLED='0',
        LAWBOT_RERANKER_ENABLED='1',
    )
    try:
        yield stack
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
    finally:
        stack.stop()


@pytest.mark.parametrize('width', [390, 1280])
def test_admin_reviews_carried_answer_and_controls_cache(review_stack, page, width):
    stack = review_stack
    page.set_viewport_size({'width': width, 'height': 844})
    e2e.login(page, stack.url, user=ADMIN_USER, pw=ADMIN_PW)
    e2e.ask(page, KEEP_QUESTION)
    assert request_row(stack, 1)['call_counts'] == dict.fromkeys(('embedding', 'search', 'rerank', 'llm'), 0)
    e2e.ask(page, QUESTION)
    assert request_row(stack, 2)['response_source'] == 'rag'
    admin = page.get_by_role('radio', name='피드백 & 법령 관리')
    admin.click()
    page.get_by_role('button', name='캐시', exact=True).click()
    panel = page.get_by_role('region', name='정확 일치 캐시 관리')
    panel.get_by_label('판정 필터').select_option('pending')
    panel.get_by_role('button', name='합성 검토 답변 · 검토 대기').click()
    panel.get_by_text('답변 원문', exact=True).click()
    expect(panel.get_by_text('합성 참고', exact=False)).to_be_visible()
    panel.get_by_text('근로기준법 제26조 비교', exact=True).click()
    expect(panel.get_by_role('heading', name='이전 본문')).to_be_visible()
    expect(panel.get_by_text('해고의 예고 변경 후', exact=False)).to_be_visible()
    expect(panel.get_by_text('재승인 즉시 사용자에게 반환됩니다.', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    panel.get_by_label('변경 사유').fill('합성 재승인')
    panel.get_by_role('button', name='표시본 확인 및 재승인').click()
    expect(panel.get_by_text('변경을 저장했습니다.')).to_be_visible()
    page.get_by_role('radio', name='채팅', exact=True).click()
    e2e.ask(page, QUESTION)
    assert request_row(stack, 3)['response_source'] == 'exact_cache'
    admin.click()
    page.get_by_role('button', name='캐시', exact=True).click()
    panel.get_by_label('변경 사유').fill('합성 중단')
    panel.get_by_role('button', name='모드 off').click()
    expect(panel.get_by_text('현재 모드 off', exact=False)).to_be_visible()
    page.get_by_role('radio', name='채팅', exact=True).click()
    e2e.ask(page, QUESTION)
    assert request_row(stack, 4)['response_source'] == 'rag'
