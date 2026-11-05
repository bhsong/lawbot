"""Compare committed Assembly observations with complete, independently collected feeds."""

import hashlib
import json
import unicodedata

from law_monitor.assembly import (
    ACTIVE,
    BILLS,
    CLOSED,
    SOURCES,
    AssemblyClient,
    AssemblyError,
    civil_date,
    official_identity,
)
from law_monitor.collectors.base import CollectResult, RunContext
from law_monitor.events import _write_atomic, make_event_id
from law_monitor.state import ItemState, State

BILL_DATES = {
    'PROPOSE_DT': 'bill_proposed',
    'COMMITTEE_DT': 'committee_referred',
    'COMMITTEE_PROC_DT': 'committee_reviewed',
    'CMT_PRESENT_DT': 'committee_presented',
    'CMT_PROC_DT': 'committee_decided',
    'LAW_SUBMIT_DT': 'judiciary_referred',
    'LAW_PRESENT_DT': 'judiciary_presented',
    'LAW_PROC_DT': 'judiciary_decided',
    'PROC_DT': 'bill_decided',
}
BILL_STATUS = ('PASS_GUBUN', 'CMT_PROC_RESULT_CD', 'LAW_PROC_RESULT_CD', 'PROC_RESULT_CD')
BILL_STAGES = (
    ('bill_decided', '본회의 의결'),
    ('judiciary_decided', '법사위 처리'),
    ('judiciary_presented', '법사위 상정'),
    ('judiciary_referred', '법사위 회부'),
    ('committee_decided', '소관위 처리'),
    ('committee_reviewed', '위원회 심사 처리'),
    ('committee_presented', '소관위 상정'),
    ('committee_referred', '소관위 회부'),
)


