"""Import saved monitor law events without running collectors or trusting legal dates."""

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from law_calendar.automation import DatedChange, apply_automation, read_schedule, resolve_changes
from law_calendar.db import write_database
from law_calendar.validation import civil_date, timestamp

PARSER_VERSION = 'monitor-law-v7'
ARTICLE = re.compile(r'^#####\s*(제\d+조(?:의\d+)*)', re.MULTILINE)


class Side(BaseModel):
    file: str
    version_key: str


class Target(BaseModel):
    law_id: str
    law_name: str


class Chunk(BaseModel):
    article: str


class Diff(BaseModel):
    chunks: list[Chunk]


class Context(BaseModel):
    source_url: str | None = None


class MonitorEvent(BaseModel):
    model_config = ConfigDict(strict=True)
    event_id: str = Field(min_length=1)
    source: str
    change_type: str
    detected_at: str
    target: Target
    old: Side | None
    new: Side | None
    diff: Diff
    context: Context
    dates: dict


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def inside(home: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(home.resolve()):
        raise ValueError('Source path escapes the explicit monitor home')
    return resolved


def article_texts(text: str) -> dict[str, str]:
    """Retain exact source spans including headings, whitespace and line endings."""
    headings = list(re.finditer(r'^#{1,5}\s+.*$', text, re.MULTILINE))
    result = {}
    for index, heading in enumerate(headings):
        match = ARTICLE.match(heading.group())
        if match:
            label = match.group(1)
            if label in result:
                raise ValueError('Duplicate article label requires manual source normalization')
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            result[label] = text[heading.start() : end]
    return result


def load_event(home: Path, path: Path):
    raw = inside(home, path).read_bytes().decode('utf-8')
    event = MonitorEvent.model_validate_json(raw)
    if event.source != 'legalize_law' or event.change_type not in {'law_new', 'law_amended', 'law_repealed'}:
        raise ValueError('Unsupported monitor event; not a law article candidate')
    timestamp(event.detected_at)
    if not event.target.law_id.strip() or not event.target.law_name.strip():
        raise ValueError('A stable law ID and name are required')
    texts = {}
    articles = {}
    for side in ('old', 'new'):
        ref = getattr(event, side)
        if ref is not None:
            texts[side] = inside(home, home / ref.file).read_bytes().decode('utf-8')
            articles[side] = article_texts(texts[side])
    labels = [c.article for c in event.diff.chunks if ARTICLE.fullmatch('##### ' + c.article)]
    labels = list(dict.fromkeys(labels))
    if not labels or not any(texts.values()):
        raise ValueError('No changed articles with saved source text')
    for label in labels:
        if not any(label in part for part in articles.values()):
            raise ValueError('Changed article absent from saved versions')
    labels = [label for label in labels if articles.get('old', {}).get(label) != articles.get('new', {}).get(label)]
    files_json = json.dumps(texts, ensure_ascii=False, sort_keys=True)
    snapshot_id = digest(PARSER_VERSION + raw + files_json)
    return event, raw, texts, articles, labels, files_json, snapshot_id


def pending_backfill_cutoff(raw: dict) -> tuple[str, set[str]]:
    """Keep a saved pending-only preview within the source's dated selection."""
    backfill = raw.get('context', {}).get('offline_backfill')
    if not isinstance(backfill, dict):
        return '', set()
    as_of, candidates = backfill.get('v5_scope_as_of'), backfill.get('v5_pending_candidates')
    if not isinstance(as_of, str) or not isinstance(candidates, dict):
        return '', set()
    return civil_date(as_of), set(candidates)


def import_event(database: Path, home: Path, path: Path) -> tuple[bool, str]:
    event, raw, texts, articles, labels, files_json, snapshot_id = load_event(home, path)
    event_id = digest(event.source + ':' + event.event_id)
    law_id = 'law:' + event.target.law_id
    with write_database(database) as conn:
        prior = conn.execute(
            'SELECT s.id,s.raw_event,s.files_json FROM event_sources e '
            'JOIN source_snapshots s ON s.id=e.snapshot_id WHERE e.event_id=?',
            (event_id,),
        ).fetchone()
        if not prior and not labels:
            return False, event.detected_at
        same_source = bool(prior and prior['raw_event'] == raw and prior['files_json'] == files_json)
        if prior and (
            prior['id'] == snapshot_id
            or (
                same_source
                and conn.execute(
                    'SELECT 1 FROM review_audit a JOIN review_details d ON d.audit_id=a.id '
                    "WHERE a.event_id=? AND d.snapshot_id=? AND a.decision IN ('approve','reject','withdraw') LIMIT 1",
                    (event_id, prior['id']),
                ).fetchone()
            )
        ):
            return False, event.detected_at
        identity = conn.execute('SELECT law_id,review_state FROM change_events WHERE id=?', (event_id,)).fetchone()
        if identity and identity['law_id'] != law_id:
            raise ValueError('Upstream event identity changed; resolve the law ID before importing')
        law = conn.execute('SELECT name FROM laws WHERE id=?', (law_id,)).fetchone()
        if law and law['name'] != event.target.law_name:
            raise ValueError('Law name changed; resolve identity before importing')
        conn.execute('INSERT OR IGNORE INTO laws VALUES (?,?)', (law_id, event.target.law_name))
        if prior:
            note = (
                'Calendar rules changed; automatic decision re-evaluated'
                if same_source
                else 'Source correction requires renewed review'
            )
            audit = conn.execute(
                "INSERT INTO review_audit(event_id,decision,reviewer,reviewed_at,note) VALUES (?,'invalidate',?,?,?)",
                (event_id, 'import-adapter', utc_now(), note),
            )
            conn.execute(
                'INSERT INTO review_details VALUES (?,?,?)',
                (audit.lastrowid, prior['id'], json.dumps({'next_snapshot': snapshot_id})),
            )
            conn.execute(
                "UPDATE article_changes SET review_state='pending',verified_at=NULL WHERE event_id=?", (event_id,)
            )
        conn.execute(
            """INSERT INTO change_events(id,law_id,kind,status,collected_at,source_url)
            VALUES (?,?,'effective','확인 필요',?,?) ON CONFLICT(id) DO UPDATE SET
            status='확인 필요',review_state='pending',verified_at=NULL,published_on=NULL,promulgated_on=NULL,
            comment_deadline=NULL,collected_at=excluded.collected_at,source_url=excluded.source_url""",
            (event_id, law_id, event.detected_at, event.context.source_url),
        )
        conn.execute(
            'INSERT OR IGNORE INTO source_snapshots VALUES (?,?,?,?,?,?,?)',
            (snapshot_id, event_id, raw, files_json, PARSER_VERSION, event.detected_at, utc_now()),
        )
        conn.execute(
            'INSERT INTO event_sources VALUES (?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
            (event_id, event.source, event.event_id, snapshot_id),
        )
        version_ids = {}
        for side, text in texts.items():
            version_id = digest(snapshot_id + ':' + side)
            ref = getattr(event, side)
            conn.execute(
                'INSERT OR IGNORE INTO law_versions VALUES (?,?,?,?,?,?,?)',
                (version_id, law_id, snapshot_id, side, ref.version_key, text, digest(text)),
            )
            for label, article in articles[side].items():
                article_id = digest(version_id + ':' + label)
                conn.execute(
                    'INSERT OR IGNORE INTO article_versions VALUES (?,?,?,?)', (article_id, version_id, label, article)
                )
                version_ids[side, label] = article_id
        raw_event = json.loads(raw)
        article_order = tuple(articles.get('new', {}))
        evidence = read_schedule(raw_event, article_order, new_document=texts.get('new', ''))
        cutoff, pending_labels = pending_backfill_cutoff(raw_event)
        changes = {}
        for label in labels:
            before, after = (articles.get(side, {}).get(label) for side in ('old', 'new'))
            dated = resolve_changes(raw_event, label, before, after, article_order, evidence, texts.get('new', ''))
            if label in pending_labels:
                dated = [change for change in dated if change.day is None or change.day > cutoff]
                if not dated:
                    dated = [DatedChange(label, before, after, None, 'conflict')]
            for change in dated:
                change_id = digest(event_id + ':' + change.article)
                changes[change_id] = change
                conn.execute(
                    """INSERT INTO article_changes(id,event_id,article,summary,before_text,after_text)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET effective_on=NULL,summary=excluded.summary,
                    before_text=excluded.before_text,after_text=excluded.after_text,comparable=0,
                    review_state='pending',verified_at=NULL""",
                    (
                        change_id,
                        event_id,
                        change.article,
                        f'{change.article} 변경 후보 · 원문·시행일 확인 필요',
                        change.before,
                        change.after,
                    ),
                )
                conn.execute(
                    'INSERT INTO change_versions VALUES (?,?,?,?) ON CONFLICT(change_id) DO UPDATE SET '
                    'snapshot_id=excluded.snapshot_id,before_version_id=excluded.before_version_id,after_version_id=excluded.after_version_id',
                    (change_id, snapshot_id, version_ids.get(('old', label)), version_ids.get(('new', label))),
                )
        if identity and identity['review_state'] in ('rejected', 'withdrawn'):
            conn.execute('UPDATE change_events SET review_state=? WHERE id=?', (identity['review_state'], event_id))
            conn.execute(
                'UPDATE article_changes SET review_state=? WHERE event_id=?', (identity['review_state'], event_id)
            )
        else:
            apply_automation(conn, event_id, snapshot_id, raw_event, changes)
    return True, event.detected_at


def import_files(database: Path, home: Path, paths: list[Path]) -> dict[str, int]:
    """Isolate bad files; SQLite serializes writers and deterministic IDs make retries safe."""
    started = utc_now()
    result = {'imported': 0, 'unchanged': 0, 'failed': 0}
    errors, collected = [], []
    for path in paths:
        try:
            changed, stamp = import_event(database, home, path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            result['failed'] += 1
            errors.append({'file': str(path), 'error': f'{type(exc).__name__}: {exc}'})
        else:
            result['imported' if changed else 'unchanged'] += 1
            collected.append(stamp)
    with write_database(database) as conn:
        conn.execute(
            'INSERT INTO import_runs VALUES (?,?,?,?,?,?,?)',
            (uuid.uuid4().hex, started, utc_now(), *result.values(), json.dumps(errors, ensure_ascii=False)),
        )
        if collected and not result['failed']:
            newest = max(collected, key=datetime.fromisoformat)
            prior = conn.execute('SELECT collected_at FROM collection_status WHERE id=1').fetchone()
            if (
                not prior
                or not prior['collected_at']
                or datetime.fromisoformat(newest) > datetime.fromisoformat(prior['collected_at'])
            ):
                conn.execute(
                    'INSERT INTO collection_status(id,collected_at) VALUES (1,?) '
                    'ON CONFLICT(id) DO UPDATE SET collected_at=excluded.collected_at',
                    (newest,),
                )
    return result
