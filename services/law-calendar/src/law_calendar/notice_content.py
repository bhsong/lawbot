"""Collect supplementary official content for published Assembly notice snapshots."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pypdfium2 import PdfiumError

from law_calendar.db import CURRENT_VERSION, read_database, write_database
from law_calendar.notice_comparison import comparison_images
from law_calendar.notice_source import NoticeSourceError, fetch_official, parse_notice_detail

MAX_NOTICES = 150


@dataclass(frozen=True, slots=True)
class NoticeContent:
    summary: str | None
    pdf_url: str | None
    pages: list[tuple[int, bytes]] = field(default_factory=list)
    pdf_error: str | None = None


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
    except (NoticeSourceError, PdfiumError) as exc:
        return NoticeContent(summary, pdf_url, pdf_error=type(exc).__name__)
    return NoticeContent(summary, pdf_url, pages)


def refresh_notice_content(
    database: Path,
    *,
    limit: int = MAX_NOTICES,
    refresh: bool = False,
    read: Callable[[str, str, str | None, str], NoticeContent] = read_notice_content,
) -> dict:
    """Attach content to current approved snapshots without altering their review or dates."""
    if not 1 <= limit <= MAX_NOTICES:
        raise ValueError(f'Notice limit must be between 1 and {MAX_NOTICES}')
    with read_database(database) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0] != CURRENT_VERSION:
            raise ValueError('Migrate the calendar database before collecting notice content')
        rows = conn.execute(
            """SELECT e.id AS entry_id,s.id,s.bill_no,s.source_url,start.event_on AS start,end.event_on AS end
            FROM assembly_entries e JOIN assembly_snapshots s ON s.id=e.snapshot_id
            JOIN assembly_dates end ON end.snapshot_id=s.id AND end.role='notice_end'
            LEFT JOIN assembly_dates start ON start.snapshot_id=s.id AND start.role='notice_start'
            LEFT JOIN assembly_notice_content c ON c.snapshot_id=s.id
            WHERE e.source='assembly_notice' AND e.review_state='approved' AND (? OR c.snapshot_id IS NULL)
            ORDER BY s.collected_at,s.id LIMIT ?""",
            (refresh, limit),
        ).fetchall()

    result = {
        'selected': len(rows),
        'stored': 0,
        'summaries': 0,
        'comparisons': 0,
        'pages': 0,
        'stale': 0,
        'failed': [],
        'pdfErrors': [],
    }
    for row in rows:
        try:
            content = read(row['source_url'], row['bill_no'], row['start'], row['end'])
        except NoticeSourceError as exc:
            result['failed'].append({'billNo': row['bill_no'], 'reason': str(exc)})
            continue
        if content.pdf_error:
            result['pdfErrors'].append({'billNo': row['bill_no'], 'reason': content.pdf_error})
        with write_database(database) as conn:
            current = conn.execute(
                "SELECT 1 FROM assembly_entries WHERE id=? AND snapshot_id=? AND review_state='approved'",
                (row['entry_id'], row['id']),
            ).fetchone()
            if not current:
                result['stale'] += 1
                continue
            replace = refresh and not content.pdf_error
            updated = conn.execute(
                'INSERT INTO assembly_notice_content VALUES (?,?,?) ON CONFLICT(snapshot_id) DO UPDATE SET '
                'summary=excluded.summary,pdf_url=excluded.pdf_url WHERE ?',
                (row['id'], content.summary, content.pdf_url, replace),
            )
            if not updated.rowcount:
                continue
            if replace:
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
