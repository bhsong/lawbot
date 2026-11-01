"""PR 2: 법제처 API 보강 (개정문·제개정이유, 별표·서식 텍스트 비교). 응답은 스파이크 견본을 줄인 것 (fixtures/lawgo)."""

import json
import os

import pytest

from law_monitor import attachments, cli, secrets
from law_monitor.app import MissingSecret
from law_monitor.lawgo import LawGoClient, LawGoError
from law_monitor.utils.http import HttpClient
from mon_helpers import (
    PDF,
    TEST_OC,
    FakeLawGo,
    SourceRepo,
    admrule_md,
    fixture,
    law_md,
    make_config,
    make_monitor,
    modified,
    units_of,
)

RULE = 'kr/소득세법/시행규칙.md'
OLD_MST, NEW_MST = '284987', '286379'  # 소득세법 시행규칙 2026-03-20판, 2026-05-22판
FORMS = [{'law': '소득세법 시행규칙', 'kinds': ['별표', '서식']}]
ETS = '전자(세금)계산서 겸용서식 사용 사업자의 전산매체 제출 절차에 관한 고시'
ETS_PATH = f'재정경제부/국세청/고시/{ETS}/본문.md'
NEW_SERIAL, OLD_SERIAL = '2100000232308', '2100000099999'


def rule_md(mst: str, promulgated: str, body: str) -> str:
    return law_md(
        '소득세법 시행규칙', {'제100조 (서식)': body}, promulgated=promulgated, effective=promulgated, mst=mst
    )


def laws_server(**kw) -> FakeLawGo:
    return FakeLawGo({OLD_MST: fixture('law_284987'), NEW_MST: fixture('law_286379')}, **kw)


@pytest.fixture
def legalize(tmp_path):
    repo = SourceRepo(tmp_path / 'src-legalize')
    repo.commit({RULE: rule_md(OLD_MST, '2026-03-20', '서식 v1')}, '재정경제부령: 소득세법 시행규칙 (일부개정)')
    return repo


def config_for(legalize, admrule=None, **kw):
    return make_config(legalize, admrule, laws=[{'name': '소득세법', 'category': 1}], forms=FORMS, **kw)


def amend(legalize):
    return legalize.commit(
        {RULE: rule_md(NEW_MST, '2026-05-22', '서식 v2')},
        '재정경제부령: 소득세법 시행규칙 (일부개정)',
        date='2026-05-22T12:00:00+09:00',
    )


# ---- 순수 함수 ----


def test_parse_effective_dates_glued_together():
    # 날짜 사이에 구분자가 없다: '…서식100의2' 바로 뒤에 '20260701:'
    got = attachments.parse_effective('20260522:서식21,서식23,서식100의220260701:서식24,서식24의5')
    assert got == {
        '서식21': ['2026-05-22'],
        '서식23': ['2026-05-22'],
        '서식100의2': ['2026-05-22'],
        '서식24': ['2026-07-01'],
        '서식24의5': ['2026-07-01'],
    }
    assert attachments.parse_effective('') == {}
    assert attachments.parse_effective('20260320:서식24,별표220260701:서식24')['서식24'] == ['2026-03-20', '2026-07-01']


def test_label_and_text():
    unit = units_of(fixture('law_286379'))[1]
    assert attachments.label(unit) == '서식21'
    assert attachments.label({'별표구분': '서식', '별표번호': '0100', '별표가지번호': '02'}) == '서식100의2'
    assert attachments.text(unit).startswith('[별지 제21호서식] <개정 2026. 5. 22.>')
    assert not attachments.text(unit).splitlines()[0].endswith(' ')  # 줄 끝 공백 제거


def test_compare_uses_text_not_links():
    old, new = fixture('law_284987'), fixture('law_286379')
    # 견본 4개 모두 링크(flSeq)는 판마다 다르다. 텍스트가 다른 것은 서식21·서식100의2뿐 (스파이크 발견 2)
    assert all(a['별표서식PDF파일링크'] != b['별표서식PDF파일링크'] for a, b in zip(units_of(old), units_of(new)))
    got = [(c.kind, attachments.label(c.new or c.old)) for c in attachments.compare(old, new, kinds=('별표', '서식'))]
    assert got == [('changed', '서식21'), ('changed', '서식100의2')]
    assert attachments.compare(old, new, kinds=('별표',)) == []

    removed = modified(old, lambda r: r['별표']['별표단위'].append({**r['별표']['별표단위'][0], '별표번호': '0009'}))
    kinds = [(c.kind, attachments.label(c.new or c.old)) for c in attachments.compare(removed, new, kinds=None)]
    assert ('removed', '별표9') in kinds


# ---- 법령 보강 ----


