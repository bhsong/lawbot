"""Render and deliver the approved ERP snapshot without rereading collector drafts."""

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.errors import HeaderParseError
from email.headerregistry import Address
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from lawbot_common.erp_daily import LIMIT, read_snapshot
from lawbot_common.erp_news import AGENCIES, official_url
from lawbot_common.erp_rules import official_link
from source_hub import gmail
from source_hub.collector import file_lock
from source_hub.daily import morning_path
from source_hub.store import KST

APPLICATION = 0x4552504D
SCHEMA = """CREATE TABLE IF NOT EXISTS deliveries (
 day TEXT NOT NULL, snapshot TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT NOT NULL,
 state TEXT NOT NULL, mime BLOB NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 attempted_at TEXT, provider_id TEXT,
 PRIMARY KEY(day,snapshot,channel,recipient));"""
ENRICHMENT_SCHEMA = 'CREATE TABLE IF NOT EXISTS enrichment (day TEXT PRIMARY KEY)'
BODY_SCHEMA = """CREATE TABLE IF NOT EXISTS bodies (
 day TEXT NOT NULL, snapshot TEXT NOT NULL, subject TEXT NOT NULL, text TEXT NOT NULL, html TEXT NOT NULL,
 PRIMARY KEY(day,snapshot))"""
RETRY_INTERVAL = timedelta(minutes=10)
APPLIES = {
    'legal_effective_date': '법령 시행일',
    'payment_date': '지급일',
    'work_period': '근로제공기간',
    'income_year': '귀속연도',
    'insurance_month': '보험료 적용월',
    'filing_date': '신고·제출일',
    'other': '기타',
}


@dataclass(frozen=True, repr=False)
class Settings:
    page: str
    sender: str
    recipients: dict[str, str]
    at: time = time(7, 30)
    send_unchanged: bool = True
    limit: int = 20

    def __post_init__(self):
        if not page_url(self.page) or not 1 <= self.limit <= 100 or not 1 <= len(self.recipients) <= 20:
            raise ValueError('Invalid ERP email page, item limit or recipient count')
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}', alias) for alias in self.recipients):
            raise ValueError('Invalid ERP email recipient alias')
        addresses = [self.sender, *self.recipients.values()]
        try:
            valid = all(Address(addr_spec=addr).domain and addr.isascii() for addr in addresses)
        except ValueError, IndexError, HeaderParseError:
            valid = False
        if not valid or len({addr.lower() for addr in self.recipients.values()}) != len(self.recipients):
            raise ValueError('Invalid or duplicate ERP email address')
        if self.at.tzinfo is not None:
            raise ValueError('Use a local KST email time')


def page_url(url: str) -> bool:
    if any(char.isspace() or ord(char) < 32 or char in '\\\x7f' for char in url):
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme == 'https' and bool(parts.hostname) and parts.username is None and parts.fragment == 'erp'
    except ValueError:
        return False


def public_link(url: str) -> bool:
    return any(official_url(url, agency) for agency in AGENCIES) or official_link(url, proposal=True)


def morning_entries(path: Path, run_id: str) -> list[dict]:
    try:
        with path.open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) <= LIMIT:
            data = json.loads(raw)
            if data['runId'] == run_id and isinstance(data['entries'], list):
                return [item for item in data['entries'] if isinstance(item, dict)]
    except OSError, ValueError, KeyError, TypeError:
        pass
    return []


def verified_morning(snapshot: dict, entries: list[dict]) -> dict[str, str]:
    """Reuse Qwen lines that are independently confirmed in the approved revision."""
    by_event = {row['eventId']: row for row in snapshot['changes'] if row['kind'] == 'law'}
    reused = {}
    for entry in entries:
        # A group containing a form or another unverified input cannot be safely reused.
        events = entry.get('events')
        if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], dict):
            continue
        event = events[0]
        row = by_event.get(event.get('eventId'))
        reply = entry.get('text')
        if not row or event.get('revision') != row['revision'] or not isinstance(reply, str):
            continue
        approved = row['publicSummaries']
        if not approved or len(approved) != row['expected'] or not all(approved):
            continue
        lines = [line.removeprefix('- ').strip() for line in reply.splitlines()]
        matched = list(dict.fromkeys(line for line in lines if line and line in approved))
        if matched:
            reused[row['id']] = ' '.join(matched)
    return reused


