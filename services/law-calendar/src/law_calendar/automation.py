"""Resolve explicit effective dates; never certify Git versions as legal before/after."""

import json
import re
import sqlite3
from datetime import UTC, datetime

from law_calendar.validation import civil_date, official_url

RULE_VERSION = 'effective-date-v1'
REASONS = {
    'delegated': '대통령령 등 별도로 정하는 시행일을 확인해야 합니다.',
    'complex': '부칙의 예외·적용례·경과조치를 추가 확인해야 합니다.',
    'missing': '조문 전체의 시행일을 확정할 근거가 부족합니다.',
    'conflict': '시행일 근거가 상충하거나 날짜 형식을 확인해야 합니다.',
    'source': '공식 출처와 공포일을 확인해야 합니다.',
}


def addendum(raw: dict) -> str:
    context = raw.get('context', {})
    added = context.get('addenda_added') or []
    if added:
        return '\n'.join(added)
    amendment = context.get('amendment_text') or ''
    parts = re.split(r'(?:^|\n)\s*부칙[^\n]*\n', amendment)
    return parts[-1] if len(parts) > 1 else ''


def explicit_date(text: str, promulgated: str) -> str | None:
    text = re.sub(r'^부칙[^\n]*\n', '', text.strip())
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^제1조\s*\(시행일\)\s*', '', text)
    if re.fullmatch(r'이 (?:법|영|규칙)은 공포한 날부터 시행한다\.', text):
        return promulgated
    match = re.fullmatch(r'이 (?:법|영|규칙)은 (\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일부터 시행한다\.', text)
    if match:
        return civil_date(f'{int(match[1]):04}-{int(match[2]):02}-{int(match[3]):02}')
    return None


def resolve_date(raw: dict, article: str) -> tuple[str | None, str]:
    try:
        official_url(raw.get('context', {}).get('source_url') or '')
        promulgated = civil_date(raw.get('dates', {}).get('promulgation') or '')
    except ValueError:
        return None, 'source'
    added = raw.get('context', {}).get('addenda_added') or []
    if not isinstance(added, list) or len(added) > 1 or any(not isinstance(item, str) for item in added):
        return None, 'complex'
    text = addendum(raw)
    if re.search(r'(?:대통령령|부령|조례|규칙)으로\s*정하|(?:따로|별도로)\s*정하|정하는\s*날', text):
        return None, 'delegated'
    if re.search(r'다만|경과조치|적용례|적용에 관한|제외하고', text):
        return None, 'complex'
    metadata = (raw.get('context', {}).get('lawgo') or {}).get('article_effective') or ''
    if not isinstance(metadata, str):
        return None, 'conflict'
    entries = re.findall(r'(\d{8}):([^:]*?)(?=\d{8}:|$)', metadata)
    days = set()
    partial = False
    try:
        for day, labels in entries:
            for label in labels.split(','):
                label = label.strip()
                if label.startswith(article) and label != article and not label.startswith(article + '의'):
                    partial = True
                if label == article:
                    days.add(civil_date(f'{day[:4]}-{day[4:6]}-{day[6:]}'))
        simple = explicit_date(text, promulgated)
    except ValueError:
        return None, 'conflict'
    if text.strip() and simple is None:
        return None, 'complex'
    if partial:
        return None, 'complex'
    if len(days) > 1 or (days and simple and simple not in days):
        return None, 'conflict'
    if days:
        return next(iter(days)), 'article_metadata'
    # Unparsed special dates may override a document-level rule for this article.
    if metadata:
        return None, 'missing'
    if simple:
        return simple, 'simple_addendum'
    return None, 'missing'


def apply_automation(conn: sqlite3.Connection, event_id: str, snapshot_id: str, raw: dict) -> None:
    rows = conn.execute(
        'SELECT c.* FROM article_changes c JOIN change_versions v ON v.change_id=c.id '
        "WHERE c.event_id=? AND v.snapshot_id=? AND c.review_state='pending'",
        (event_id, snapshot_id),
    ).fetchall()
    now = datetime.now(UTC).isoformat(timespec='seconds')
    published = []
    for row in rows:
        day, reason = resolve_date(raw, row['article'])
        if day is None:
            conn.execute(
                'INSERT INTO held_changes VALUES (?,?,?,?) ON CONFLICT(change_id) DO UPDATE SET '
                'snapshot_id=excluded.snapshot_id,reason=excluded.reason,rule_version=excluded.rule_version',
                (row['id'], snapshot_id, reason, RULE_VERSION),
            )
        else:
            kind = '신설' if row['before_text'] is None else '삭제' if row['after_text'] is None else '변경'
            conn.execute(
                "UPDATE article_changes SET effective_on=?,summary=?,review_state='approved',"
                'verified_at=?,comparable=0 WHERE id=?',
                (day, f'{row["article"]} {kind} · 시행일 자동 확인', now, row['id']),
            )
            published.append({'id': row['id'], 'effective_on': day, 'basis': reason})
    if published:
        conn.execute(
            "UPDATE change_events SET review_state='approved',status='시행일 자동 확인 · 비교 미확인',"
            'verified_at=?,promulgated_on=? WHERE id=?',
            (now, raw['dates']['promulgation'], event_id),
        )
        audit = conn.execute(
            'INSERT INTO review_audit(event_id,decision,reviewer,reviewed_at,note) VALUES (?,?,?,?,?)',
            (event_id, 'auto_publish', RULE_VERSION, now, 'Effective dates only; comparison unverified'),
        )
        conn.execute(
            'INSERT INTO review_details VALUES (?,?,?)',
            (audit.lastrowid, snapshot_id, json.dumps({'rule': RULE_VERSION, 'changes': published})),
        )
        conn.execute(
            'INSERT INTO collection_status(id,verified_at) VALUES (1,?) '
            'ON CONFLICT(id) DO UPDATE SET verified_at=excluded.verified_at',
            (now,),
        )
