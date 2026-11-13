"""Apply SQLite settings over the seed and audit each committed revision."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, model_validator

from lawbot_common.law_scope import load_law_scope
from source_hub.config import HOSTS, Config, Source, Target

KST = ZoneInfo('Asia/Seoul')
APPLICATION_ID = 0x4C534855
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS settings (kind TEXT, target TEXT, value TEXT NOT NULL, PRIMARY KEY(kind,target))',
    """CREATE TABLE IF NOT EXISTS audit (
        version INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, actor TEXT NOT NULL,
        reason TEXT NOT NULL, old TEXT, new TEXT NOT NULL, at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT NOT NULL, source_id TEXT NOT NULL, config_version INTEGER NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT NOT NULL, outcome TEXT NOT NULL,
        new_count INTEGER, modified_count INTEGER, error TEXT,
        PRIMARY KEY (source_id,run_id)
    )""",
    'CREATE INDEX IF NOT EXISTS runs_by_source ON runs(source_id,started_at,run_id)',
)
IMMUTABLE_SOURCE_FIELDS = {'source_id', 'group', 'agency', 'law_scope'}


class Conflict(ValueError):
    """The supplied revision is older than the current settings."""


class UnknownTarget(ValueError):
    """The setting target is missing."""


class Run(BaseModel):
    """Completed observation from an adapter. Errors use a bounded classification code."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    run_id: str = Field(pattern=r'^[A-Za-z0-9._-]{1,80}$')
    source_id: str
    config_version: int = Field(ge=0)
    started_at: AwareDatetime
    finished_at: AwareDatetime
    outcome: Literal['ok', 'partial', 'blocked', 'error']
    new_count: int | None = Field(default=None, ge=0)
    modified_count: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{1,47}$')

    @field_serializer('started_at', 'finished_at')
    def serialize_timestamp(self, value: datetime) -> str:
        return value.astimezone(KST).isoformat(timespec='seconds')

    @model_validator(mode='after')
    def validate_result(self):
        if self.finished_at < self.started_at:
            raise ValueError('완료 시각은 시작 시각 이후여야 합니다')
        if self.outcome == 'ok' and (self.new_count is None or self.modified_count is None or self.error):
            raise ValueError('정상 실행에는 신규·수정 건수가 필요합니다')
        if self.outcome != 'ok' and not self.error:
            raise ValueError('실패 분류 코드가 필요합니다')
        return self


