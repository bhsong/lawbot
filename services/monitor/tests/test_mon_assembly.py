"""Assembly API and persisted transitions, using synthetic official responses."""

import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from law_monitor.assembly import ACTIVE, BILLS, CLOSED, AssemblyClient, AssemblyError
from law_monitor.collectors.assembly import AssemblyCollector
from mon_helpers import make_config, make_monitor
from test_mon_summary import FakeLLM

KEY = 'test-secret-assembly-key'
NOTICE_ID = 'PRC_NOTICE'
BILL_ID = 'PRC_BILL'
NOTICE_URL = f'https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId={NOTICE_ID}'


def bill(**fields):
    return {
        'BILL_ID': BILL_ID,
        'BILL_NO': '2200012',
        'BILL_NAME': '근로 기준법 일부개정법률안',
        'AGE': '22',
        'PROPOSE_DT': '2026-10-05',
        'PASS_GUBUN': '계류의안',
        'LINK_URL': f'https://likms.assembly.go.kr/bill/billDetail.do?billId={BILL_ID}',
        **fields,
    }


def notice(*, closed=False, **fields):
    return bill(
        **{
            'LINK_URL': NOTICE_URL.replace('Ongoing', 'Done') if closed else NOTICE_URL,
            'NOTI_ED_DT': '2026-11-05',
            **fields,
        }
    )


class Feed:
    def __init__(self):
        self.rows = {BILLS: [], ACTIVE: [], CLOSED: []}
        self.calls = []
        self.failure = None
        self.html = (
            '<tr><th>의안번호</th><th>입법예고기간</th></tr><tr><td>2200012</td><td>2026-10-26~2026-11-05</td></tr>'
        )

    def fetch(self, url, timeout):
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        code = parsed.path.rsplit('/', 1)[-1]
        self.calls.append((code, params))
        if self.failure == code:
            raise URLError(url)
        if parsed.hostname == 'pal.assembly.go.kr':
            assert 'KEY' not in params
            return 200, self.html.encode()
        assert params['KEY'] == [KEY]
        page, size = int(params['pIndex'][0]), int(params['pSize'][0])
        rows = self.rows[code]
        data = (
            {
                code: [
                    {'head': [{'list_total_count': len(rows)}, {'RESULT': {'CODE': 'INFO-000'}}]},
                    {'row': rows[(page - 1) * size : page * size]},
                ]
            }
            if rows
            else {'RESULT': {'CODE': 'INFO-200'}}
        )
        return 200, json.dumps(data).encode()

    def client(self, **options):
        return AssemblyClient(KEY, fetch=self.fetch, sleep=lambda _: None, **options)


def test_pages_empty_results_and_redacted_retry():
    feed = Feed()
    feed.rows[BILLS] = [bill(), bill(BILL_ID='another')]
    client = feed.client(page_size=1)
    assert len(client.pages(BILLS, 22)) == 2
    assert client.pages(ACTIVE, 22) == []
    feed.failure = CLOSED
    with pytest.raises(AssemblyError, match='URLError') as caught:
        client.pages(CLOSED, 22)
    assert KEY not in str(caught.value)
    assert [code for code, _ in feed.calls].count(CLOSED) == 3


def test_notice_bootstrap_close_and_no_date_invention(tmp_path):
    feed = Feed()
    feed.rows[ACTIVE] = [notice(), notice(BILL_NAME='다른 법률안', LINK_URL=NOTICE_URL.replace(NOTICE_ID, 'other'))]
    collector = AssemblyCollector('assembly_notice', feed.client())
    monitor = make_monitor(tmp_path, make_config(), collectors=[collector])
    assert monitor.bootstrap(only=collector.name).events == []
    [observed] = monitor.collect().events
    assert observed['change_type'] == 'assembly_notice_observed'
    assert observed['dates'] == {'notice_start': '2026-10-26', 'notice_end': '2026-11-05'}
    assert observed['target']['item_id'] == NOTICE_ID
    assert monitor.collect().events == []
    feed.rows[ACTIVE] = []
    assert monitor.collect().events == []
    feed.rows[CLOSED] = [notice(closed=True)]
    [closed] = monitor.collect().events
    assert closed['change_type'] == 'assembly_notice_closed'
    assert closed['target']['item_id'] == NOTICE_ID
    assert monitor.collect().events == []


def test_bill_a_to_b_to_a_and_source_failure_isolated(tmp_path):
    feed = Feed()
    feed.rows[BILLS] = [bill()]
    bills, notices = [AssemblyCollector(source, feed.client()) for source in ('assembly_bill', 'assembly_notice')]
    monitor = make_monitor(tmp_path, make_config(), collectors=[bills, notices])
    assert monitor.bootstrap(only=bills.name).ok == [bills.name]
    feed.rows[BILLS] = [bill(PROC_DT='2026-11-05', PROC_RESULT_CD='원안가결')]
    first = monitor.collect()
    assert first.ok == [bills.name] and notices.name in first.failed
    [changed] = first.events
    assert changed['dates']['bill_decided'] == '2026-11-05'
    feed.rows[BILLS] = [bill()]
    [reverted] = monitor.collect(only=bills.name).events
    assert reverted['event_id'] != changed['event_id']
    assert 'bill_decided' not in reverted['dates']
    assert monitor.collect(only=bills.name).events == []


