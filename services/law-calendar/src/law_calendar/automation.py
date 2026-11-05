"""Resolve explicit effective dates; never certify Git versions as legal before/after."""

import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from law_calendar.article_fragments import Fragment, partition_changes
from law_calendar.effective_clause import ARTICLE, EffectiveSchedule, parse_schedule
from law_calendar.source_addenda import borrowed_clause
from law_calendar.validation import civil_date, official_url

RULE_VERSION = 'effective-date-v4'
REASONS = {
    'delegated': '대통령령 등 별도로 정하는 시행일을 확인해야 합니다.',
    'complex': '부칙의 예외·적용례·경과조치를 추가 확인해야 합니다.',
    'partial': '항·호별 시행일과 실제 변경 범위를 함께 확인해야 합니다.',
    'missing': '조문 시행일을 확정할 근거가 부족합니다.',
    'conflict': '시행일 근거가 상충하거나 날짜 형식을 확인해야 합니다.',
    'source': '공식 출처와 공포일을 확인해야 합니다.',
}
METADATA_ENTRY = re.compile(r'(\d{8}):([^:]*?)(?=\d{8}:|$)')
METADATA_SCOPE = re.compile(rf'({ARTICLE})(?:제\d+[항호](?:의\d+)*|[가-힣]목)*')
DateEvidence = tuple[EffectiveSchedule | None, str]


@dataclass(frozen=True, slots=True)
class DatedChange:
    article: str
    before: str | None
    after: str | None
    day: str | None
    basis: str
    note: str = ''


def read_schedule(raw: dict, article_order: Sequence[str], copied_clause: str = '') -> DateEvidence:
    context = raw.get('context', {})
    try:
        official_url(context.get('source_url') or '')
        promulgated = civil_date(raw.get('dates', {}).get('promulgation') or '')
    except TypeError, ValueError:
        return None, 'source'
    added = context.get('addenda_added') or []
    if not isinstance(added, list) or len(added) > 1 or any(not isinstance(item, str) for item in added):
        return None, 'complex'
    if added:
        text = added[0]
    else:
        amendment = context.get('amendment_text') or ''
        if not isinstance(amendment, str):
            return None, 'complex'
        parts = re.split(r'(?:^|\n)\s*부칙[^\n]*\n', amendment)
        text = parts[-1] if len(parts) > 1 else ''
    schedule, reason = parse_schedule(text or copied_clause, promulgated, article_order)
    return schedule, 'borrowed_addendum' if schedule and copied_clause and not text else reason


def _metadata(raw: dict):
    return (raw.get('context', {}).get('lawgo') or {}).get('article_effective') or ''


def _metadata_day(day: str) -> str:
    return civil_date(f'{day[:4]}-{day[4:6]}-{day[6:]}')


def resolve_date(
    raw: dict, article: str, article_order: Sequence[str] = (), evidence: DateEvidence | None = None
) -> tuple[str | None, str]:
    schedule, reason = evidence if evidence is not None else read_schedule(raw, article_order)
    if schedule:
        addendum_day, basis = schedule.date_for(article)
        if addendum_day is None:
            return None, basis
        if reason in {'simple_addendum', 'borrowed_addendum'}:
            basis = reason
    elif reason == 'missing':
        addendum_day, basis = None, reason
    else:
        return None, reason
    metadata = _metadata(raw)
    if not isinstance(metadata, str):
        return None, 'conflict'
    days = set()
    try:
        for day, labels in METADATA_ENTRY.findall(metadata):
            for label in labels.split(','):
                label = label.strip()
                if label.startswith(article) and label != article and not label.startswith(article + '의'):
                    return None, 'complex'
                if label == article:
                    days.add(_metadata_day(day))
    except ValueError:
        return None, 'conflict'
    if len(days) > 1 or (days and addendum_day and addendum_day not in days):
        return None, 'conflict'
    if days:
        return next(iter(days)), 'article_metadata'
    if metadata:
        return None, 'missing'
    return addendum_day, basis


def _scope_metadata_agrees(raw: dict, article: str, scopes: dict[str, str], days: set[str]) -> bool:
    metadata = _metadata(raw)
    if not metadata:
        return True
    if not isinstance(metadata, str) or not re.match(r'^\d{8}:', metadata):
        return False
    entries = METADATA_ENTRY.findall(metadata)
    if ''.join(f'{day}:{labels}' for day, labels in entries) != metadata:
        return False
    try:
        for day, labels in entries:
            value = _metadata_day(day)
            for label in labels.split(','):
                label = label.strip()
                match = METADATA_SCOPE.fullmatch(label)
                if not match:
                    return False
                if match[1] == article and (days != {value} if label == article else scopes.get(label) != value):
                    return False
    except ValueError:
        return False
    return True


