"""Persist administrator login outcomes without credential or session data."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from lawbot.db.connection import connect, transaction
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')


def record_login(path: Path, actor: str, accepted: bool) -> None:
    """Log one administrator login outcome using an already validated DB path."""
    at = datetime.now(UTC).isoformat()
    action = 'login_ok' if accepted else 'login_fail'
    try:
        with connect(path, write=True) as conn, transaction(conn):
            conn.execute(
                'INSERT INTO audit_events (at, actor, role, action) VALUES (?, ?, ?, ?)', (at, actor, 'admin', action)
            )
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        log.warning('관리자 로그인 감사 기록을 쓰지 못했습니다 (%s).', type(exc).__name__)
