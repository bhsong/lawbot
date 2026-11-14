"""Calculation cards on Chromium with the real web and model-server apps."""

import re
import sys

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
UNSUPPORTED = '월급 300만 원을 받으면 건강보험료가 얼마인지 계산해 주세요.'
MIDYEAR = '2026년 중도퇴사 총급여 2000만원, 건강보험료 50만원, 장기요양보험료 5만원입니다. 소득세는 얼마인가요?'
LOOKUP = '2026년 근로소득자의 표준세액공제 법정 금액은 얼마인가요?'
SEVERANCE = '월급 300만원으로 3년 일했습니다. 퇴직금을 얼마 받나요?'
ANNUAL = '상시 5명, 주 소정 40시간입니다. 근속 2년을 마쳤고 연차 발생일에도 재직하며 직전 1년 출근율 96%입니다. 이번에 새로 생기는 연차가 며칠인가요?'
ANNUAL_MISSING = '근속 2년하고 11개월이면 연차가 며칠인지 산출해 주세요.'
ANNUAL_INVALID = (
    '상시 8명, 주 소정 40시간, 출근율 92%입니다. 입사일은 2024년 2월 30일이고 '
    '2026년 3월 1일에도 재직 중입니다. 발생 연차를 계산해 주세요.'
)


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
        # CR041 has no date. Use the approved evaluation's 2026 reference year in the web subprocess.
        web_command=[
            sys.executable,
            '-c',
            'from functools import partial\n'
            'from lawbot import service, web\n'
            'service.route_calculation = partial(service.route_calculation, reference_year=2026)\n'
            'web.main()',
        ],
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
    read_body = e2e.capture_response_body(page, '/api/chat')
    with page.expect_response(f'{stack.url}/api/chat', timeout=e2e.ANSWER_TIMEOUT) as response:
        e2e.ask(page, question)
    assert response.value.ok
    events = list(dict.fromkeys(re.findall(r'^event: (\S+)', read_body(), re.MULTILINE)))
    assert 'done' in events, events
    e2e.wait_done(page)
    return events


@pytest.mark.parametrize('width', [390, 1280])
def test_calculation_and_guidance_cards(calculation_stack, ui, width):
    ui.set_viewport_size({'width': width, 'height': 844})
    events = ask_with_events(ui, calculation_stack, MEDICAL)
    relevant = ['calculation', 'answer', 'calculation_check', 'done']
    assert [event for event in events if event in relevant] == ['calculation', 'calculation_check', 'done'], events
    result = ui.get_by_role('region', name='계산 결과')
    expect(result.get_by_text('225,000원', exact=True).first).to_be_visible()
    expect(result.get_by_text(re.compile(r'미입력\(0\) \d+건'))).to_be_visible()
    expect(e2e.answer(ui).get_by_role('status')).to_contain_text('계산 카드를 확인해 주세요')
    expect(e2e.answer(ui)).not_to_contain_text('의료비 세액공제액은 1원입니다.')
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

    events = ask_with_events(ui, calculation_stack, MIDYEAR)
    assert [event for event in events if event in relevant] == ['calculation', 'calculation_check', 'done'], events
    midyear = ui.get_by_role('region', name='계산 결과').last
    expect(midyear.get_by_text('146,750원', exact=True).first).to_be_visible()
    midyear.get_by_text('입력과 계산 단계', exact=True).click()
    steps = midyear.get_by_role('table', name='단계별 계산')
    expect(steps.get_by_role('row', name=re.compile('A 보험료 공제 적용 · 결정세액'))).to_contain_text('261,900원')
    expect(steps.get_by_role('row', name=re.compile('B 표준세액공제 적용 · 결정세액'))).to_contain_text('146,750원')
    expect(steps.get_by_role('row', name=re.compile('^선택 '))).to_contain_text('B (세액이 적은 쪽)')
    expect(midyear.get_by_role('alert')).to_have_count(0)

    events = ask_with_events(ui, calculation_stack, LOOKUP)
    assert [event for event in events if event in relevant] == ['answer', 'done'], events
    expect(e2e.answer(ui)).to_contain_text('가짜 모델 답변입니다.')
    expect(ui.get_by_role('region', name='계산 안내')).to_have_count(2)
    expect(ui.get_by_role('region', name='계산 결과')).to_have_count(2)

    rows = e2e.request_log(calculation_stack.request_log)[-5:]
    assert len(rows) == 5
    for row in rows:
        assert row['cache_reason'] == 'calculation_question' and row['response_source'] == 'rag'
        assert row['cache_eligible'] is False and row['exact_lookup_ms'] is None
        assert row['call_counts']['router'] == row['call_counts']['llm'] == 1
    assert rows[-2]['calculation_check'] == 'match'
    assert rows[-1]['router_reason'] == 'lookup_question'


