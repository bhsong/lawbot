"""Persist and page scheduled import diagnostics without storing source payloads."""

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from law_calendar.db import read_database, write_database


class ImportInputError(ValueError):
    """An import validation error with a fixed diagnostic safe for persistence."""


def error_message(exc: Exception) -> str:
    if isinstance(exc, ImportInputError):
        return str(exc)
    if isinstance(exc, ValidationError):
        messages = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False)[:3]:
            fields = []
            for field in error['loc']:
                fields.append(str(field))
                if field in ('dates', 'date_sources', 'status_fields'):
                    break  # A dictionary key may contain source data.
            messages.append(f'{".".join(fields)}: {error["msg"]}')
        return ' / '.join(messages)[:500]
    if isinstance(exc, json.JSONDecodeError):
        return f'{exc.msg} (line {exc.lineno}, column {exc.colno})'
    if isinstance(exc, UnicodeDecodeError):
        return 'Saved source is not valid UTF-8'
    if isinstance(exc, OSError):
        return os.strerror(exc.errno) if exc.errno is not None else 'Unable to read or write import data'
    if isinstance(exc, sqlite3.Error):
        return getattr(exc, 'sqlite_errorname', 'Unable to access the calendar database')
    return 'Invalid import data'


def import_failure(source: str, event_id: str | None, exc: Exception) -> dict[str, str | None]:
    return {'source': source, 'event_id': event_id, 'error_type': type(exc).__name__, 'message': error_message(exc)}


def record_import_failure(database: Path, source: str, event_id: str | None, exc: Exception) -> None:
    stamp = datetime.now(UTC).isoformat(timespec='seconds')
    with write_database(database) as conn:
        conn.execute(
            'INSERT INTO import_runs VALUES (?,?,?,?,?,?,?)',
            (uuid.uuid4().hex, stamp, stamp, 0, 0, 1, json.dumps([import_failure(source, event_id, exc)])),
        )


def list_import_failures(database: Path, page: int, page_size: int) -> dict:
    """Page sanitized scheduled diagnostics. Legacy CLI rows contain private paths and inputs."""
    source = """
        FROM import_runs r
        JOIN json_each(CASE WHEN json_valid(r.errors_json) THEN r.errors_json ELSE '[]' END) e
        WHERE r.failed > 0 AND json_extract(r.errors_json,e.fullkey || '.source')
            IN ('legalize_law','assembly_bill','assembly_notice')
    """
    with read_database(database) as conn:
        conn.execute('BEGIN')
        total = conn.execute(f'SELECT COUNT(*) {source}').fetchone()[0]
        rows = conn.execute(
            f"""SELECT r.finished_at AS occurred_at,
                    json_extract(r.errors_json,e.fullkey || '.source') AS source,
                    json_extract(r.errors_json,e.fullkey || '.event_id') AS event_id,
                    json_extract(r.errors_json,e.fullkey || '.error_type') AS error_type,
                    json_extract(r.errors_json,e.fullkey || '.message') AS message
                {source}
                ORDER BY r.finished_at DESC,r.rowid DESC,e.key
                LIMIT ? OFFSET ?""",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}