def _note(schedule: EffectiveSchedule | None, article: str) -> str:
    if not schedule:
        return ''
    for clause in schedule.related_clauses:
        title, _, body = clause.partition(')')
        if re.search(rf'{re.escape(article)}(?!의\d)', body):
            if '적용례' in title:
                return ' · 부칙 적용례 있음'
            if '경과조치' in title:
                return ' · 부칙 경과조치 있음'
    return ''


def _join_fragments(fragments: list[Fragment], side: int) -> str | None:
    return ''.join(text for fragment in fragments if (text := fragment[side]) is not None) or None


def _fragment_order(fragment: Fragment) -> tuple[int, int]:
    paragraph = re.search(r'제(\d+)항', fragment[0])
    item = re.search(r'제(\d+)호', fragment[0])
    return (int(paragraph[1]) if paragraph else 0, int(item[1]) if item else 0)


def resolve_changes(
    raw: dict,
    article: str,
    before: str | None,
    after: str | None,
    article_order: Sequence[str],
    evidence: DateEvidence,
    new_document: str,
) -> list[DatedChange]:
    if evidence == (None, 'missing') and new_document:
        copied = borrowed_clause(new_document, raw['target']['law_name'], article, raw['dates']['promulgation'])
        if copied:
            evidence = read_schedule(raw, article_order, copied)
    schedule, _ = evidence
    day, basis = resolve_date(raw, article, article_order, evidence)
    note = _note(schedule, article)
    whole = DatedChange(article, before, after, day, basis, note)
    if basis != 'partial' or not schedule or before is None or after is None:
        return [whole]

    scopes = schedule.scopes_for(article)
    groups = partition_changes(before, after, [scope[len(article) :] for scope in scopes])
    if not groups:
        return [whole]
    by_day: dict[str, list[tuple[str, Fragment]]] = defaultdict(list)
    for scope, fragments in groups.items():
        effective_on = schedule.default_on if scope is None else scopes[article + scope]
        for fragment in fragments:
            path = fragment[0]
            label = scope or (f'{path} 본문' if path.endswith('항') else path or '본문')
            by_day[effective_on].append((label, fragment))
            if path:
                scopes[article + path] = effective_on
    if not _scope_metadata_agrees(raw, article, scopes, set(by_day)):
        return [DatedChange(article, before, after, None, 'conflict', note)]
    changes = []
    for effective_on, items in sorted(by_day.items()):
        items.sort(key=lambda item: _fragment_order(item[1]))
        label = '·'.join(dict.fromkeys(scope for scope, _ in items))
        fragments = [fragment for _, fragment in items]
        changes.append(
            DatedChange(
                f'{article} {label}',
                _join_fragments(fragments, 1),
                _join_fragments(fragments, 2),
                effective_on,
                'scoped_fragment',
                note,
            )
        )
    return changes


def article_kind(before: str | None, after: str | None) -> str:
    if before is None:
        return '신설'
    if after is None or re.fullmatch(r'삭제(?:\s*<\d{4}\.\d{1,2}\.\d{1,2}\.?\s*>)?', after.partition('\n')[2].strip()):
        return '삭제'
    return '변경'


def apply_automation(
    conn: sqlite3.Connection,
    event_id: str,
    snapshot_id: str,
    raw: dict,
    changes: dict[str, DatedChange],
) -> None:
    now = datetime.now(UTC).isoformat(timespec='seconds')
    published = []
    for change_id, change in changes.items():
        if change.day is None:
            conn.execute(
                'INSERT INTO held_changes VALUES (?,?,?,?) ON CONFLICT(change_id) DO UPDATE SET '
                'snapshot_id=excluded.snapshot_id,reason=excluded.reason,rule_version=excluded.rule_version',
                (change_id, snapshot_id, change.basis, RULE_VERSION),
            )
        else:
            kind = article_kind(change.before, change.after)
            conn.execute(
                "UPDATE article_changes SET effective_on=?,summary=?,review_state='approved',"
                'verified_at=?,comparable=0 WHERE id=?',
                (change.day, f'{change.article} {kind} · 시행일 자동 확인{change.note}', now, change_id),
            )
            published.append({'id': change_id, 'effective_on': change.day, 'basis': change.basis})
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
