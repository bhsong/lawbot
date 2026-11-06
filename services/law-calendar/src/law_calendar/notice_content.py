"""Verify Assembly notice snapshots and collect their official content."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pypdfium2 import PdfiumError

from law_calendar.assembly_ingest import AUTO_NOTICE_REVIEWER, record_assembly_review
from law_calendar.db import CURRENT_VERSION, read_database, write_database
from law_calendar.ingest import utc_now
from law_calendar.notice_comparison import ComparisonLimitError, LimitReason, comparison_images
from law_calendar.notice_source import NoticeSourceError, fetch_official, parse_notice_detail

MAX_NOTICES = 150
DAILY_NOTICE_LIMIT = 24


@dataclass(frozen=True, slots=True)
class NoticeContent:
    summary: str | None
    pdf_url: str | None
    pages: list[tuple[int, bytes]] = field(default_factory=list)
    pdf_error: str | None = None
    pdf_limit_reason: LimitReason | None = None


def read_notice_content(url: str, bill_no: str, start: str | None, end: str) -> NoticeContent:
    try:
        html = fetch_official(url).decode('utf-8')
    except UnicodeError as exc:
        raise NoticeSourceError('Official notice text was not UTF-8') from exc
    summary, pdf_url = parse_notice_detail(html, bill_no, start, end)
    if not pdf_url:
        return NoticeContent(summary, None)
    try:
        pages = comparison_images(fetch_official(pdf_url, pdf=True))
    except ComparisonLimitError as exc:
        return NoticeContent(summary, pdf_url, pdf_limit_reason=exc.reason)
    except (NoticeSourceError, PdfiumError) as exc:
        return NoticeContent(summary, pdf_url, pdf_error=type(exc).__name__)
    return NoticeContent(summary, pdf_url, pages)


def refresh_notice_content(
    database: Path,
    *,
    limit: int = MAX_NOTICES,
    refresh: bool = False,
    after_snapshot: str | None = None,
    publish_verified: bool = False,
    read: Callable[[str, str, str | None, str], NoticeContent] = read_notice_content,
) -> dict:
    """Attach official content and optionally publish pending notices with a verified period."""
    if not 1 <= limit <= MAX_NOTICES:
        raise ValueError(f'Notice limit must be between 1 and {MAX_NOTICES}')
    paginated_refresh = refresh and not publish_verified
    if after_snapshot is not None and not paginated_refresh:
        raise ValueError('A continuation snapshot requires a manual refresh')
    with read_database(database) as conn:
        conn.execute('BEGIN')
        if conn.execute('PRAGMA user_version').fetchone()[0] != CURRENT_VERSION:
            raise ValueError('Migrate the calendar database before collecting notice content')
        after_stamp = None
        if after_snapshot is not None:
            anchor = conn.execute(
                "SELECT collected_at FROM assembly_snapshots WHERE id=? AND source='assembly_notice'",
                (after_snapshot,),
            ).fetchone()
            if not anchor:
                raise ValueError('Continuation notice snapshot not found')
            after_stamp = anchor['collected_at']
        rows = conn.execute(
            """SELECT e.id AS entry_id,s.id,s.bill_no,s.source_url,start.event_on AS start,end.event_on AS end
            FROM assembly_entries e JOIN assembly_snapshots s ON s.id=e.snapshot_id
            JOIN assembly_dates end ON end.snapshot_id=s.id AND end.role='notice_end'
            LEFT JOIN assembly_dates start ON start.snapshot_id=s.id AND start.role='notice_start'
            LEFT JOIN assembly_notice_content c ON c.snapshot_id=s.id
            LEFT JOIN assembly_notice_attempts a ON a.snapshot_id=s.id
            WHERE e.source='assembly_notice'
            AND (e.review_state='approved' OR (:publish AND e.review_state='pending' AND start.event_on IS NOT NULL))
            AND (:refresh OR c.snapshot_id IS NULL OR c.pdf_pending)
            AND (:after_id IS NULL OR (s.collected_at,s.id) > (:after_stamp,:after_id))
            ORDER BY CASE WHEN :publish THEN a.attempted_at END,s.collected_at,s.id LIMIT :limit""",
            {
                'publish': publish_verified,
                'refresh': refresh,
                'after_id': after_snapshot,
                'after_stamp': after_stamp,
                'limit': limit + int(paginated_refresh),
            },
        ).fetchall()

    batch = rows[:limit]
    result = {
        'selected': len(batch),
        'stored': 0,
        'summaries': 0,
        'comparisons': 0,
        'pages': 0,
        'published': 0,
        'stale': 0,
        'failed': [],
        'pdfErrors': [],
        'pdfLimits': [],
    }
    if paginated_refresh:
        result['nextAfterSnapshot'] = batch[-1]['id'] if len(rows) > limit else None
    for row in batch:
        try:
            content = read(row['source_url'], row['bill_no'], row['start'], row['end'])
        except NoticeSourceError as exc:
            result['failed'].append({'billNo': row['bill_no'], 'reason': str(exc)})
            content = None
        if content and content.pdf_error:
            result['pdfErrors'].append({'billNo': row['bill_no'], 'reason': content.pdf_error})
        if content and content.pdf_limit_reason:
            result['pdfLimits'].append({'billNo': row['bill_no'], 'reason': content.pdf_limit_reason})
        with write_database(database) as conn:
            current = conn.execute(
                'SELECT review_state FROM assembly_entries WHERE id=? AND snapshot_id=?',
                (row['entry_id'], row['id']),
            ).fetchone()
            publish = bool(current and current['review_state'] == 'pending' and publish_verified)
            if not current or (current['review_state'] != 'approved' and not publish):
                result['stale'] += 1
                continue
            if publish_verified:
                conn.execute(
                    'INSERT INTO assembly_notice_attempts VALUES (?,?) '
                    'ON CONFLICT(snapshot_id) DO UPDATE SET attempted_at=excluded.attempted_at',
                    (row['id'], utc_now()),
                )
            if content is None:
                continue
            if publish:
                record_assembly_review(
                    conn,
                    row['entry_id'],
                    row['id'],
                    'approve',
                    reviewer=AUTO_NOTICE_REVIEWER,
                    note='국회 상세의 의안번호와 예고 시작·종료일이 수집 기록과 일치',
                )
                result['published'] += 1
            updated = conn.execute(
                'INSERT INTO assembly_notice_content VALUES (?,?,?,?,?) ON CONFLICT(snapshot_id) DO UPDATE SET '
                'summary=excluded.summary,pdf_url=excluded.pdf_url,pdf_pending=excluded.pdf_pending, '
                'pdf_limit_reason=excluded.pdf_limit_reason '
                'WHERE NOT excluded.pdf_pending AND (? OR assembly_notice_content.pdf_pending)',
                (
                    row['id'],
                    content.summary,
                    content.pdf_url,
                    bool(content.pdf_error),
                    content.pdf_limit_reason,
                    refresh,
                ),
            )
            if not updated.rowcount:
                if refresh and content.pdf_error:
                    conn.execute(
                        'UPDATE assembly_notice_content SET pdf_pending=1 '
                        'WHERE snapshot_id=? AND pdf_limit_reason IS NULL',
                        (row['id'],),
                    )
                continue
            if not content.pdf_error and not content.pdf_limit_reason:
                conn.execute('DELETE FROM assembly_notice_pages WHERE snapshot_id=?', (row['id'],))
                conn.executemany(
                    'INSERT INTO assembly_notice_pages VALUES (?,?,?)',
                    [(row['id'], page, png) for page, png in content.pages],
                )
        result['stored'] += 1
        result['summaries'] += bool(content.summary)
        result['comparisons'] += bool(content.pages)
        result['pages'] += len(content.pages)
    return result


def synchronize_notice_content(database: Path) -> dict[str, int]:
    """Run a bounded daily batch and report exceptions for follow-up."""
    with read_database(database) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0] < CURRENT_VERSION:
            return {'notice_migration_required': 1}
    result = refresh_notice_content(database, limit=DAILY_NOTICE_LIMIT, publish_verified=True)
    with read_database(database) as conn:
        pending, pdf_pending, pdf_limits = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE e.review_state='pending'), "
            "COUNT(*) FILTER (WHERE e.review_state='approved' AND c.pdf_pending), "
            "COUNT(*) FILTER (WHERE e.review_state='approved' AND c.pdf_limit_reason IS NOT NULL) "
            'FROM assembly_entries e LEFT JOIN assembly_notice_content c ON c.snapshot_id=e.snapshot_id '
            "WHERE e.source='assembly_notice'"
        ).fetchone()
    return {
        'notice_auto_published': result['published'],
        'notice_content_stored': result['stored'],
        'notice_content_errors': len(result['failed']),
        'notice_pending': pending,
        'notice_pdf_pending': pdf_pending,
        'notice_pdf_limits': pdf_limits,
    }