def test_force_bootstrap_refreshes_existing_bill_at_same_revision(tmp_path):
    feed = Feed()
    feed.rows[BILLS] = [bill()]
    collector = AssemblyCollector('assembly_bill', feed.client())
    monitor = make_monitor(tmp_path, make_config(), collectors=[collector])
    monitor.bootstrap(only=collector.name)
    feed.rows[BILLS] = [bill(COMMITTEE_DT='2026-11-01')]
    [first] = monitor.collect().events

    feed.rows[BILLS] = [bill(PROC_DT='2026-11-05', PROC_RESULT_CD='원안가결')]
    result = monitor.bootstrap(only=collector.name, force=True)
    assert result.ok == [collector.name] and result.events == []
    assert monitor.collect().events == []

    feed.rows[BILLS] = [bill(PROC_DT='2026-11-06', PROC_RESULT_CD='수정가결')]
    [changed] = monitor.collect().events
    assert changed['old']['version_key'] == first['new']['version_key'] == '1'
    assert changed['new']['version_key'] == '2'
    previous = json.loads((monitor.paths.home / changed['old']['file']).read_text())
    assert previous['dates']['bill_decided'] == '2026-11-05'


@pytest.mark.parametrize('closed', [False, True])
def test_force_bootstrap_refreshes_existing_notice_period(tmp_path, closed):
    feed = Feed()
    feed.rows[ACTIVE] = [notice()]
    collector = AssemblyCollector('assembly_notice', feed.client())
    monitor = make_monitor(tmp_path, make_config(), collectors=[collector])
    monitor.bootstrap(only=collector.name)
    [first] = monitor.collect().events

    feed.html = feed.html.replace('2026-11-05', '2026-11-06')
    feed.rows[ACTIVE] = [] if closed else [notice(NOTI_ED_DT='2026-11-06')]
    feed.rows[CLOSED] = [notice(closed=True, NOTI_ED_DT='2026-11-06')] if closed else []
    result = monitor.bootstrap(only=collector.name, force=True)
    assert result.ok == [collector.name] and result.events == []
    assert monitor.collect().events == []
    item = monitor.state.items(collector.name)[NOTICE_ID]
    assert item.version_key == first['new']['version_key'] == '1'
    snapshot = json.loads((monitor.paths.home / item.file_path).read_text())
    assert snapshot['dates'] == {'notice_start': '2026-10-26', 'notice_end': '2026-11-06'}
    assert snapshot['context']['endpoint'] == (CLOSED if closed else ACTIVE)


def test_conflicting_pages_leave_notice_state_and_key_out_of_logs(tmp_path, caplog):
    feed = Feed()
    feed.rows[ACTIVE] = [notice()]
    collector = AssemblyCollector('assembly_notice', feed.client(page_size=1))
    monitor = make_monitor(tmp_path, make_config(), collectors=[collector])
    monitor.bootstrap()
    monitor.collect()
    before = monitor.state.items(collector.name)
    feed.rows[ACTIVE] *= 2
    assert collector.name in monitor.collect().failed
    assert monitor.state.items(collector.name) == before
    feed.rows[ACTIVE] = []
    feed.rows[CLOSED] = [notice(closed=True)]
    feed.failure = CLOSED
    for _ in range(2):
        result = monitor.collect()
        assert KEY not in str(result.failed)
    [warning] = result.events
    assert warning['change_type'] == 'collector_failing'
    assert KEY not in caplog.text
    assert all(KEY not in path.read_text() for path in monitor.paths.events.rglob('*.json'))
    assert monitor.state.items(collector.name) == before
    feed.failure = None
    assert monitor.collect().events[0]['change_type'] == 'assembly_notice_closed'


def test_unverified_notice_start_is_omitted_and_retried_after_closure(tmp_path):
    feed = Feed()
    valid = feed.html
    feed.html = valid.replace('2026-11-05', '2026-11-04')
    collector = AssemblyCollector('assembly_notice', feed.client())
    monitor = make_monitor(tmp_path, make_config(), collectors=[collector])
    monitor.bootstrap()
    feed.rows[CLOSED] = [notice(closed=True)]
    [closed] = monitor.collect().events
    assert closed['dates'] == {'notice_end': '2026-11-05'}
    feed.html = valid
    [amended] = monitor.collect().events
    assert amended['dates']['notice_start'] == '2026-10-26'
    assert monitor.collect().events == []


def test_assembly_summary_uses_source_fields_without_model_calls(tmp_path):
    feed = Feed()
    feed.rows[ACTIVE] = [notice()]
    llm = FakeLLM()
    collector = AssemblyCollector('assembly_notice', feed.client())
    monitor = make_monitor(tmp_path, make_config(), collectors=[collector], llm=llm.client())
    monitor.bootstrap()
    monitor.collect()
    summary = monitor.summarize()
    assert summary.calls == summary.pending == 0
    assert llm.bodies == []
    assert '예고 시작 2026-10-26' in summary.path.read_text()
