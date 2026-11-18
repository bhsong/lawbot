from datetime import datetime

from law_calendar import news as calendar_news
from lawbot_common.erp_news import AGENCIES, NewsSnapshot
from source_hub import daily, materials, news
from source_hub.config import Source, load
from source_hub.store import KST, Registry

STAMP = datetime(2026, 11, 13, 6, 30, tzinfo=KST)
BOARDS = {
    'moel': [
        ('moel-rss-lawinfo', '입법·행정예고 RSS', '/rss/lawinfo.do', ['법령']),
        ('moel-rules', '고시·훈령·예규', '/info/lawinfo/instruction/list.do', ['고시', '행정규칙']),
        ('moel-interpretations', '행정해석·질의회시', '/info/publicdata/qnrinfo/qnrInfoList.do', ['행정해석']),
    ],
    'mofe': [
        (
            'mofe-tax-reform',
            '입법·행정예고',
            '/lw/lap/TbPrvntcList.do?bbsId=MOSFBBS_000000000055&menuNo=7050300',
            ['세무', '법령'],
        ),
        ('mofe-laws', '법령자료실', '/com/bbs/ComtnbbsList.do?bbsId=MOSFBBS_000000000059&menuNo=7080200', ['법령']),
        (
            'mofe-notice',
            '고시·공고·지침',
            '/lw/denm/TbDenmList.do?bbsId=MOSFBBS_000000000120&menuNo=7090200',
            ['고시', '공지'],
        ),
        ('mofe-qa', '세제 문답자료', None, ['세무']),
        (
            'mofe-publications',
            '간추린 개정세법',
            '/pl/policydta/pblictn/ComtBalbbsList.do?bbsId=MOSFBBS_000000000004&menuNo=5020300&policyCategory=PUB05',
            ['세무', '발간물'],
        ),
    ],
}


def test_registration_retains_only_selected_boards_with_unchanged_settings():
    config = load()
    for agency, boards in BOARDS.items():
        actual = {source.source_id: source.model_dump() for source in config.sources if source.agency == agency}
        host = 'https://www.moel.go.kr' if agency == 'moel' else 'https://mofe.go.kr'
        expected = []
        for source_id, name, path, tags in boards:
            manual = path is None
            expected.append(
                Source(
                    source_id=source_id,
                    group='erp',
                    agency=agency,
                    board_name=name,
                    official_homepage=host
                    + ('/' if agency == 'moel' else '/mn/siteguide/rssService.do?menuNo=2060000'),
                    source_url=host + path if path else None,
                    adapter=agency,
                    mode='rss' if source_id == 'moel-rss-lawinfo' else 'html',
                    verification='manual_review' if manual else 'ready',
                    license_state='unverified' if manual else 'links_only',
                    review_policy='manual' if manual else 'auto_metadata',
                    category_tags=tags,
                ).model_dump()
            )
        assert actual == {source['source_id']: source for source in expected}


def test_daily_news_retains_selected_boards_order_and_latest_ten(tmp_path, monkeypatch):
    registry = Registry(load(), tmp_path / 'collector.db')
    version = 0
    for source in load().sources:
        if source.agency in BOARDS and source.source_url:
            version = registry.patch(
                'source', source.source_id, {'enabled': True}, actor='fixture', reason='fixture', version=version
            )
    cfg = daily.DailyConfig(
        root=tmp_path,
        monitor_home=tmp_path / 'monitor',
        erp_data=tmp_path / 'erp',
        calendar_db=tmp_path / 'calendar.db',
        materials_db=tmp_path / 'materials.db',
        public=tmp_path / 'daily.json',
    )
    materials.initialize(cfg.materials_db)

    def collect(registry, database, source_id, *, plan, reconcile):
        from source_hub.store import Run

        source = next(row for row in plan['sources'] if row['source_id'] == source_id)
        host = 'https://www.moel.go.kr' if source['agency'] == 'moel' else 'https://mofe.go.kr'
        articles = [
            materials.Article(str(day), f'자료 {day}', f'{host}/view.do?id={day}', f'2026-11-{day:02}', None)
            for day in range(1, 13)
        ]
        run = Run(
            run_id=source_id,
            source_id=source_id,
            config_version=plan['config_version'],
            started_at=STAMP,
            finished_at=STAMP,
            outcome='ok',
            new_count=0,
            modified_count=0,
        )
        materials.save(database, articles, run, 'baseline')
        registry.record_run(run)
        return {'outcome': 'ok', 'new_count': 0, 'modified_count': 0}

    def capture(config, paths, cutoff):
        snapshot = NewsSnapshot.model_validate(news.read_snapshot(paths.news))
        return {
            'erp': {'items': [], 'history': []},
            'rules': None,
            'news': {
                agency: calendar_news.from_snapshot(
                    config.calendar_db, snapshot, agency, 10, now=cutoff, use_law_feed=False
                )
                for agency in AGENCIES
            },
        }

    monkeypatch.setattr(daily, 'COLLECTORS', dict.fromkeys(daily.COLLECTORS, collect))
    monkeypatch.setattr(
        daily,
        'run_legacy',
        lambda config, plan, previous: {source: {'outcome': 'ok', 'new_count': 0} for source in plan['collectors']},
    )
    monkeypatch.setattr(daily, 'run_erp', lambda *args, **kwargs: {'outcome': 'ok', 'new_count': 0})
    monkeypatch.setattr(daily, 'capture', capture)
    assert daily.run(registry, cfg, now=lambda: STAMP)['status'] == 'ok'
    snapshot = daily.read_snapshot(cfg.public)
    for agency, boards in BOARDS.items():
        published = snapshot['feeds']['news'][agency]
        assert [board['name'] for board in published['boards']] == [board[1] for board in boards]
        for _, name, path, _ in boards:
            items = [item for item in published['items'] if item['board'] == name]
            assert [item['title'] for item in items] == ([f'자료 {day}' for day in range(12, 2, -1)] if path else [])
    assert snapshot['feeds']['news']['nts']['boards'][-1]['name'] == '공지사항'