@pytest.mark.parametrize('width', [390, 1280])
def test_labor_calculation_cards(calculation_stack, ui, width):
    ui.set_viewport_size({'width': width, 'height': 844})
    relevant = ['calculation', 'answer', 'calculation_check', 'done']
    events = ask_with_events(ui, calculation_stack, SEVERANCE)
    assert [event for event in events if event in relevant] == ['calculation', 'calculation_check', 'done'], events
    severance = ui.get_by_role('region', name='계산 결과').last
    summaries = severance.locator('dl > div')
    average = summaries.filter(has_text='평균임금 기준 퇴직금 추정')
    floor = summaries.filter(has_text='통상임금 하한 적용 퇴직금 추정')
    expect(average.get_by_text('9,000,000원', exact=True)).to_be_visible()
    expect(average.get_by_text('최근 3개월 월급이 일정하고 평균임금을 적용한 경우', exact=True)).to_be_visible()
    expect(floor.get_by_text('10,334,928원', exact=True)).to_be_visible()
    expect(floor.get_by_text('월급 전액이 통상임금이고 월 209시간·1일 8시간인 경우', exact=True)).to_be_visible()
    expect(severance.get_by_text(re.compile('계속근로 1년 이상·4주 평균 주 소정근로시간 15시간 이상'))).to_be_visible()
    answer = e2e.answer(ui)
    expect(answer).to_contain_text('계속근로 1년 이상이고 4주 평균 주 소정근로시간이 15시간 이상이라는 전제')
    expect(answer).to_contain_text('최근 3개월 월급이 일정하고 평균임금을 적용하면 9,000,000원')
    expect(answer).to_contain_text('월급 전액을 통상임금으로 보고 월 209시간과 1일 8시간을 가정하면 10,334,928원')
    expect(severance.get_by_text(re.compile(r'미입력\(0\) \d+건'))).to_be_visible()
    severance.get_by_text('입력과 계산 단계', exact=True).click()
    inputs = severance.get_by_role('table', name='계산 입력')
    expect(inputs.get_by_text('질문: 300만원', exact=True)).to_be_visible()
    expect(inputs.get_by_text('질문: 3년', exact=True)).to_be_visible()
    steps = severance.get_by_role('table', name='단계별 계산')
    expect(steps.get_by_text('근로자퇴직급여 보장법 제8조', exact=True).first).to_be_visible()
    expect(severance.get_by_role('alert')).to_have_count(0)
    assert ui.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    assert steps.evaluate('table => table.scrollWidth <= table.clientWidth + 1')

    events = ask_with_events(ui, calculation_stack, ANNUAL)
    assert [event for event in events if event in relevant] == ['calculation', 'calculation_check', 'done'], events
    annual = ui.get_by_role('region', name='계산 결과').last
    expect(annual.get_by_text('연차유급휴가 일수', exact=True).first).to_be_visible()
    expect(annual.get_by_text('15일', exact=True).first).to_be_visible()
    expect(annual.get_by_text('15원', exact=True)).to_have_count(0)
    expect(annual.get_by_role('alert')).to_have_count(0)

    events = ask_with_events(ui, calculation_stack, ANNUAL_MISSING)
    assert [event for event in events if event in relevant] == ['calculation', 'answer', 'done'], events
    guidance = ui.get_by_role('region', name='계산 안내').last
    expect(guidance.get_by_text(re.compile('상시 근로자수'))).to_be_visible()
    expect(guidance.get_by_text('근로기준법 제60조', exact=True)).to_be_visible()
    expect(guidance.get_by_role('table')).to_have_count(0)

    rows = e2e.request_log(calculation_stack.request_log)[-3:]
    assert [row['calculator'] for row in rows] == ['severance_pay', 'annual_leave_days', 'annual_leave_days']
    assert [row['calculation_check'] for row in rows] == ['match', 'match', 'not_run']
    assert rows[-1]['router_reason'] == 'missing_required'
    for row in rows:
        assert row['cache_reason'] == 'calculation_question' and row['response_source'] == 'rag'
        assert row['cache_eligible'] is False and row['exact_lookup_ms'] is None
        assert row['call_counts']['router'] == row['call_counts']['llm'] == 1


@pytest.mark.parametrize('width', [390, 1280])
def test_invalid_labor_input_keeps_guidance_and_no_calculation(calculation_stack, ui, width):
    ui.set_viewport_size({'width': width, 'height': 844})
    events = ask_with_events(ui, calculation_stack, ANNUAL_INVALID)
    relevant = ['calculation', 'answer', 'calculation_check', 'done']
    assert [event for event in events if event in relevant] == ['calculation', 'answer', 'done']
    guidance = ui.get_by_role('region', name='계산 안내')
    expect(guidance.get_by_text('입력한 수량과 단위를 확인해 다시 알려 주세요.', exact=True)).to_be_visible()
    expect(guidance.get_by_text('근로기준법 제60조', exact=True)).to_be_visible()
    expect(guidance.get_by_role('table')).to_have_count(0)
    expect(ui.get_by_role('region', name='계산 결과')).to_have_count(0)
    # The fake answer is emitted only when the request forbids direct calculation.
    expect(e2e.answer(ui)).to_contain_text('계산에 필요한 자료와 근거 조문을 확인해 주세요.')
    assert ui.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    row = e2e.request_log(calculation_stack.request_log)[-1]
    assert row['calculator'] == 'annual_leave_days'
    assert row['calculation_status'] == 'guidance'
    assert row['router_reason'] == 'unresolved_input'
    assert row['calculation_check'] == 'not_run'
    assert row['cache_reason'] == 'calculation_question' and row['response_source'] == 'rag'
    assert row['cache_eligible'] is False and row['exact_lookup_ms'] is None
    assert row['call_counts']['router'] == row['call_counts']['llm'] == 1