def render(snapshot: dict, page: str, *, limit: int, summaries: dict[str, str] | None = None) -> gmail.Message:
    if not page_url(page):
        raise ValueError('Use the HTTPS public ERP page')
    summaries = summaries or {}
    counts = Counter()
    for row in snapshot['sources']:
        outcome = row['outcome']
        if outcome in ('blocked', 'error', 'partial'):
            counts['실패'] += 1
        elif outcome in ('disabled', 'unverified'):
            counts['비활성' if outcome == 'disabled' else '미검증'] += 1
        else:
            counts['지연' if row['stale'] or outcome == 'busy' else '정상'] += 1
    subject = f'Lawbot ERP 일일 자료 알림 | {snapshot["dayKst"]} KST'
    text = [subject, ' · '.join(f'{state} {counts[state]}' for state in ('정상', '지연', '실패', '비활성', '미검증'))]
    if snapshot['status'] == 'partial':
        text.append('일부 출처의 확인이 끝나지 않았습니다.')
    sections = [f'<h1>{escape(subject)}</h1>', *(f'<p>{escape(line)}</p>' for line in text[1:])]

    def section(title: str, rows: list[tuple[str, list[str], list[tuple[str, str]]]]) -> None:
        if not rows:
            return
        text.extend(('', title))
        sections.append(f'<h2>{escape(title)}</h2><ul>')
        for label, details, links in rows[:limit]:
            text.extend((label, *details, *(f'{name} {url}' for name, url in links)))
            html_links = [f'<a href="{escape(url)}">{escape(name)}</a>' for name, url in links]
            content = '<br>'.join([f'<strong>{escape(label)}</strong>', *map(escape, details), *html_links])
            sections.append(f'<li>{content}</li>')
        if len(rows) > limit:
            more = f'외 {len(rows) - limit}건'
            text.append(more)
            sections.append(f'<li>{more}</li>')
        sections.append('</ul>')

    def link(url: str | None, label: str = '공식 원문') -> list[tuple[str, str]]:
        return [(label, url)] if url and public_link(url) else []

    recent, future, cards = [], [], []
    for row in snapshot['changes']:
        title, kind = row['title'], row['kind']
        if kind == 'scheduled':
            version, old = row['version'], row.get('predecessor')
            details = [
                f'시행일 {version["effectiveFrom"]}',
                f'적용 시작 {version["appliesFrom"]} · {APPLIES.get(row.get("applicability"), "적용 기준")} · {row["scope"]}',
            ]
            if old:
                details.append(f'기존 승인 내용 {old["valueText"]}')
            details.append(f'예정 승인 내용 {version["valueText"]}')
            links = link(version['evidenceUrl'], '예정 공식 원문')
            if old:
                links += link(old['evidenceUrl'], '기존 공식 원문')
            future.append((title, details, links))
            continue
        label = '새 글' if row['change'] == 'new' else '수정·정정'
        if row.get('event') == 'attachment_changed' or row.get('attachmentChanged'):
            label = '첨부 변경'
        details = [f'게시·공포일 {row["publishedOn"]}'] if row.get('publishedOn') else []
        if kind == 'news':
            agency = {'moel': '고용노동부', 'mofe': '재정경제부', 'nts': '국세청'}.get(
                row.get('sourceId', '').split('-')[0]
            )
            if agency:
                label = f'{label} · {agency}'
            details.append('내용 미확인')
        elif kind == 'law':
            fallback = [line for line in row['publicSummaries'] if line]
            details += [summaries[row['id']]] if row['id'] in summaries else fallback
            details += [f'시행일 {day}' for day in sorted(set(row.get('dates', [])) - {None})]
        else:
            excerpt = summaries.get(row['id']) or row.get('excerpt')
            if excerpt:
                details.append(excerpt[:600])
        links = link(row.get('url'))
        links.extend(entry for item in row.get('attachments', []) for entry in link(item.get('pdfUrl'), item['title']))
        recent.append((f'{label} · {title}', details, links))
    for row in snapshot['feeds']['erp'].get('items', []):
        details = [f'시행일 {row["effectiveFrom"]}'] if row.get('effectiveFrom') else []
        if row.get('excerpt'):
            details.append(row['excerpt'][:300])
        cards.append((row['title'], details, link(row.get('sourceUrl'))))
    for rule in snapshot['feeds'].get('resolvedRules', []):
        for scope in rule['scopes']:
            details = []
            links = []
            if current := scope['currentVersion']:
                details.append(f'현행 · {current["valueText"][:300]}')
                links += link(current['evidenceUrl'], '현행 공식 원문')
            if scope['scheduledChanges']:
                next_version = scope['scheduledChanges'][0]
                details.append(
                    f'변경 예정 · 적용 시작 {next_version["appliesFrom"]} · {next_version["valueText"][:300]}'
                )
                links += link(next_version['evidenceUrl'], '예정 공식 원문')
            if scope['recheckRequired']:
                details.append('재확인 필요')
            if details:
                cards.append((f'{rule["title"]} · {scope["scope"]}', details, links))
    if not recent and not future:
        notice = '확인된 신규·수정 자료 없음' if snapshot['status'] == 'partial' else '신규·수정 자료 없음'
        text.append(notice)
        sections.append(f'<p>{notice}</p>')
    section('신규·수정 자료', recent)
    section('향후 적용 변경', future)
    section('공개 ERP 자료', cards)
    text.extend(('', f'ERP 자료 전체 보기 {page}'))
    sections.append(f'<p><a href="{escape(page)}">ERP 자료 전체 보기</a></p>')
    return gmail.Message(subject, '\n'.join(text), '\n'.join(sections))


