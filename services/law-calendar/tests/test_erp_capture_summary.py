import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime

from law_calendar.db import migrate
from law_calendar.erp_capture import approved_law_changes
from source_hub.digest import render

STAMP = '2026-11-13T07:00:00+09:00'
START = datetime.fromisoformat('2026-11-13T06:00:00+09:00')
CUTOFF = datetime.fromisoformat('2026-11-13T08:00:00+09:00')


def test_capture_binds_public_law_rows_to_the_imported_monitor_revision(tmp_path):
    database = tmp_path / 'calendar.db'
    migrate(database)
    raw = '{"event_id":"upstream"}'
    with closing(sqlite3.connect(database)) as conn:
        with conn:
            conn.execute("INSERT INTO laws VALUES ('law','가상 법률')")
            conn.execute(
                """INSERT INTO change_events(id,law_id,kind,status,review_state,promulgated_on,source_url,collected_at,verified_at)
                   VALUES ('event','law','effective','public','approved','2026-11-13','https://www.law.go.kr/법령/가상',?,?)""",
                (STAMP, STAMP),
            )
            conn.execute("INSERT INTO source_snapshots VALUES ('snapshot','event',?,'{}','1',?,?)", (raw, STAMP, STAMP))
            conn.execute("INSERT INTO event_sources VALUES ('event','legalize_law','upstream','snapshot')")
            conn.execute(
                """INSERT INTO article_changes(id,event_id,article,effective_on,summary,comparable,review_state,verified_at)
                   VALUES ('change','event','제1조','2026-12-01','승인 문구',1,'approved',?),
                          ('pending','event','제2조',NULL,'비공개 문구',0,'pending',NULL)""",
                (STAMP,),
            )
            conn.execute("INSERT INTO change_versions VALUES ('change','snapshot',NULL,NULL)")
        assert approved_law_changes(database, START, CUTOFF, []) == []
        assert approved_law_changes(database, datetime.fromisoformat(STAMP), CUTOFF, ['가상 법률']) == []
        records = approved_law_changes(database, START, CUTOFF, ['가상 법률'])
        assert len(records) == 1
        assert records[0]['revision'] == hashlib.sha256(raw.encode()).hexdigest()
        assert records[0]['expected'] == 2 and records[0]['publicSummaries'] == ['승인 문구']
        assert '비공개 문구' not in str(records)
        mail = render(
            {'dayKst': '2026-11-13', 'status': 'ok', 'sources': [], 'changes': records, 'feeds': {'erp': {}}},
            'https://calendar.example/#erp',
            limit=20,
        )
        assert '승인 문구' in mail.text and records[0]['url'] in mail.text
        with conn:
            conn.execute("UPDATE source_snapshots SET imported_at='2026-11-12T07:00:00+09:00'")
        assert approved_law_changes(database, START, CUTOFF, ['가상 법률']) == records
        with conn:
            conn.execute("UPDATE change_events SET review_state='withdrawn' WHERE id='event'")
        assert approved_law_changes(database, START, CUTOFF, ['가상 법률']) == []