def normalized(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def encoded(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def field(row: dict, key: str, *, required: bool = False) -> str:
    value = row.get(key)
    if value is None and not required:
        return ''
    if not isinstance(value, str) or (required and not value.strip()):
        raise AssemblyError(f'Invalid Assembly field {key}')
    return ' '.join(value.split())


def dates_for(row: dict, names: dict[str, str]) -> dict[str, str]:
    dates = {}
    for source, target in names.items():
        value = field(row, source)
        if value:
            try:
                dates[target] = civil_date(value)
            except ValueError:
                raise AssemblyError(f'Invalid Assembly date {source}') from None
    return dates


def bill_status(dates: dict[str, str], fields: dict[str, str]) -> str:
    if fields['PROC_RESULT_CD']:
        return fields['PROC_RESULT_CD']
    stage = next((label for role, label in BILL_STAGES if role in dates), '')
    return ' · '.join(filter(None, (fields['PASS_GUBUN'], stage))) or '상태 확인 필요'


class AssemblyCollector:
    isolated_baseline = True

    def __init__(self, name: str, client: AssemblyClient):
        if name not in SOURCES:
            raise ValueError('Unknown Assembly collector')
        self.name, self.client = name, client

    def has_baseline(self, state: State) -> bool:
        return state.baseline(self.name) is not None

    def _observations(self, age: int, keywords: list[str]) -> dict[str, dict]:
        snapshots = {}
        codes = (ACTIVE, CLOSED) if self.name == 'assembly_notice' else (BILLS,)
        for code in codes:
            seen = set()
            for row in self.client.pages(code, age):
                title = field(row, 'BILL_NAME', required=True)
                bill_id = field(row, 'BILL_ID', required=True)
                bill_no = field(row, 'BILL_NO', required=True)
                row_age = field(row, 'AGE', required=True)
                url = field(row, 'LINK_URL', required=True)
                try:
                    item_id = official_identity(url, notice=code != BILLS)
                except ValueError:
                    raise AssemblyError('Invalid Assembly source URL') from None
                if (code == BILLS and item_id != bill_id) or item_id in seen:
                    raise AssemblyError('Repeated page or conflicting Assembly identity')
                seen.add(item_id)
                if row_age != str(age) or not any(word in normalized(title) for word in keywords):
                    continue
                if item_id in snapshots:
                    raise AssemblyError('Notice is in both active and closed lists')
                dates = dates_for(row, BILL_DATES if code == BILLS else {'NOTI_ED_DT': 'notice_end'})
                status_fields = {key: field(row, key) for key in BILL_STATUS} if code == BILLS else {}
                status = bill_status(dates, status_fields) if code == BILLS else '진행 중' if code == ACTIVE else '종료'
                snapshots[item_id] = {
                    'target': {
                        'item_id': item_id,
                        'bill_id': bill_id,
                        'bill_no': bill_no,
                        'law_name': title,
                        'agency': field(row, 'CURR_COMMITTEE') or None,
                        'committee_id': field(row, 'CURR_COMMITTEE_ID') or None,
                        'age': age,
                    },
                    'dates': dates,
                    'context': {
                        'source_url': url,
                        'endpoint': code,
                        'status': status,
                        'status_fields': status_fields,
                        'date_sources': {role: code for role in dates},
                        'schema_version': 1,
                    },
                }
        return snapshots

    def _event_type(self, before: dict | None, after: dict, revision: int) -> str:
        if self.name == 'assembly_bill':
            return 'assembly_bill_new' if before is None else 'assembly_bill_changed'
        code = after['context']['endpoint']
        previous = before['context']['endpoint'] if before else None
        if code == CLOSED and previous != CLOSED:
            return 'assembly_notice_closed'
        if code == ACTIVE and (previous != ACTIVE or revision == 0):
            return 'assembly_notice_observed'
        return 'assembly_notice_changed'

    def collect(self, ctx: RunContext) -> CollectResult:
        keywords = sorted({normalized(word) for word in ctx.config.prelegislation_keywords})
        age = ctx.config.assembly_age
        scope = digest(encoded({'age': age, 'keywords': keywords}))
        bootstrap = ctx.mode == 'bootstrap'
        if not bootstrap and ctx.state.baseline(self.name) != scope:
            raise AssemblyError('Assembly baseline is missing or scope changed; run bootstrap for this source')
        self.client.begin(40 if self.name == 'assembly_bill' else 50)
        snapshots = self._observations(age, keywords)
        existing = ctx.state.items(self.name)
        result = CollectResult(baselines={self.name: scope} if bootstrap else {})
        day = ctx.now().date().isoformat()
        order = sorted(snapshots, key=lambda item_id: digest(f'{day}:{item_id}'))
        for item_id in order:
            after = snapshots[item_id]
            old = existing.get(item_id)
            revision = int(old.version_key) if old else 0
            before = json.loads((ctx.paths.home / old.file_path).read_text(encoding='utf-8')) if old else None
            if self.name == 'assembly_notice':
                if before and before['target']['bill_id'] != after['target']['bill_id']:
                    raise AssemblyError('Notice identity was assigned to a different bill')
                if not bootstrap or revision > 0:
                    self._add_notice_start(after, before, revision)
            payload = encoded(after)
            content_hash = digest(payload)
            initial_active = not bootstrap and revision == 0 and after['context']['endpoint'] == ACTIVE
            if old and old.content_hash == content_hash and not initial_active:
                continue
            path = ctx.paths.files / f'assembly-{content_hash}.json'
            if not path.exists():
                _write_atomic(path, payload)
            new_revision = revision if bootstrap else revision + 1
            result.items.append(ItemState(self.name, item_id, str(new_revision), content_hash, ctx.paths.rel(path)))
            if not bootstrap:
                result.events.append(
                    {
                        'event_id': make_event_id(self.name, item_id, f'{new_revision}:{content_hash}'),
                        'detected_at': ctx.now().isoformat(timespec='seconds'),
                        'category': 4,
                        'source': self.name,
                        'change_type': self._event_type(before, after, revision),
                        **after,
                        'old': {'version_key': old.version_key, 'file': old.file_path} if old else None,
                        'new': {'version_key': str(new_revision), 'file': ctx.paths.rel(path)},
                        'diff': {'kind': 'none', 'path': None, 'chunks': []},
                    }
                )
        return result

    def _add_notice_start(self, after: dict, before: dict | None, revision: int) -> None:
        dates, context = after['dates'], after['context']
        end = dates.get('notice_end')
        if not end:
            return
        previous = before['dates'] if before else {}
        start = previous.get('notice_start') if previous.get('notice_end') == end else None
        if not start:
            # Historical closed notices are baseline only. Retry missing dates for current and newly changed notices.
            if context['endpoint'] == CLOSED and revision == 0 and before == after:
                return
            start = self.client.notice_start(context['source_url'], after['target']['bill_no'], end)
        if start:
            dates['notice_start'] = start
            context['date_sources']['notice_start'] = 'pal.assembly.go.kr'