def _database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            raise ValueError('ERP email outbox must be private (0600)')
    finally:
        os.close(fd)
    conn = sqlite3.connect(path, timeout=5)
    try:
        with conn:
            app_id = conn.execute('PRAGMA application_id').fetchone()[0]
            if not app_id and not conn.execute("SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchone():
                conn.execute(f'PRAGMA application_id={APPLICATION}')
            elif app_id != APPLICATION:
                raise ValueError('Use a dedicated ERP email outbox database')
            conn.execute(SCHEMA)
            conn.execute(ENRICHMENT_SCHEMA)
            conn.execute(BODY_SCHEMA)
        return conn
    except ValueError, sqlite3.Error:
        conn.close()
        raise


def deliver(
    public: Path,
    database: Path,
    lock: Path,
    settings: Settings,
    *,
    now: datetime,
    send: Callable[[bytes], gmail.Result],
    monitor_home: Path | None = None,
    enrich: Callable[[dict], dict[str, str]] | None = None,
) -> dict:
    """Pin the current publication, persist exact messages, and record each provider outcome."""
    if public.resolve() == database.resolve():
        raise ValueError('Use an outbox separate from the public snapshot')
    now = now.astimezone(KST)
    day = now.date().isoformat()
    if now.time() < settings.at:
        return {'status': 'not_due'}
    with file_lock(lock) as acquired:
        if not acquired:
            return {'status': 'busy'}
        with file_lock(database.with_name(database.name + '.lock')) as outbox_locked:
            if not outbox_locked:
                return {'status': 'busy'}
            try:
                snapshot = read_snapshot(public)
            except FileNotFoundError:
                return {'status': 'awaiting_snapshot'}
            if snapshot['dayKst'] != day or datetime.fromisoformat(snapshot['cutoffAt']) > now:
                return {'status': 'awaiting_snapshot'}
            with closing(_database(database)) as conn:
                return _send_day(conn, snapshot, settings, now, send, monitor_home, enrich)


def _body(conn, snapshot, settings, monitor_home, enrich) -> gmail.Message:
    key = (snapshot['dayKst'], snapshot['snapshotHash'])
    saved = conn.execute('SELECT subject,text,html FROM bodies WHERE day=? AND snapshot=?', key).fetchone()
    if saved:
        return gmail.Message(*saved)
    summaries = {}
    if monitor_home is not None:
        summaries = read_morning(snapshot, monitor_home)
    if enrich is not None:
        with conn:
            claim = conn.execute('INSERT OR IGNORE INTO enrichment VALUES (?)', key[:1]).rowcount
        if claim:
            summaries.update(enrich(snapshot))
    body = render(snapshot, settings.page, limit=settings.limit, summaries=summaries)
    with conn:
        conn.execute('INSERT INTO bodies VALUES (?,?,?,?,?)', (*key, *body))
    return body


def read_morning(snapshot: dict, monitor_home: Path) -> dict[str, str]:
    try:
        path = morning_path(monitor_home, snapshot['runId'])
    except ValueError:
        return {}
    return verified_morning(snapshot, morning_entries(path, snapshot['runId']))


def _send_day(conn, snapshot, settings, now, send, monitor_home, enrich) -> dict:
    day, digest = snapshot['dayKst'], snapshot['snapshotHash']
    history = conn.execute(
        "SELECT snapshot,recipient,state,mime,attempts,attempted_at FROM deliveries WHERE day=? AND channel='gmail'",
        (day,),
    ).fetchall()
    old = {}
    for snapshot_id, recipient, state, *_ in history:
        if snapshot_id != digest and old.get(recipient) != 'accepted':
            old[recipient] = state
    current = {row[1]: row for row in history if row[0] == digest}
    deliveries = {}
    with conn:
        conn.execute("UPDATE deliveries SET state='unknown' WHERE day<=? AND state='sending'", (day,))
        conn.execute(
            "UPDATE deliveries SET state='superseded' WHERE day=? AND snapshot!=? AND state IN ('pending','retryable')",
            (day, digest),
        )
        conn.execute(
            "UPDATE deliveries SET state='superseded' WHERE day<? AND state IN ('pending','retryable')", (day,)
        )
    if not history and not snapshot['changes'] and snapshot['status'] == 'ok' and not settings.send_unchanged:
        return {'status': 'no_changes'}
    body = None
    for alias, address in settings.recipients.items():
        # The address hash also prevents aliases renamed during the day from receiving duplicates.
        recipient = hashlib.sha256(address.lower().encode()).hexdigest()
        if recipient in old:
            deliveries[alias] = 'already_sent' if old[recipient] == 'accepted' else 'awaiting_review'
            continue
        key = (day, digest, recipient)
        if recipient not in current:
            if body is None:
                body = _body(conn, snapshot, settings, monitor_home, enrich)
            mime = gmail.message(body, settings.sender, address, digest)
            with conn:
                conn.execute("INSERT INTO deliveries VALUES (?,?,'gmail',?,'pending',?,0,NULL,NULL)", (*key, mime))
            state, attempts, previous = 'pending', 0, None
        else:
            _, _, state, mime, attempts, previous = current[recipient]
        if state == 'sending':
            state = 'unknown'
        if state == 'retryable' and previous and now - datetime.fromisoformat(previous) < RETRY_INTERVAL:
            deliveries[alias] = state
            continue
        if state not in ('pending', 'retryable'):
            deliveries[alias] = state
            continue
        with conn:
            conn.execute(
                "UPDATE deliveries SET state='sending',attempts=?,attempted_at=? WHERE day=? AND snapshot=? AND recipient=? AND channel='gmail'",
                (attempts + 1, now.isoformat(), *key),
            )
        result = send(mime)
        state = 'failed' if result.status == 'retryable' and attempts >= 2 else result.status
        with conn:
            conn.execute(
                "UPDATE deliveries SET state=?,provider_id=? WHERE day=? AND snapshot=? AND recipient=? AND channel='gmail'",
                (state, result.provider_id, *key),
            )
        deliveries[alias] = state
    return {'status': 'processed', 'snapshot_hash': digest, 'deliveries': deliveries}
