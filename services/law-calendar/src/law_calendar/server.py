"""Public read-only API. No collection, inference, chatbot authentication or writes."""

import argparse
import calendar
import difflib
import os
import re
import sqlite3
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from law_calendar.db import read_database
from law_calendar.models import CalendarMonth, CalendarStatus, ChangePage, PublicDetail

KST = timezone(timedelta(hours=9))


def month_bounds(value: str) -> tuple[str, str]:
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise HTTPException(422, 'month must be YYYY-MM')
    try:
        first = Date.fromisoformat(value + '-01')
    except ValueError as exc:
        raise HTTPException(422, 'Invalid month') from exc
    return first.isoformat(), first.replace(day=calendar.monthrange(first.year, first.month)[1]).isoformat()


def civil_date(value: str) -> str:
    try:
        parsed = Date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, 'Invalid KST date') from exc
    if parsed.isoformat() != value:
        raise HTTPException(422, 'date must be YYYY-MM-DD')
    return value


def change_data(row) -> dict:
    return {
        'id': row['id'],
        'eventId': row['event_id'],
        'kind': row['kind'],
        'date': row['event_on'],
        'lawName': row['law_name'],
        'article': row['article'],
        'summary': row['summary'],
        'status': row['status'],
        'before': row['before_text'],
        'after': row['after_text'],
        'comparable': bool(row['comparable']),
        'publishedOn': row['published_on'],
        'promulgatedOn': row['promulgated_on'],
        'effectiveOn': row['effective_on'],
        'commentDeadline': row['comment_deadline'],
        'sourceUrl': row['source_url'],
        'collectedAt': row['collected_at'],
        'verifiedAt': row['verified_at'],
        'reviewState': 'approved',
    }


def create_app(database: Path, frontend: Path | None = None) -> FastAPI:
    app = FastAPI(title='Law Calendar', version='0.2.0')

    @app.middleware('http')
    async def public_headers(request: Request, call_next):
        response = await call_next(request)
        # Revalidate every request so withdrawals cannot persist in browser caches.
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(sqlite3.Error)
    async def database_unavailable(request: Request, exc: sqlite3.Error):
        return JSONResponse({'detail': 'Calendar data is unavailable'}, status_code=503)

    @app.get('/api/law-calendar', response_model=CalendarMonth)
    def monthly(month: str):
        start, end = month_bounds(month)
        with read_database(database) as conn:
            rows = conn.execute(
                """SELECT event_on,
                COUNT(DISTINCT CASE WHEN kind='notice' THEN event_id END) AS notice_count,
                SUM(CASE WHEN kind='effective' THEN 1 ELSE 0 END) AS effective_count
                FROM public_changes WHERE event_on BETWEEN ? AND ? GROUP BY event_on ORDER BY event_on""",
                (start, end),
            ).fetchall()
        return {'month': month, 'timezone': 'Asia/Seoul', 'days': [dict(row) for row in rows]}

    @app.get('/api/law-events', response_model=ChangePage)
    def events(
        date: str | None = None,
        month: str | None = None,
        type: Literal['all', 'notice', 'effective'] = 'all',
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(100, ge=1, le=100),
    ):
        if (date is None) == (month is None):
            raise HTTPException(422, 'Specify exactly one of date or month')
        start, end = month_bounds(month) if month is not None else (civil_date(date or ''),) * 2
        where = 'event_on BETWEEN ? AND ?'
        params: list = [start, end]
        if type != 'all':
            where += ' AND kind=?'
            params.append(type)
        with read_database(database) as conn:
            total = conn.execute(f'SELECT COUNT(*) FROM public_changes WHERE {where}', params).fetchone()[0]
            rows = conn.execute(
                f'SELECT * FROM public_changes WHERE {where} ORDER BY event_on,kind,event_id,id LIMIT ? OFFSET ?',
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {'items': [change_data(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

    @app.get('/api/law-changes/{change_id}', response_model=PublicDetail)
    def detail(change_id: str):
        with read_database(database) as conn:
            row = conn.execute('SELECT * FROM public_changes WHERE id=?', (change_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Change not found')
        result = change_data(row)
        result['diff'] = (
            list(difflib.ndiff((row['before_text'] or '').splitlines(), (row['after_text'] or '').splitlines()))
            if row['comparable']
            else None
        )
        return result

    @app.get('/api/law-calendar/status', response_model=CalendarStatus)
    def status():
        with read_database(database) as conn:
            row = conn.execute('SELECT collected_at,verified_at FROM collection_status WHERE id=1').fetchone()
        collected, verified = (row['collected_at'], row['verified_at']) if row else (None, None)
        delayed = True
        if collected:
            try:
                stamp = datetime.fromisoformat(collected)
                delayed = stamp.tzinfo is None or datetime.now(KST) - stamp > timedelta(hours=48)
            except ValueError:
                pass
        return {'collectedAt': collected, 'verifiedAt': verified, 'delayed': delayed, 'timezone': 'Asia/Seoul'}

    if frontend is not None:
        app.mount('/', StaticFiles(directory=frontend, html=True), name='calendar')
    return app


def main():
    parser = argparse.ArgumentParser(description='Independent calendar read API')
    parser.add_argument('--database', type=Path, default=os.environ.get('LAW_CALENDAR_DB'))
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--frontend', type=Path)
    args = parser.parse_args()
    if args.database is None or not 1 <= args.port <= 65535:
        parser.error('An explicit database and valid port are required')
    uvicorn.run(create_app(args.database, args.frontend), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
