from datetime import date

import pytest

from source_hub.tax_schedule import parse_year


def schedule(year, *rows):
    return (
        f'<div id="taxSchdulList"><a href="/nts/ad/taxSchdul/selectCalender.do?mi=135747&amp;taxYear={year}&amp;taxMonth=">달력형</a>'
        '<table><tbody>'
        + ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
        + '</tbody></table></div>'
    ).encode()


def test_tax_schedule_preserves_deadline_and_target_period():
    data = schedule(
        2026,
        ('03', '10', '원천세 신고 납부기한', '2025.연말정산분'),
        ('03', '10', '근로소득 지급명세서 제출', '2025년 귀속'),
        ('03', '31', '법인세 신고', '2025 사업연도'),
    )
    rows = parse_year(data, 2026)
    assert [(row.title, row.deadline_on, row.target_period) for row in rows] == [
        ('원천세 신고 납부기한', '2026-03-10', '2025.연말정산분'),
        ('근로소득 지급명세서 제출', '2026-03-10', '2025년 귀속'),
    ]
    assert all(row.published_on is None for row in rows)
    assert all(str(date(2026, 3, 10)) == row.deadline_on for row in rows)


def test_wrong_year_and_malformed_dates_are_rejected():
    with pytest.raises(ValueError):
        parse_year(schedule(2026, ('03', '10', '연말정산', '')), 2027)
    with pytest.raises(ValueError):
        parse_year(schedule(2026, ('02', '30', '연말정산', '')), 2026)
