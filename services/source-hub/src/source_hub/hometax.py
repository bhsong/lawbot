"""Record operator-confirmed Hometax notices without contacting Hometax."""

import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from source_hub import collector, materials
from source_hub.materials import Article
from source_hub.store import KST, Registry, Run

OFFICIAL_HOME = 'https://www.hometax.go.kr/'
SOURCE_IDS = ('hometax-media', 'hometax-specs', 'hometax-developers', 'hometax-errata')


def record_notice(
    registry: Registry, database: Path, source_id: str, notice_id: str, title: str, published_on: str | None
) -> dict:
    plan = registry.snapshot()
    source = next((row for row in plan['sources'] if row['source_id'] == source_id), None)
    if (
        source_id not in SOURCE_IDS
        or source is None
        or source['adapter'] != 'hometax'
        or source['group'] != 'erp'
        or source['official_homepage'] != OFFICIAL_HOME
    ):
        raise ValueError('공식 홈택스 수동 확인 대상인지 확인하세요')
    title = ' '.join(title.split())
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', notice_id) or not 1 <= len(title) <= 500:
        raise ValueError('확인한 게시물의 식별자와 제목을 입력하세요')
    if published_on is not None and date.fromisoformat(published_on).isoformat() != published_on:
        raise ValueError('확인된 게시일은 YYYY-MM-DD로 입력하세요')

    path = collector.materials_path(registry, database)
    with collector.collect_lock(registry) as locked:
        if not locked:
            return {'outcome': 'busy'}
        materials.initialize(path)
        now = datetime.now(KST)
        run = Run(
            run_id=uuid4().hex,
            source_id=source_id,
            config_version=plan['config_version'],
            started_at=now,
            finished_at=now,
            outcome='ok',
            new_count=0,
            modified_count=0,
        )
        article = Article(notice_id, title, OFFICIAL_HOME, published_on, None)
        run, counts = materials.save(path, [article], run, source['baseline_policy'], manual_notice=True)
    return {'outcome': 'ok', 'run_id': run.run_id, 'link_kind': 'official_search', **counts}
