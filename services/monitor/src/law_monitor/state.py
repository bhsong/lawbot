"""SQLite 상태 저장소 (원 계획서 5.3). 한 실행의 커서·항목·이벤트·실행 기록은 apply() 한 트랜잭션으로 저장한다."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
create table if not exists git_cursor (
    repo text primary key not null,
    last_commit text not null,
    updated_at text
);
create table if not exists items (
    source text not null,
    item_id text not null,
    version_key text not null,
    content_hash text,
    file_path text,
    seen_at text,
    primary key (source, item_id)
);
create table if not exists seen_posts (
    source text not null,
    post_id text not null,
    first_seen_at text,
    primary key (source, post_id)
);
create table if not exists events (
    event_id text primary key not null,
    source text,
    change_type text,
    category integer,
    json_path text,
    created_at text not null,
    processed_at text
);
create table if not exists runs (
    run_id text primary key not null,
    mode text,
    started_at text,
    finished_at text,
    status text,
    summary_json text
);
"""


@dataclass(frozen=True)
class ItemState:
    source: str
    item_id: str
    version_key: str  # git: 파일 blob 해시
    content_hash: str
    file_path: str  # 저장소 안 경로


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def cursor(self, repo: str) -> str | None:
        row = self.conn.execute('select last_commit from git_cursor where repo = ?', (repo,)).fetchone()
        return row[0] if row else None

    def items(self, source: str) -> dict[str, ItemState]:
        rows = self.conn.execute(
            'select source, item_id, version_key, content_hash, file_path from items where source = ?', (source,)
        )
        return {r['item_id']: ItemState(*r) for r in rows}

    def event_exists(self, event_id: str) -> bool:
        return self.conn.execute('select 1 from events where event_id = ?', (event_id,)).fetchone() is not None

    def pending_events(self) -> list[tuple[str, str | None]]:
        """요약하지 않은 이벤트 (event_id, json_path), 저장 순서대로 (2단계 입력)."""
        rows = self.conn.execute(
            'select event_id, json_path from events where processed_at is null order by created_at, rowid'
        )
        return [(r[0], r[1]) for r in rows]

    def pending_count(self) -> int:
        return self.conn.execute('select count(*) from events where processed_at is null').fetchone()[0]

    def mark_processed(self, event_ids: list[str], now: str) -> None:
        with self.conn:
            self.conn.executemany(
                'update events set processed_at = ? where event_id = ? and processed_at is null',
                [(now, eid) for eid in event_ids],
            )

    def last_summary_started(self) -> str | None:
        """끝난 요약 실행 중 마지막 것의 시작 시각 (다음 요약의 감지 구간 시작)."""
        row = self.conn.execute(
            "select started_at from runs where mode = 'summarize' and finished_at is not null "
            'order by started_at desc, rowid desc limit 1'
        ).fetchone()
        return row[0] if row else None

    def first_run_started(self) -> str | None:
        """첫 수집 실행(보통 bootstrap) 시각."""
        row = self.conn.execute("select min(started_at) from runs where mode != 'summarize'").fetchone()
        return row[0] if row else None

    def finish_run(self, run_id: str, finished_at: str, status: str, summary_json: str) -> None:
        with self.conn:
            self.conn.execute(
                'update runs set finished_at = ?, status = ?, summary_json = ? where run_id = ?',
                (finished_at, status, summary_json, run_id),
            )

    def start_run(self, run_id: str, mode: str, started_at: str) -> None:
        with self.conn:
            self.conn.execute(
                "insert into runs (run_id, mode, started_at, status) values (?, ?, ?, 'running')",
                (run_id, mode, started_at),
            )

    def last_run(self) -> dict | None:
        row = self.conn.execute('select * from runs order by started_at desc, rowid desc limit 1').fetchone()
        return dict(row) if row else None

    def consecutive_failures(self, collector: str) -> int:
        """끝난 실행 중 이 수집기를 실행한 것만 최근부터 세어, 연속으로 실패한 횟수."""
        rows = self.conn.execute(
            'select summary_json from runs where finished_at is not null order by started_at desc, rowid desc'
        )
        count = 0
        for (summary,) in rows:
            entry = json.loads(summary or '{}').get('collectors', {}).get(collector)
            if entry is None:
                continue
            if entry.get('status') != 'failed':
                break
            count += 1
        return count

    def apply(
        self,
        *,
        cursors: dict[str, str],
        items: list[ItemState],
        removed: list[tuple[str, str]],
        events: list[dict],
        now: str,
        event_paths: dict[str, str] | None = None,
        replace_sources: list[str] | None = None,
        run: tuple[str, str, str, str] | None = None,
    ) -> None:
        """run = (run_id, finished_at, status, summary_json). 하나라도 실패하면 모두 되돌린다."""
        paths = event_paths or {}
        with self.conn:
            for source in replace_sources or []:
                self.conn.execute('delete from items where source = ?', (source,))
            for repo, commit in cursors.items():
                self.conn.execute(
                    'insert into git_cursor (repo, last_commit, updated_at) values (?, ?, ?) '
                    'on conflict(repo) do update set last_commit = excluded.last_commit, updated_at = excluded.updated_at',
                    (repo, commit, now),
                )
            for it in items:
                self.conn.execute(
                    'insert or replace into items (source, item_id, version_key, content_hash, file_path, seen_at) '
                    'values (?, ?, ?, ?, ?, ?)',
                    (it.source, it.item_id, it.version_key, it.content_hash, it.file_path, now),
                )
            for source, item_id in removed:
                self.conn.execute('delete from items where source = ? and item_id = ?', (source, item_id))
            for ev in events:
                self.conn.execute(
                    'insert into events (event_id, source, change_type, category, json_path, created_at) '
                    'values (?, ?, ?, ?, ?, ?) on conflict(event_id) do nothing',  # 중복만 건너뜀 (NOT NULL은 오류)
                    (
                        ev['event_id'],
                        ev.get('source'),
                        ev.get('change_type'),
                        ev.get('category'),
                        paths.get(ev['event_id']),
                        now,
                    ),
                )
            if run:
                run_id, finished_at, status, summary = run
                self.conn.execute(
                    'update runs set finished_at = ?, status = ?, summary_json = ? where run_id = ?',
                    (finished_at, status, summary, run_id),
                )
