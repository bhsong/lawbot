"""Public read-only API. No collection, inference, chatbot authentication or writes."""

import argparse
import calendar
import difflib
import json
import os
import re
import sqlite3
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from law_calendar.automation import REASONS
from law_calendar.db import NOTICE_CONTENT_VERSION, NOTICE_LIMIT_VERSION, read_database
from law_calendar.models import CalendarMonth, CalendarStatus, ChangePage, HoldPage, PublicDetail

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
    item = {
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
    if row['assembly_json']:
        raw = json.loads(row['assembly_json'])
        dates, status = raw['dates'], raw['context']['status_fields']
        item['assembly'] = {
            'agency': row['agency'],
            'billNo': row['bill_no'],
            'dateRole': row['date_role'],
            'noticeStart': dates.get('notice_start'),
            'noticeEnd': dates.get('notice_end'),
            'billProposed': dates.get('bill_proposed'),
            'billDecided': dates.get('bill_decided'),
            'committeeDecided': dates.get('committee_decided'),
            'committeeResult': status.get('CMT_PROC_RESULT_CD') or None,
            'judiciaryDecided': dates.get('judiciary_decided'),
            'judiciaryResult': status.get('LAW_PROC_RESULT_CD') or None,
        }
    return item


def notice_snapshot(conn: sqlite3.Connection, change_id: str) -> str | None:
    if conn.execute('PRAGMA user_version').fetchone()[0] < NOTICE_CONTENT_VERSION:
        return None
    row = conn.execute(
        "SELECT id FROM public_assembly WHERE kind='notice' AND event_id || ':' || date_role=?",
        (change_id,),
    ).fetchone()
    return row['id'] if row else None


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

    @app.get('/api/law-calendar', response_model=CalendarMonth, response_model_exclude_unset=True)
    def monthly(month: str):
        start, end = month_bounds(month)
        with read_database(database) as conn:
            rows = conn.execute(
                """SELECT event_on,
                COUNT(DISTINCT CASE WHEN kind='notice' THEN event_id END) AS notice_count,
                SUM(CASE WHEN kind='effective' THEN 1 ELSE 0 END) AS effective_count,
                COUNT(DISTINCT CASE WHEN kind='bill' THEN event_id END) AS bill_count
                FROM public_calendar_events WHERE event_on BETWEEN ? AND ? GROUP BY event_on ORDER BY event_on""",
                (start, end),
            ).fetchall()
        days = []
        for row in rows:
            day = dict(row)
            if not day['bill_count']:
                day.pop('bill_count')
            days.append(day)
        return {'month': month, 'timezone': 'Asia/Seoul', 'days': days}

    @app.get('/api/law-events', response_model=ChangePage, response_model_exclude_unset=True)
    def events(
        date: str | None = None,
        month: str | None = None,
        type: Literal['all', 'notice', 'effective', 'bill'] = 'all',
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
            total = conn.execute(f'SELECT COUNT(*) FROM public_calendar_events WHERE {where}', params).fetchone()[0]
            rows = conn.execute(
                f'SELECT * FROM public_calendar_events WHERE {where} ORDER BY event_on,kind,event_id,id LIMIT ? OFFSET ?',
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {'items': [change_data(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

    @app.get('/api/law-changes/{change_id}', response_model=PublicDetail, response_model_exclude_unset=True)
    def detail(change_id: str):
        with read_database(database) as conn:
            conn.execute('BEGIN')
            row = conn.execute('SELECT * FROM public_calendar_events WHERE id=?', (change_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Change not found')
            result = change_data(row)
            if row['kind'] == 'notice' and row['assembly_json'] and (snapshot := notice_snapshot(conn, change_id)):
                has_limit_status = conn.execute('PRAGMA user_version').fetchone()[0] >= NOTICE_LIMIT_VERSION
                limit_column = 'pdf_limit_reason' if has_limit_status else 'NULL AS pdf_limit_reason'
                content = conn.execute(
                    f'SELECT summary,pdf_url,{limit_column} FROM assembly_notice_content WHERE snapshot_id=?',
                    (snapshot,),
                ).fetchone()
                if content and (content['summary'] or content['pdf_url']):
                    pages = conn.execute(
                        'SELECT page FROM assembly_notice_pages WHERE snapshot_id=? ORDER BY page', (snapshot,)
                    ).fetchall()
                    result['noticeContent'] = {
                        'summary': content['summary'],
                        'pdfUrl': content['pdf_url'],
                        'comparisonPages': [page['page'] for page in pages],
                    }
                    if content['pdf_limit_reason']:
                        result['noticeContent']['comparisonLimited'] = True
        result['diff'] = (
            list(difflib.ndiff((row['before_text'] or '').splitlines(), (row['after_text'] or '').splitlines()))
            if row['comparable']
            else None
        )
        return result

    @app.get('/api/law-changes/{change_id}/notice-comparison/{page}', response_class=Response)
    def notice_comparison(change_id: str, page: int):
        with read_database(database) as conn:
            conn.execute('BEGIN')
            snapshot = notice_snapshot(conn, change_id)
            row = (
                conn.execute(
                    'SELECT png FROM assembly_notice_pages WHERE snapshot_id=? AND page=?', (snapshot, page)
                ).fetchone()
                if snapshot
                else None
            )
        if not row:
            raise HTTPException(404, 'Notice comparison page not found')
        return Response(row['png'], media_type='image/png')

    @app.get('/api/law-calendar/status', response_model=CalendarStatus, response_model_exclude_unset=True)
    def status():
        with read_database(database) as conn:
            row = conn.execute('SELECT collected_at,verified_at FROM collection_status WHERE id=1').fetchone()
            assembly = dict(conn.execute('SELECT source,collected_at FROM assembly_collection_status').fetchall())
        collected, verified = (row['collected_at'], row['verified_at']) if row else (None, None)
        delayed = True
        if collected:
            try:
                stamp = datetime.fromisoformat(collected)
                delayed = stamp.tzinfo is None or datetime.now(KST) - stamp > timedelta(hours=48)
            except ValueError:
                pass
        result = {'collectedAt': collected, 'verifiedAt': verified, 'delayed': delayed, 'timezone': 'Asia/Seoul'}
        for source, field in (('assembly_bill', 'billCollectedAt'), ('assembly_notice', 'noticeCollectedAt')):
            if source in assembly:
                result[field] = assembly[source]
        return result

    @app.get('/api/law-holds', response_model=HoldPage)
    def holds(
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(20, ge=1, le=100),
        type: Literal['effective', 'notice'] = 'effective',
    ):
        params = (page_size, (page - 1) * page_size)
        with read_database(database) as conn:
            conn.execute('BEGIN')
            if type == 'notice':
                source = (
                    'FROM assembly_entries e JOIN assembly_snapshots s ON s.id=e.snapshot_id '
                    "WHERE e.source='assembly_notice' AND e.review_state='pending' "
                    "AND s.source_url GLOB 'https://pal.assembly.go.kr/napal/lgsltpa/*'"
                )
                total = conn.execute(f'SELECT COUNT(*) {source}').fetchone()[0]
                rows = conn.execute(
                    'SELECT s.id,s.title AS lawName,s.bill_no AS billNo,s.source_url AS sourceUrl, '
                    's.collected_at AS collectedAt, '
                    '(SELECT COUNT(*) FROM assembly_dates d WHERE d.snapshot_id=s.id '
                    "AND d.role IN ('notice_start','notice_end'))=2 AS has_period "
                    f'{source} ORDER BY s.collected_at DESC,s.id LIMIT ? OFFSET ?',
                    params,
                ).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item['reason'] = (
                        '국회 공식 상세의 의안번호와 예고기간 확인 필요'
                        if item.pop('has_period')
                        else '예고 시작일 또는 종료일 확인 필요'
                    )
                    items.append(item)
            else:
                total = conn.execute('SELECT COUNT(*) FROM public_holds').fetchone()[0]
                rows = conn.execute(
                    'SELECT * FROM public_holds ORDER BY law_name,article,id LIMIT ? OFFSET ?', params
                ).fetchall()
                items = [
                    {
                        'id': row['id'],
                        'lawName': row['law_name'],
                        'article': row['article'],
                        'reason': REASONS.get(row['reason'], REASONS['missing']),
                        'sourceUrl': row['source_url'],
                        'collectedAt': row['collected_at'],
                    }
                    for row in rows
                ]
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size}

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
