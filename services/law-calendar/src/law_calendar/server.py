"""Public calendar reads and service-token administrator reviews."""

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
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from law_calendar import admin, erp_rules, news
from law_calendar.automation import REASONS
from law_calendar.db import NOTICE_CONTENT_VERSION, read_database
from law_calendar.models import (
    CalendarMonth,
    CalendarStatus,
    ChangePage,
    ErpFeed,
    ErpNews,
    ErpUpdates,
    HoldPage,
    PublicDetail,
)
from lawbot_common import envfile
from lawbot_common.erp_daily import read_snapshot
from lawbot_common.erp_news import Agency
from lawbot_common.erp_rules import ErpRules, RuleSnapshot
from lawbot_common.logs import logger
from lawbot_common.tax_schedule import TaxSchedule

KST = timezone(timedelta(hours=9))
SECRETS = ('LAWBOT_CALENDAR_ADMIN_TOKEN',)


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


def erp_updates(feed: Path | None) -> dict:
    """Read the approved ERP feed; a missing or malformed file never affects the law calendar."""
    if feed is None or not feed.is_file():
        return {'state': 'missing'}
    try:
        parsed = ErpFeed.model_validate_json(feed.read_bytes())
    except ValidationError:
        logger('law_calendar', 'calendar').warning('ERP feed is invalid: %s', feed.name)
        return {'state': 'invalid'}
    return {'state': 'ok', **parsed.model_dump(exclude={'schemaVersion'})}


def create_app(
    database: Path,
    frontend: Path | None = None,
    erp_feed: Path | None = None,
    *,
    news_feed: Path | None = None,
    rules_feed: Path | None = None,
    daily_feed: Path | None = None,
) -> FastAPI:
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
                content = conn.execute(
                    'SELECT summary,pdf_url FROM assembly_notice_content WHERE snapshot_id=?',
                    (snapshot,),
                ).fetchone()
                if content and (content['summary'] or content['pdf_url']):
                    result['noticeContent'] = {
                        'summary': content['summary'],
                        'pdfUrl': content['pdf_url'],
                    }
        result['diff'] = (
            list(difflib.ndiff((row['before_text'] or '').splitlines(), (row['after_text'] or '').splitlines()))
            if row['comparable']
            else None
        )
        return result

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

    def daily_snapshot(response: Response) -> dict | None:
        if daily_feed is None:
            return None
        try:
            snapshot = read_snapshot(daily_feed)
        except FileNotFoundError:
            return {'state': 'missing'}
        except OSError, ValueError:
            return {'state': 'invalid'}
        response.headers['X-ERP-Snapshot'] = snapshot['snapshotHash']
        return snapshot

    @app.get('/api/tax-schedule')
    def tax_schedule(month: str, response: Response):
        start, end = month_bounds(month)
        snapshot = daily_snapshot(response) or {}
        schedule = snapshot.get('feeds', {}).get('taxSchedule')
        if not schedule:
            return {'items': [], 'confirmedAt': None}
        try:
            verified = TaxSchedule.model_validate(schedule)
        except ValidationError as exc:
            raise HTTPException(503, 'Tax schedule unavailable') from exc
        items = [
            {
                'id': row.upstream_id,
                'title': row.title,
                'date': row.deadline_on.isoformat(),
                'targetPeriod': row.target_period,
                'sourceUrl': row.canonical_url,
            }
            for row in verified.items
            if start <= row.deadline_on.isoformat() <= end
        ]
        return {
            'items': sorted(items, key=lambda row: (row['date'], row['title'], row['id'])),
            'confirmedAt': verified.confirmedAt.isoformat(),
        }

    @app.get('/api/erp-updates', response_model=ErpUpdates)
    def erp(response: Response):
        snapshot = daily_snapshot(response)
        if snapshot is None:
            return erp_updates(erp_feed)
        return snapshot.get('feeds', {}).get('erp', {'state': snapshot.get('state', 'invalid')})

    @app.get('/api/erp-updates/status')
    def erp_status(response: Response):
        snapshot = daily_snapshot(response)
        if snapshot is None:
            return {'state': 'unconfigured'}
        return {
            key: value
            for key, value in snapshot.items()
            if key
            in (
                'state',
                'status',
                'dayKst',
                'cutoffAt',
                'snapshotHash',
                'sources',
            )
        }

    @app.get('/api/erp-updates/v2', response_model=ErpRules)
    def erp_rule_versions(response: Response, asOf: str | None = None, targetOn: str | None = None):
        as_of = Date.fromisoformat(civil_date(asOf)) if asOf is not None else erp_rules.today_kst()
        target = Date.fromisoformat(civil_date(targetOn)) if targetOn is not None else as_of
        snapshot = daily_snapshot(response)
        if snapshot is None:
            return erp_rules.read(rules_feed, as_of, target)
        rules = snapshot.get('feeds', {}).get('rules')
        if rules:
            return erp_rules.from_snapshot(RuleSnapshot.model_validate(rules), as_of, target)
        return ErpRules(state=snapshot.get('state', 'missing'), asOfDate=as_of)

    @app.get('/api/erp-news/latest', response_model=ErpNews)
    def latest_news(response: Response, agency: Agency, limit: int = Query(10, ge=1, le=10)):
        snapshot = daily_snapshot(response)
        if snapshot is None or agency == 'hometax':
            return news.response(database, news_feed, agency, limit)
        entry = snapshot.get('feeds', {}).get('news', {}).get(agency)
        if entry is None:
            return {'agency': agency, 'officialHome': news.HOME[agency], 'state': snapshot.get('state', 'missing')}
        return news.current_news(entry, limit)

    app.include_router(admin.router(database))
    if frontend is not None:
        app.mount('/', StaticFiles(directory=frontend, html=True), name='calendar')
    return app


def main():
    service = Path(__file__).resolve().parents[2]
    envfile.load(service / '.env', service, SECRETS, logger('law_calendar', 'calendar'))
    parser = argparse.ArgumentParser(description='Independent calendar API')
    parser.add_argument('--database', type=Path, default=os.environ.get('LAW_CALENDAR_DB'))
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--frontend', type=Path)
    parser.add_argument('--erp-feed', type=Path, default=os.environ.get('LAWBOT_CALENDAR_ERP_FEED'))
    parser.add_argument('--news-feed', type=Path, default=os.environ.get('LAWBOT_CALENDAR_NEWS_FEED'))
    parser.add_argument('--erp-rules-feed', type=Path, default=os.environ.get('LAWBOT_CALENDAR_ERP_RULES_FEED'))
    parser.add_argument('--erp-daily-feed', type=Path, default=os.environ.get('LAWBOT_CALENDAR_ERP_DAILY_FEED'))
    args = parser.parse_args()
    if args.database is None or not 1 <= args.port <= 65535:
        parser.error('An explicit database and valid port are required')
    uvicorn.run(
        create_app(
            args.database,
            args.frontend,
            args.erp_feed,
            news_feed=args.news_feed,
            rules_feed=args.erp_rules_feed,
            daily_feed=args.erp_daily_feed,
        ),
        host='127.0.0.1',
        port=args.port,
    )


if __name__ == '__main__':
    main()
