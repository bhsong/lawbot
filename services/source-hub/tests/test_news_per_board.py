from datetime import UTC, datetime, timedelta

from source_hub import materials, news
from source_hub.config import load
from source_hub.store import Registry, Run

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def runnable_plan(tmp_path):
    plan = Registry(load(), tmp_path / 'registry.db').snapshot()
    for source in plan['sources']:
        source['runnable'] = True
        source['status'] = 'ok'
    return plan


def record(upstream: str, published: str | None, host: str = 'https://www.moel.go.kr') -> tuple[str, dict]:
    return upstream, {
        'title': f'글 {upstream}',
        'canonical_url': f'{host}/view.do?id={upstream}',
        'published_on': published,
    }


def test_each_board_publishes_ten_and_rules_and_law_boards_come_first(tmp_path):
    plan = runnable_plan(tmp_path)
    records = {
        'moel-rules': [record('r1', '2026-09-01')],
        'moel-rss-lawinfo': [record(f'l{day}', f'2026-11-{day:02}') for day in range(1, 13)],
        'moel-interpretations': [record('i1', '2022-08-03')],
    }

    moel = news.build(None, tmp_path / 'unused.db', plan=plan, records=records, now=NOW)['agencies']['moel']

    assert [board['name'] for board in moel['boards']] == [
        '입법·행정예고 RSS',
        '고시·훈령·예규',
        '행정해석·질의회시',
    ]
    boards = [item['board'] for item in moel['items']]
    assert boards.count('입법·행정예고 RSS') == 10
    assert {'고시·훈령·예규', '입법·행정예고 RSS', '행정해석·질의회시'} == set(boards)


def save(database, source_id, upstream_ids, at):
    articles = [
        materials.Article(upstream, f'예고 {upstream}', f'https://www.nts.go.kr/view.do?id={upstream}', None, None)
        for upstream in upstream_ids
    ]
    run = Run(
        run_id=f'run-{at:%H%M}',
        source_id=source_id,
        config_version=0,
        started_at=at,
        finished_at=at,
        outcome='ok',
        new_count=0,
        modified_count=0,
    )
    materials.save(database, [], run, 'baseline', board=articles, board_attempted=True)


def test_undated_posts_keep_the_board_listing_order(tmp_path):
    plan = runnable_plan(tmp_path)
    database = tmp_path / 'materials.db'
    materials.initialize(database)
    save(database, 'nts-withholding', ['300', '100', '200'], NOW)
    save(database, 'nts-withholding', ['050', '300', '100', '200'], NOW + timedelta(days=1))

    records = news.approved(database, news.review_sources_from_plan(plan))
    assert [upstream for upstream, _ in records['nts-withholding']] == ['050', '300', '100', '200']

    nts = news.build(None, database, plan=plan, records=records, now=NOW)['agencies']['nts']
    assert [item['title'] for item in nts['items']] == ['예고 050', '예고 300', '예고 100', '예고 200']
