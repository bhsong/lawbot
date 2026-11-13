"""Read the approved news snapshot and approved calendar promulgations."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from law_calendar.db import read_database
from lawbot_common.erp_news import HOMETAX_HOME, Agency, NewsItem, NewsSnapshot, latest, official_url

KST = timezone(timedelta(hours=9))
HOME = {
    'lawgo': 'https://www.law.go.kr/',
    'moel': 'https://www.moel.go.kr/',
    'mofe': 'https://mofe.go.kr/',
    'nts': 'https://www.nts.go.kr/',
    'hometax': HOMETAX_HOME,
}
LIMIT = 1_048_576


def law_news(database: Path, names: list[str]) -> tuple[list[NewsItem], str | None]:
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
            (datetime.now(KST).date().isoformat(), json.dumps(names)),
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


def response(database: Path, file: Path | None, agency: Agency, limit: int) -> dict:
    result = {'agency': agency, 'officialHome': HOME[agency]}
    if agency == 'hometax':
        return result | {'state': 'link_only', 'navigation': '전체메뉴 → 기타 → 고객센터 → 자료실'}
    if file is None:
        return result | {'state': 'missing'}
    try:
        with file.open('rb') as stream:
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            return result | {'state': 'invalid'}
        snapshot = NewsSnapshot.model_validate_json(data)
    except FileNotFoundError:
        return result | {'state': 'missing'}
    except OSError, ValidationError:
        return result | {'state': 'invalid'}
    entry = snapshot.agencies[agency]
    now = datetime.now(KST)
    boards = [
        {
            'name': board.name,
            'status': 'delayed'
            if board.status == 'ok' and board.staleAfter and board.staleAfter < now
            else board.status,
            'lastSuccessAt': board.lastSuccessAt.astimezone(KST).isoformat() if board.lastSuccessAt else None,
        }
        for board in entry.boards
    ]
    items = entry.items
    if agency == 'lawgo' and snapshot.lawNames:
        try:
            items, stamp = law_news(database, snapshot.lawNames)
        except sqlite3.Error, ValueError:
            return result | {'state': 'unavailable'}
        if boards and stamp:
            try:
                collected = datetime.fromisoformat(stamp)
                if collected.utcoffset() is not None:
                    boards[0].update(
                        status='delayed' if now - collected > timedelta(hours=48) else 'ok',
                        lastSuccessAt=collected.astimezone(KST).isoformat(),
                    )
            except ValueError:
                pass
    return result | {
        'state': 'ok',
        'generatedAt': snapshot.generatedAt.astimezone(KST).isoformat(),
        'boards': boards,
        'items': latest(items)[:limit],
    }