def test_law_event_gets_reason_and_form_events(tmp_path, legalize):
    server = laws_server()
    mon = make_monitor(tmp_path, config_for(legalize), lawgo=server.client())
    mon.bootstrap(only='legalize_law')
    assert server.requests == []  # bootstrap은 법제처를 부르지 않음
    amend(legalize)
    summary = mon.collect(only='legalize_law')

    law = [e for e in summary.events if e['source'] == 'legalize_law']
    forms = sorted((e for e in summary.events if e['source'] == 'lawgo_forms'), key=lambda e: e['target']['item_id'])
    assert len(law) == 1 and len(forms) == 2
    ctx = law[0]['context']
    assert '퇴직소득' in ctx['amendment_reason'] and ctx['amendment_text'].startswith('⊙재정경제부령 제33호')
    assert ctx['lawgo']['mst_old'] == OLD_MST and ctx['lawgo']['mst_new'] == NEW_MST
    assert ctx['lawgo']['attachment_effective'].startswith('20260522:서식21')

    f21, f100 = forms
    assert f21['change_type'] == 'attachment_changed' and f21['category'] == 2
    assert f21['target'] == {
        'law_name': '소득세법 시행규칙',
        'item_name': units_of(fixture('law_286379'))[1]['별표제목'],
        'item_id': '서식 0021-00',
        'kind': '서식',
    }
    assert f21['dates'] == {'promulgation': '2026-05-22', 'effective': '2026-05-22', 'effective_dates': ['2026-05-22']}
    assert f100['target']['item_id'] == '서식 0100-02'
    assert f21['context']['parent_event_id'] == law[0]['event_id']
    assert f21['context']['amendment_reason'] == ctx['amendment_reason']

    home = mon.paths.home
    diff = (home / f21['diff']['path']).read_text(encoding='utf-8')
    assert '-[별지 제21호서식] <개정 2026. 3. 20.>' in diff and '+[별지 제21호서식] <개정 2026. 5. 22.>' in diff
    assert (home / f21['old']['text']).read_text(encoding='utf-8').startswith('[별지 제21호서식] <개정 2026. 3. 20.>')
    assert (home / f21['new']['file']).read_bytes().startswith(PDF) and f21['new']['file'].endswith('.pdf')
    assert f21['new']['hwp'].endswith('.hwp') and f21['old'].get('file') is None  # 파일은 신판만

    # 본문 2번(신·구판), 다운로드는 바뀐 서식 2개 × (PDF + HWP) = 4번. 링크만 바뀐 2개는 받지 않음
    assert server.count(path='lawService.do') == 2 and server.count(path='flDownload.do') == 4
    assert mon.collect(only='legalize_law').events == []


def test_law_not_in_forms_gets_reason_only(tmp_path, legalize):
    server = laws_server()
    mon = make_monitor(
        tmp_path, make_config(legalize, laws=[{'name': '소득세법', 'category': 1}]), lawgo=server.client()
    )
    mon.bootstrap(only='legalize_law')
    amend(legalize)
    events = mon.collect(only='legalize_law').events
    assert [e['source'] for e in events] == ['legalize_law'] and events[0]['context']['amendment_reason']
    assert server.count(path='lawService.do', MST=NEW_MST) == 1 and server.count(path='lawService.do', MST=OLD_MST) == 0


def test_body_cache_across_commits(tmp_path, legalize):
    server = laws_server()
    mon = make_monitor(tmp_path, config_for(legalize), lawgo=server.client())
    mon.bootstrap(only='legalize_law')
    amend(legalize)
    legalize.commit({RULE: rule_md(OLD_MST, '2026-06-01', '서식 v3')}, '되돌림')  # 286379 → 284987
    events = mon.collect(only='legalize_law').events
    assert len([e for e in events if e['source'] == 'legalize_law']) == 2
    assert server.count(path='lawService.do') == 2  # MST마다 한 번 (가운데 판을 두 번 받지 않음)


def test_retry_then_success_and_failure_keeps_cursor(tmp_path, legalize):
    flaky = laws_server(fail=2)
    mon = make_monitor(tmp_path, config_for(legalize), lawgo=flaky.client())
    mon.bootstrap(only='legalize_law')
    amend(legalize)
    assert len(mon.collect(only='legalize_law').events) == 3  # 503 두 번 뒤 성공

    down = laws_server(fail=10**6)
    mon2 = make_monitor(tmp_path / 'b', config_for(legalize), lawgo=down.client())
    legalize_cursor_before = None
    mon2.bootstrap(only='legalize_law')
    legalize_cursor_before = mon2.state.cursor('legalize-kr')
    legalize.commit({RULE: rule_md(OLD_MST, '2026-06-01', '서식 v3')}, '개정')
    summary = mon2.collect(only='legalize_law')
    assert list(summary.failed) == ['legalize_law'] and summary.events == []
    assert mon2.state.cursor('legalize-kr') == legalize_cursor_before  # 원문 없는 이벤트는 저장하지 않음
    assert down.count(path='lawService.do') == 3  # 최대 3회 시도

    mon3 = make_monitor(tmp_path / 'b', config_for(legalize), lawgo=laws_server().client())
    assert len([e for e in mon3.collect(only='legalize_law').events if e['source'] == 'legalize_law']) == 1


