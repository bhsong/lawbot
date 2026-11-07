import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lawbot.db.connection import transaction
from lawbot.feedback import REASONS
from lawbot.feedback_store import insert, record_values

REQUIRED = ('feedback_id', 'turn_id', 'ts', 'label', 'question')


@dataclass(frozen=True)
class FeedbackRow:
    location: str
    values: tuple[str | None, ...]
    record: str


@dataclass(frozen=True)
class FeedbackImport:
    rows: list[FeedbackRow]
    malformed: list[str]
    invalid: list[str]

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _parse_row(record: object, location: str) -> FeedbackRow | None:
    if not isinstance(record, dict) or any(not isinstance(record.get(key), str) for key in REQUIRED):
        return None
    if not all(record[key] for key in ('feedback_id', 'turn_id', 'ts')):
        return None
    label, reason = record['label'], record.get('reason')
    if label not in ('like', 'flag', 'cleared'):
        return None
    if reason not in (REASONS.values() if label == 'flag' else (None, '')):
        return None
    values = record_values(record)
    if any(value is not None and not isinstance(value, str) for value in values):
        return None
    return FeedbackRow(location, values, json.dumps(record, ensure_ascii=False))


def prepare_feedback(paths: list[Path]) -> FeedbackImport:
    """Read JSONL records and keep diagnostics limited to file names and line numbers."""
    result = FeedbackImport([], [], [])
    for path in paths:
        with path.open(encoding='utf-8') as source:
            for line_no, line in enumerate(source, 1):
                if not line.strip():
                    continue
                location = f'{path.name}:{line_no}'
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    result.malformed.append(location)
                    continue
                row = _parse_row(record, location)
                if row is None:
                    result.invalid.append(location)
                else:
                    result.rows.append(row)
    return result


def write_feedback(conn: sqlite3.Connection, feedback: FeedbackImport) -> int:
    """Append valid records atomically. A conflicting feedback ID rolls back the batch."""
    changed = 0
    with transaction(conn):
        for row in feedback.rows:
            previous = conn.execute('SELECT record FROM feedbacks WHERE feedback_id=?', (row.values[0],)).fetchone()
            if previous is not None:
                if previous != (row.record,):
                    raise ValueError(f'같은 피드백 ID의 원본 내용이 다릅니다: {row.location}')
                continue
            insert(conn, row.values, row.record, f'import:{row.location}')
            changed += 1
    return changed
