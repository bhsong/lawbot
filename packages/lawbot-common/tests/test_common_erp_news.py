from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from lawbot_common.erp_news import AGENCIES, NewsItem, NewsSnapshot, latest

KST = timezone(timedelta(hours=9))


def item(board: str, number: int, published: str | None) -> NewsItem:
    return NewsItem(
        id=f'{board}-{number}',
        title=f'{board} {number}',
        board=board,
        publishedOn=date.fromisoformat(published) if published else None,
        officialUrl=f'https://www.moel.go.kr/view.do?board={board}&id={number}',
    )


def test_latest_keeps_ten_per_board_and_listing_order_for_ties():
    notices = [item('notice', number, f'2026-11-{number:02}') for number in range(1, 13)]
    rules = [item('rules', 1, None), item('rules', 2, '2026-10-01'), item('rules', 3, None), item('rules', 4, None)]
    ties = [item('press', 9, '2026-11-01'), item('press', 1, '2026-11-01')]

    result = latest([*notices, *rules, *ties])

    assert [entry.id for entry in result if entry.board == 'notice'] == [f'notice-{n}' for n in range(12, 2, -1)]
    assert [entry.id for entry in result if entry.board == 'rules'] == ['rules-2', 'rules-1', 'rules-3', 'rules-4']
    assert [entry.id for entry in result if entry.board == 'press'] == ['press-9', 'press-1']


def test_latest_limit_applies_per_board_and_drops_repeated_links():
    repeated = item('notice', 1, '2026-11-02').model_copy(update={'id': 'copy', 'publishedOn': date(2026, 11, 1)})
    items = [item('notice', n, f'2026-11-{n:02}') for n in range(1, 4)] + [repeated, item('rules', 1, None)]

    result = latest(items, 2)

    assert [entry.id for entry in result] == ['notice-3', 'notice-2', 'rules-1']


def snapshot(items: list[dict]) -> dict:
    agencies = {agency: {'boards': [], 'items': []} for agency in AGENCIES}
    agencies['moel']['items'] = items
    return {'schemaVersion': 1, 'generatedAt': datetime.now(KST).isoformat(), 'agencies': agencies, 'lawNames': []}


def test_snapshot_allows_ten_items_for_each_board_but_not_eleven():
    two_boards = [item(board, n, None).model_dump(mode='json') for board in ('notice', 'rules') for n in range(10)]
    assert len(NewsSnapshot.model_validate(snapshot(two_boards)).agencies['moel'].items) == 20

    eleven = [item('notice', n, None).model_dump(mode='json') for n in range(11)]
    with pytest.raises(ValidationError, match='10'):
        NewsSnapshot.model_validate(snapshot(eleven))


def test_same_link_is_shown_once_per_board_but_in_every_board():
    shared = item('guide', 1, None)
    other = shared.model_copy(update={'id': 'summary-1', 'board': 'summary'})
    twice = shared.model_copy(update={'id': 'guide-copy'})

    result = latest([shared, twice, other])

    assert [(entry.id, entry.board) for entry in result] == [('guide-1', 'guide'), ('summary-1', 'summary')]