def test_oc_never_leaks(tmp_path, legalize):
    down = laws_server(fail=10**6, status=500)
    mon = make_monitor(tmp_path, config_for(legalize), lawgo=down.client())
    mon.bootstrap(only='legalize_law')
    amend(legalize)
    summary = mon.collect(only='legalize_law')
    assert 'OC=***' in summary.failed['legalize_law'] and TEST_OC not in summary.failed['legalize_law']
    assert down.requests[0].url.params['OC'] == TEST_OC  # 실제 요청에는 들어감
    for root, _, files in os.walk(mon.paths.home):
        for name in files:
            if name.endswith(('.json', '.log', '.diff', '.txt', '.db')):
                assert TEST_OC.encode() not in open(os.path.join(root, name), 'rb').read(), name


def test_download_must_be_pdf_or_hwp(tmp_path, legalize):
    server = laws_server(html_downloads=True)
    mon = make_monitor(tmp_path, config_for(legalize), lawgo=server.client())
    mon.bootstrap(only='legalize_law')
    amend(legalize)
    summary = mon.collect(only='legalize_law')
    assert 'PDF' in summary.failed['legalize_law'] and summary.events == []


def test_client_rejects_unknown_response():
    server = FakeLawGo()
    with pytest.raises(LawGoError, match='응답'):
        server.client().law_body('1')
    assert isinstance(HttpClient(), HttpClient)


# ---- 행정규칙 보강 ----


def test_admrule_event_reason_and_attachment(tmp_path, legalize):
    new = fixture('admrul_2100000232308')

    def older(r):
        r['제개정이유'] = {'제개정이유내용': [['◇ 예전 이유']]}
        r['별표']['별표단위'][1]['별표내용'] = [['예전 파일제출 신청서']]

    server = FakeLawGo(admrules={NEW_SERIAL: new, OLD_SERIAL: modified(new, older)})
    admrule = SourceRepo(legalize.path.parent / 'src-admrule')
    admrule.commit({ETS_PATH: admrule_md('38212', ETS, '국세청', '절차 v1', serial=OLD_SERIAL)}, '고시: 이전')
    names = [{'agency': '국세청', 'pattern': r'^전자\(세금\)계산서 겸용서식', 'category': 5}]
    mon = make_monitor(tmp_path, config_for(legalize, admrule, names=names, keyword_rules=[]), lawgo=server.client())
    mon.bootstrap()
    admrule.commit(
        {ETS_PATH: admrule_md('38212', ETS, '국세청', '절차 v2', serial=NEW_SERIAL, number='2023-22')},
        f'고시: {ETS} (2023-22)',
    )
    events = mon.collect(only='legalize_admrule').events
    rule = next(e for e in events if e['source'] == 'legalize_admrule')
    ctx = rule['context']
    assert '표준인증' in ctx['amendment_reason'] and ctx['addenda'].startswith('부칙 <제2023-22호')
    assert [f['name'] for f in ctx['lawgo']['files']][-1] == '고시개정 이유서.pdf'
    att = [e for e in events if e['source'] == 'lawgo_admrul']
    assert [(e['change_type'], e['target']['item_id'], e['category']) for e in att] == [
        ('attachment_changed', '별지 0002-00', 5)
    ]


# ---- OC ----


def test_missing_oc_refuses_collect_not_bootstrap(tmp_path, legalize, monkeypatch, capsys):
    from law_monitor.app import Monitor
    from law_monitor.paths import Paths

    mon = Monitor(Paths(tmp_path / 'h'), config_for(legalize), lawgo=None)
    mon.bootstrap(only='legalize_law')
    with pytest.raises(MissingSecret, match='LAW_OC'):
        mon.collect(only='legalize_law')
    with pytest.raises(MissingSecret):
        mon.replay('legalize-kr', 'HEAD', 'HEAD')

    monkeypatch.delenv('LAW_OC', raising=False)
    monkeypatch.setattr(secrets, 'ENV_FILE', tmp_path / 'none.env')
    assert secrets.law_oc() is None
    cfg = tmp_path / 'targets.yaml'
    cfg.write_text(
        f'repos: {{legalize-kr: "{legalize.url}", admrule-kr: "file:///none"}}\n'
        'laws: [{name: 소득세법, category: 1}]\nadmrules: {names: [], keyword_rules: []}\n',
        encoding='utf-8',
    )
    assert cli.main(['--home', str(tmp_path / 'h'), '--config', str(cfg), 'collect', '--only', 'legalize_law']) == 2
    assert 'LAW_OC' in capsys.readouterr().err


def test_law_oc_from_env_or_file(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('# 주석\nLAW_OC=fromfile\nDATA_GO_KR_KEY=\n', encoding='utf-8')
    monkeypatch.setattr(secrets, 'ENV_FILE', env)
    monkeypatch.delenv('LAW_OC', raising=False)
    assert secrets.law_oc() == 'fromfile'
    monkeypatch.setenv('LAW_OC', 'fromenv')
    assert secrets.law_oc() == 'fromenv'


# ---- 실제 접속 (기본 실행 제외: uv run pytest -m network services/monitor) ----


@pytest.mark.network
def test_real_law_search():
    oc = secrets.law_oc()
    if not oc:
        pytest.skip('LAW_OC 없음')
    client = LawGoClient(oc)
    body = client.law_body(NEW_MST)
    assert '퇴직소득' in json.dumps(body['제개정이유'], ensure_ascii=False)