class Registry:
    def __init__(self, config: Config, database: Path):
        self.config, self.database = config, database
        self.laws = {law.folder: law for law in load_law_scope()}
        self.law_parts = {f'{law.folder}/{part}' for law in self.laws.values() for part in ('법률', *law.decrees)}
        self.seed_hash = hashlib.sha256(config.model_dump_json().encode()).hexdigest()

    @contextmanager
    def _connect(self, *, write: bool = False):
        if not write and not self.database.exists():
            yield None
            return
        if write:
            self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        uri = self.database.resolve().as_uri() + ('?mode=rwc' if write else '?mode=ro')
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            application_id = conn.execute('PRAGMA application_id').fetchone()[0]
            if application_id == 0:
                if conn.execute('SELECT 1 FROM sqlite_schema LIMIT 1').fetchone() is not None:
                    raise ValueError('지정한 파일은 수집 설정 DB가 아닙니다')
                if not write:
                    yield None
                    return
                conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
                for statement in SCHEMA:
                    conn.execute(statement)
            elif application_id != APPLICATION_ID:
                raise ValueError('지정한 파일은 수집 설정 DB가 아닙니다')
            yield conn

    @staticmethod
    def _read(conn: sqlite3.Connection | None) -> tuple[int, dict[tuple[str, str], dict]]:
        if conn is None:
            return 0, {}
        version = conn.execute('SELECT COALESCE(MAX(version),0) FROM audit').fetchone()[0]
        return version, {
            (row['kind'], row['target']): json.loads(row['value']) for row in conn.execute('SELECT * FROM settings')
        }

    def _sources(self, settings: dict[tuple[str, str], dict]) -> dict[str, Source]:
        sources = {source.source_id: source for source in self.config.sources}
        for (kind, target), value in settings.items():
            if kind != 'source':
                continue
            seed = sources[target].model_dump() if target in sources else {}
            sources[target] = Source.model_validate(seed | value)
        return sources

    def snapshot(self) -> dict:
        """Read a coherent, serializable plan without creating a database or collecting anything."""
        with self._connect() as conn:
            version, settings = self._read(conn)
            latest, last_success = {}, {}
            if conn is not None:
                latest = {
                    row['source_id']: dict(row)
                    for row in conn.execute(
                        'SELECT * FROM runs r WHERE rowid = (SELECT rowid FROM runs '
                        'WHERE source_id=r.source_id ORDER BY started_at DESC,run_id DESC LIMIT 1)'
                    )
                }
                last_success = dict(
                    conn.execute("SELECT source_id,MAX(finished_at) FROM runs WHERE outcome='ok' GROUP BY source_id")
                )
        sources = []

        def enabled(kind: str, target: str) -> bool:
            return settings.get((kind, target), {}).get('enabled', True)

        for source in self._sources(settings).values():
            active = source.enabled and enabled('group', source.group) and enabled('agency', source.agency)
            laws = (
                [
                    {
                        'law_id': law.folder,
                        'name': law.name,
                        'enabled': enabled('law', law.folder),
                        'effective_enabled': active and enabled('law', law.folder),
                        'parts': [
                            {
                                'id': f'{law.folder}/{part}',
                                'name': part,
                                'enabled': enabled('law-part', f'{law.folder}/{part}'),
                                'effective_enabled': active
                                and enabled('law', law.folder)
                                and enabled('law-part', f'{law.folder}/{part}'),
                            }
                            for part in ('법률', *law.decrees)
                        ],
                    }
                    for law in self.laws.values()
                ]
                if source.law_scope
                else []
            )
            runnable = (
                active
                and source.verification == 'ready'
                and (not laws or any(part['effective_enabled'] for law in laws for part in law['parts']))
            )
            run = latest.get(source.source_id, {})
            next_run = (
                datetime.fromisoformat(run['started_at']) + timedelta(minutes=source.interval_minutes)
                if runnable and run
                else None
            )
            status = 'disabled'
            if runnable:
                status = run.get('outcome', source.verification)
            elif active and source.verification != 'ready':
                status = source.verification
            sources.append(
                {
                    **source.model_dump(),
                    'effective_enabled': active,
                    'runnable': runnable,
                    'status': status,
                    'last_attempt_at': run.get('started_at'),
                    'last_success_at': last_success.get(source.source_id),
                    'new_count': run.get('new_count'),
                    'modified_count': run.get('modified_count'),
                    'next_run_at': next_run.isoformat() if next_run else None,
                    'error': run.get('error'),
                    'laws': laws,
                }
            )
        return {
            'config_version': version,
            'seed_sha256': self.seed_hash,
            'groups': {group: enabled('group', group) for group in ('law', 'erp')},
            'agencies': {agency: enabled('agency', agency) for agency in HOSTS},
            'sources': sources,
        }

    def patch(
        self, kind: Target, target: str, changes: dict, *, actor: str, reason: str, version: int, create: bool = False
    ) -> int:
        """Validate and apply an administrator's change in the same transaction as its audit row."""
        if not actor.strip() or not reason.strip() or len(reason) > 500 or not changes:
            raise ValueError('변경 내용과 사유를 입력하세요')
        with self._connect(write=True) as conn:
            assert conn is not None
            current_version, settings = self._read(conn)
            if version != current_version:
                raise Conflict('설정이 갱신되었습니다. 목록을 다시 불러오세요')
            override = settings.get((kind, target), {})
            if kind == 'source':
                source = self._sources(settings).get(target)
                if create and source is not None:
                    raise Conflict('이미 등록된 source_id입니다')
                if source is None and not create:
                    raise UnknownTarget('등록되지 않은 수집원입니다')
                if 'source_id' in changes or (not create and IMMUTABLE_SOURCE_FIELDS & changes.keys()):
                    raise ValueError('수집원의 고정 필드를 변경할 수 없습니다')
                old = source.model_dump() if source else None
                if create:
                    new = Source.model_validate(
                        {'source_id': target, **changes, 'verification': 'not_tested'}
                    ).model_dump()
                    if new['adapter'] in {'legacy', 'local'}:
                        raise ValueError('기존 수집 어댑터는 초기 등록부의 고정 수집원에서 사용하세요')
                    value = new
                else:
                    assert old is not None
                    if {'source_url', 'mode', 'adapter', 'parser_version'} & changes.keys():
                        changes = {**changes, 'verification': 'not_tested'}
                    new = Source.model_validate(old | changes).model_dump()
                    value = override | {key: new[key] for key in changes}
            else:
                valid = {'group': {'law', 'erp'}, 'agency': HOSTS, 'law': self.laws, 'law-part': self.law_parts}
                if target not in valid[kind]:
                    raise UnknownTarget('등록되지 않은 설정 대상입니다')
                if set(changes) != {'enabled'} or type(changes['enabled']) is not bool:
                    raise ValueError('enabled에는 true 또는 false를 입력하세요')
                old = override or {'enabled': True}
                new = value = changes
            if old == new:
                return current_version
            conn.execute(
                'INSERT INTO settings VALUES (?,?,?) ON CONFLICT(kind,target) DO UPDATE SET value=excluded.value',
                (kind, target, json.dumps(value, ensure_ascii=False)),
            )
            conn.execute(
                'INSERT INTO audit VALUES (?,?,?,?,?,?,?,?)',
                (
                    version + 1,
                    kind,
                    target,
                    actor,
                    reason,
                    json.dumps(old, ensure_ascii=False),
                    json.dumps(new, ensure_ascii=False),
                    datetime.now(KST).isoformat(timespec='seconds'),
                ),
            )
        return version + 1

    def record_run(self, run: Run) -> None:
        """Store one result per source and run. Replays must carry the same result."""
        with self._connect(write=True) as conn:
            assert conn is not None
            version, settings = self._read(conn)
            if run.source_id not in self._sources(settings):
                raise UnknownTarget('등록되지 않은 수집원입니다')
            if run.config_version > version:
                raise ValueError('수집 설정 버전을 확인하세요')
            values = run.model_dump()
            previous = conn.execute(
                'SELECT * FROM runs WHERE source_id=? AND run_id=?', (run.source_id, run.run_id)
            ).fetchone()
            if previous is not None:
                if dict(previous) != values:
                    raise Conflict('기존 실행 결과와 일치하지 않습니다')
                return
            conn.execute(
                'INSERT INTO runs VALUES (:run_id,:source_id,:config_version,:started_at,:finished_at,:outcome,'
                ':new_count,:modified_count,:error)',
                values,
            )

    def runs(self, source_id: str) -> list[dict]:
        with self._connect() as conn:
            if conn is None:
                return []
            return [
                dict(row)
                for row in conn.execute(
                    'SELECT * FROM runs WHERE source_id=? ORDER BY started_at DESC,run_id DESC LIMIT 50', (source_id,)
                )
            ]

    def history(self) -> list[dict]:
        with self._connect() as conn:
            if conn is None:
                return []
            return [
                {**row, 'old': json.loads(row['old']), 'new': json.loads(row['new'])}
                for row in conn.execute('SELECT * FROM audit ORDER BY version DESC')
            ]
