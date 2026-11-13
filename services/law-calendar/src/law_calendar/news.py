"""Read the approved news snapshot and approved calendar promulgations."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from law_calendar.db import read_database
from lawbot_common.erp_news import (
    HOMETAX_HOME,
    SNAPSHOT_MAX_BYTES,
    Agency,
    NewsItem,
    NewsSnapshot,
    latest,
    official_url,
)

KST = timezone(timedelta(hours=9))
HOME = {
    'lawgo': 'https://www.law.go.kr/',
    'moel': 'https://www.moel.go.kr/',
    'mofe': 'https://mofe.go.kr/',
    'nts': 'https://www.nts.go.kr/',
    'hometax': HOMETAX_HOME,
}


def law_news(database: Path, names: list[str], as_of: date | None = None) -> tuple[list[NewsItem], str | None]:
    with read_database(database) as conn:
        conn.execute('BEGIN')
        stamp = conn.execute('SELECT collected_at FROM collection_status WHERE id=1').fetchone()
        rows = conn.execute(
            """SELECT e.id,l.name,e.promulgated_on,e.source_url,
                 (SELECT CASE WHEN COUNT(*)=COUNT(effective_on)
                         AND MIN(effective_on)=MAX(effective_on)
                         THEN MIN(effective_on) END
                  FROM public_changes WHERE event_id=e.id
                    HAVING COUNT(*)=(SELECT COUNT(*) FROM article_changes WHERE event_id=e.id)) AS effective_on
               FROM change_events e JOIN laws l ON l.id=e.law_id
               WHERE e.kind='effective' AND e.promulgated_on <= ?
                 AND e.promulgated_on = date(e.promulgated_on)
                 AND l.name IN (SELECT value FROM json_each(?))
                 AND EXISTS (SELECT 1 FROM public_changes WHERE event_id=e.id)
               ORDER BY e.promulgated_on DESC, e.id DESC""",
            ((as_of or datetime.now(KST).date()).isoformat(), json.dumps(names)),
        ).fetchall()
    items = [
        NewsItem(
            id=row['id'],
            title=f'{row["name"]} 개정',
            board='추적 법령 공포',
            publishedOn=date.fromisoformat(row['promulgated_on']),
            officialUrl=row['source_url'],
            effectiveOn=date.fromisoformat(row['effective_on']) if row['effective_on'] else None,
        )
        for row in rows
        if official_url(row['source_url'], 'lawgo')
    ]
    return items, stamp[0] if stamp else None


def response(
    database: Path,
    file: Path | None,
    agency: Agency,
    limit: int,
    *,
    now: datetime | None = None,
    use_law_feed: bool = True,
) -> dict:
    result = {'agency': agency, 'officialHome': HOME[agency]}
    if agency == 'hometax':
        return result | {'state': 'link_only', 'navigation': '전체메뉴 → 기타 → 고객센터 → 자료실'}
    if file is None:
        return result | {'state': 'missing'}
    try:
        with file.open('rb') as stream:
            data = stream.read(SNAPSHOT_MAX_BYTES + 1)
        if len(data) > SNAPSHOT_MAX_BYTES:
            return result | {'state': 'invalid'}
        snapshot = NewsSnapshot.model_validate_json(data)
    except FileNotFoundError:
        return result | {'state': 'missing'}
    except OSError, ValidationError:
        return result | {'state': 'invalid'}
    return current_news(
        from_snapshot(database, snapshot, agency, limit, now=now, use_law_feed=use_law_feed), limit, now=now
    )


def current_news(entry: dict, limit: int, *, now: datetime | None = None) -> dict:
    """Derive request-time freshness without changing stored news or exposing its deadlines."""
    now = now or datetime.now(KST)
    boards = []
    for source in entry.get('boards', []):
        board = dict(source)
        cutoff = board.pop('staleAfter', None)
        if cutoff and board['status'] == 'ok' and datetime.fromisoformat(cutoff) < now:
            board['status'] = 'delayed'
        boards.append(board)
    return entry | {'boards': boards, 'items': entry.get('items', [])[:limit]}


def from_snapshot(
    database: Path,
    snapshot: NewsSnapshot,
    agency: Agency,
    limit: int,
    *,
    now: datetime | None = None,
    use_law_feed: bool = True,
) -> dict:
    result = {'agency': agency, 'officialHome': HOME[agency]}
    if agency == 'hometax':
        return result | {'state': 'link_only', 'navigation': '전체메뉴 → 기타 → 고객센터 → 자료실'}
    entry = snapshot.agencies[agency]
    now = now or datetime.now(KST)
    boards = [
        {
            'name': board.name,
            'status': board.status,
            'lastSuccessAt': board.lastSuccessAt.astimezone(KST).isoformat() if board.lastSuccessAt else None,
            'staleAfter': board.staleAfter.astimezone(KST).isoformat() if board.staleAfter else None,
        }
        for board in entry.boards
    ]
    items = entry.items
    if agency == 'lawgo' and snapshot.lawNames and use_law_feed:
        try:
            items, stamp = law_news(database, snapshot.lawNames, now.astimezone(KST).date())
        except sqlite3.Error, ValueError:
            return result | {'state': 'unavailable'}
        if boards and stamp:
            try:
                collected = datetime.fromisoformat(stamp)
                if collected.utcoffset() is not None:
                    boards[0].update(
                        status='ok',
                        lastSuccessAt=collected.astimezone(KST).isoformat(),
                        staleAfter=(collected.astimezone(KST) + timedelta(hours=48)).isoformat(),
                    )
            except ValueError:
                pass
    return result | {
        'state': 'ok',
        'generatedAt': snapshot.generatedAt.astimezone(KST).isoformat(),
        'boards': boards,
        'items': latest(items)[:limit],
    }
