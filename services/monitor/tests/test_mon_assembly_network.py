"""Read-only contract checks against the three official Assembly endpoints."""

import time

import pytest

from law_monitor import secrets
from law_monitor.assembly import ACTIVE, BILLS, CLOSED, AssemblyClient, official_identity
from law_monitor.config import load


@pytest.mark.network
def test_assembly_contract():
    key = secrets.get('LAW_ASSEMBLY_KEY')
    if not key:
        pytest.fail('LAW_ASSEMBLY_KEY is required for the explicit Assembly API test')
    age = load().assembly_age
    client = AssemblyClient(key, page_size=2, attempts=1)
    for code in (BILLS, ACTIVE, CLOSED):
        started = time.monotonic()
        total, first = client.page(code, age, 1)
        second_total, second = client.page(code, age, 2)
        assert total == second_total and len(first) == len(second) == 2
        assert not {r['BILL_ID'] for r in first} & {r['BILL_ID'] for r in second}
        row = first[0]
        assert {'BILL_NAME', 'BILL_NO', 'BILL_ID', 'AGE', 'CURR_COMMITTEE', 'LINK_URL'} <= row.keys()
        is_notice = code != BILLS
        official_identity(row['LINK_URL'], notice=is_notice)
        if is_notice:
            assert client.notice_start(row['LINK_URL'], row['BILL_NO'], row['NOTI_ED_DT'])
        else:
            assert {'PROPOSE_DT', 'PROC_DT', 'PASS_GUBUN', 'PROC_RESULT_CD'} <= row.keys()
        print(
            f'{code}: pages=2 sampled=4 total={total} detail={int(is_notice)} elapsed={time.monotonic() - started:.2f}s'
        )
