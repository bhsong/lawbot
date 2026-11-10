"""Calculation cards on Chromium with the real web and model-server apps."""

import re

import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from lawbot import cache_store, deploy
from lawbot.cache_store import Draft, Store, init
from lawbot.corpus_store import active_release, active_token
from lawbot.db import cli
from lawbot.exact_cache import Snapshot, digest, normalize
from lawbot.service import system_text
from test_exact_cache import ID, RAW
from test_migrate_csv_to_sqlite import run as migrate

pytestmark = pytest.mark.e2e_stub

MEDICAL = '2026년 귀속 총급여 5천만 원, 의료비 300만 원이면 의료비 세액공제액을 계산해 주세요.'
MISSING = '2026년 귀속 의료비 300만 원이면 의료비 세액공제액을 계산해 주세요.'
UNSUPPORTED = '월급 300만 원에 2년 일했습니다. 퇴직금을 계산해 주세요.'


@pytest.fixture(scope='module')
def calculation_stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('calculation_e2e')
    corpus = tmp / 'private' / 'corpus.db'
    cli.main(['init', '--db', str(corpus)])
    profile = deploy.get_profile({'LAWBOT_PROFILE': 'v5'})
    assert (
        migrate(
            corpus, 'corpus', '--dir', profile.articles.parent, '--profile', 'v5', '--release-id', 'test', '--activate'
        )
        == 0
    )
    path = tmp / 'private' / 'exact.db'
    init(path)
    store = Store(path)
    snapshot = Snapshot.load(
        active_release(corpus, 'v5'),
        system=system_text(profile, profile.models[0]),
        combined=False,
        attach_decrees=False,
    )
    citations = [(ID, snapshot.articles[ID].text_hash)]
    text = snapshot.render(RAW, citations)
    draft = Draft('fixture', MEDICAL, 'key', digest(RAW), text, citations, {})
    namespace = snapshot.namespace.id
    with pytest.MonkeyPatch.context() as legacy_policy:
        legacy_policy.setattr(cache_store, 'is_calculation_question', lambda question: False)
        store.stage(snapshot.namespace, [draft], 'tester', 'synthetic draft')
        approval = store.approve(
            namespace,
            active_token(corpus, 'v5')[1],
            'synthetic',
            {draft.query_id(namespace): draft.answer_id(namespace)},
            actor='tester',
            reason='test',
        )
    store.set_mode('on', 'tester', 'test', approval)
    assert store.lookup(namespace, normalize(MEDICAL))[2] == 'hit'
    stack = e2e.Stack(
        tmp,
        3,
        LAWBOT_PROFILE='v5',
        LAWBOT_CORPUS='db',
        LAWBOT_DB=str(corpus),
        LAWBOT_EXACT_CACHE_DB=str(path),
        LAWBOT_DECREE_ENABLED='0',
        LAWBOT_RERANKER_ENABLED='1',
    )
    try:
        stack.ensure('calculation')
        yield stack
    finally:
        stack.stop()


@pytest.fixture
def ui(calculation_stack, page, request):
    e2e.login(page, calculation_stack.url)
    e2e.textbox(page).wait_for()
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', calculation_stack.logs(), page)


def ask_with_events(page, stack, question):
    with page.expect_response(f'{stack.url}/api/chat', timeout=e2e.ANSWER_TIMEOUT) as response:
        e2e.ask(page, question)
    events = list(dict.fromkeys(re.findall(r'^event: (\S+)', response.value.text(), re.MULTILINE)))
    assert 'done' in events, events
    e2e.wait_done(page)
    return events


@pytest.mark.parametrize('width', [390, 1280])
def test_calculation_and_guidance_cards(calculation_stack, ui, width):
    ui.set_viewport_size({'width': width, 'height': 844})
    events = ask_with_events(ui, calculation_stack, MEDICAL)
    relevant = ['calculation', 'answer', 'calculation_check', 'done']
    assert [event for event in events if event in relevant] == relevant, events
    result = ui.get_by_role('region', name='계산 결과')
    expect(result.get_by_text('225,000원', exact=True).first).to_be_visible()
    expect(result.get_by_text(re.compile(r'미입력\(0\) \d+건'))).to_be_visible()
    expect(result.get_by_role('alert')).to_contain_text('계산 표를 기준으로')
    expect(e2e.answer(ui)).to_contain_text('의료비 세액공제액은 1원입니다.')
    result.get_by_text('입력과 계산 단계', exact=True).click()
    inputs = result.get_by_role('table', name='계산 입력')
    expect(inputs.get_by_role('row', name='실손의료보험금 0원 미입력(0)')).to_be_visible()
    expect(inputs.get_by_text('질문: 5천만 원', exact=True)).to_be_visible()
    stages = result.get_by_role('table', name='단계별 계산')
    expect(stages).to_be_visible()
    expect(stages.get_by_role('columnheader', name='식')).to_be_visible()
    expect(stages.get_by_text('소득세법 제59조의4', exact=True).first).to_be_visible()
    assert ui.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    assert stages.evaluate('table => table.scrollWidth <= table.clientWidth + 1')

    for question, message in ((MISSING, re.compile('총급여')), (UNSUPPORTED, re.compile('계산기|정보'))):
        events = ask_with_events(ui, calculation_stack, question)
        assert [event for event in events if event in relevant] == ['calculation', 'answer', 'done'], events
        guidance = ui.get_by_role('region', name='계산 안내').last
        expect(guidance.get_by_text(message).first).to_be_visible()
        expect(guidance.get_by_role('table')).to_have_count(0)
        expect(e2e.answer(ui)).to_contain_text('계산에 필요한 자료와 근거 조문을 확인해 주세요.')
        if question == MISSING:
            expect(guidance.get_by_text('소득세법 제59조의4', exact=True)).to_be_visible()

    rows = e2e.request_log(calculation_stack.request_log)[-3:]
    assert len(rows) == 3
    for row in rows:
        assert row['cache_reason'] == 'calculation_question' and row['response_source'] == 'rag'
        assert row['cache_eligible'] is False and row['exact_lookup_ms'] is None
        assert row['call_counts']['router'] == row['call_counts']['llm'] == 1
