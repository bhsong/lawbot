"""경로·설정·머리말·조문 나누기·이벤트·상태 저장소 단위 테스트 (git·네트워크 없음)."""

import json
import sqlite3
from pathlib import Path

import pytest

from law_monitor import articles, frontmatter
from law_monitor.config import CONFIG_FILE, ConfigError, from_dict, load
from law_monitor.events import EventStore, make_event_id
from law_monitor.paths import SERVICE_DIR, Paths, default_home
from law_monitor.state import ItemState, State
from mon_helpers import admrule_md, attachment, law_md, make_config

# ---- 경로 ----


def test_service_dir_is_services_monitor():
    assert SERVICE_DIR == Path(__file__).resolve().parents[1]
    assert SERVICE_DIR.name == 'monitor' and SERVICE_DIR.parent.name == 'services'
    assert CONFIG_FILE == SERVICE_DIR / 'config' / 'targets.yaml'


def test_home_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv('LAW_MONITOR_HOME', raising=False)
    assert default_home() == SERVICE_DIR
    monkeypatch.setenv('LAW_MONITOR_HOME', str(tmp_path))
    assert default_home() == tmp_path
    p = Paths(tmp_path)
    assert p.state_db == tmp_path / 'data' / 'state.db'
    assert p.repos == tmp_path / 'data' / 'repos' and p.logs == tmp_path / 'logs'


# ---- 설정 ----


def test_repo_targets_yaml_loads():
    cfg = load()
    names = [law.name for law in cfg.laws]
    assert '근로기준법' in names and '고용보험및산업재해보상보험의보험료징수등에관한법률' in names
    assert all(' ' not in n for n in names)  # legalize-kr 폴더명은 띄어쓰기 없음
    assert cfg.repos['legalize-kr'].startswith('https://github.com/legalize-kr/')
    rule = next(r for r in cfg.admrule_names if '최저임금' in r.pattern.pattern)
    assert rule.pattern.search('2027년 적용 최저임금 고시') and rule.pattern.search('2027년 적용 최저임금안 고시')
    assert not rule.pattern.search('최저임금위원회 사용자위원을 추천할 수 있는 단체 지정')


def _base() -> dict:
    return {
        'repos': {'legalize-kr': 'x', 'admrule-kr': 'y'},
        'laws': [{'name': '근로기준법', 'category': 1}],
        'admrules': {'names': [], 'keyword_rules': []},
    }


@pytest.mark.parametrize(
    'mutate, message',
    [
        (lambda d: d['laws'].append({'name': '최저임금법', 'category': 9}), 'category'),
        (lambda d: d['laws'].append({'name': '', 'category': 1}), '이름'),
        (lambda d: d['laws'].append({'name': '근로기준법', 'category': 1}), '중복'),
        (lambda d: d['admrules']['names'].append({'agency': '국세청', 'pattern': '([', 'category': 3}), '정규식'),
        (
            lambda d: d['admrules']['keyword_rules'].append({'agency': '국세청', 'keywords': [], 'category': 3}),
            '키워드',
        ),
        (lambda d: d['repos'].pop('admrule-kr'), 'admrule-kr'),
    ],
)
def test_config_rejects_bad_values(mutate, message):
    data = _base()
    mutate(data)
    with pytest.raises(ConfigError, match=message):
        from_dict(data)


# ---- 머리말·첨부 ----


def test_frontmatter_split_and_dates_as_text():
    meta, body = frontmatter.split(law_md('최저임금법', {'제1조 (목적)': '목적'}, promulgated='2026-04-07'))
    assert meta['제목'] == '최저임금법' and meta['공포일자'] == '2026-04-07'  # YAML 날짜도 문자열로
    assert body.startswith('# 최저임금법')
    assert frontmatter.split('머리말 없음') == ({}, '머리말 없음')


def test_attachment_changes_ignore_links():
    old = [attachment('0001', '근로소득 원천징수영수증', seq=10), attachment('0002', '지급명세서', seq=20)]
    relinked = [attachment('0001', '근로소득 원천징수영수증', seq=99), attachment('0002', '지급명세서', seq=98)]
    assert frontmatter.attachment_changes(old, relinked) == {'added': [], 'removed': [], 'retitled': []}
    new = [attachment('0001', '근로소득 원천징수영수증(개정)', seq=1), attachment('0003', '간이지급명세서', seq=3)]
    changes = frontmatter.attachment_changes(old, new)
    assert [a['제목'] for a in changes['added']] == ['간이지급명세서']
    assert [a['제목'] for a in changes['removed']] == ['지급명세서']
    assert changes['retitled'] == [
        {
            '별표구분': '서식',
            '별표번호': '0001',
            '별표가지번호': '00',
            'old': '근로소득 원천징수영수증',
            'new': '근로소득 원천징수영수증(개정)',
        }
    ]
    assert all('파일링크' not in a for a in changes['added'])


def test_meta_changes_skip_attachments():
    a, _ = frontmatter.split(admrule_md('1', '고시', '국세청', '본문', attachments=[attachment('1', 'x', seq=1)]))
    b, _ = frontmatter.split(
        admrule_md('1', '고시', '국세청', '본문', number='2026-9', attachments=[attachment('1', 'x', seq=5)])
    )
    assert frontmatter.meta_changes(a, b) == {'발령번호': {'old': '2026-1', 'new': '2026-9'}}


# ---- 조문 단위 diff ----


def test_article_chunks():
    old = law_md('근로기준법', {'제1조 (목적)': '목적', '제2조 (정의)': '정의', '제3조 (삭제될 조)': '없어짐'})
    new = law_md(
        '근로기준법',
        {'제1조 (목적)': '목적', '제2조 (정의)': '정의를 바꿈', '제2조의2 (신설)': '새 조문'},
        addenda=[
            '부칙 <제1호,2000.1.1>\n\n제1조 (시행일) 이 법은 공포한 날부터 시행한다.',
            '부칙 <제21784호,2026.6.9>\n\n제1조 (시행일) 이 법은 2026년 12월 10일부터 시행한다.',
        ],
    )
    _, old_body = frontmatter.split(old)
    _, new_body = frontmatter.split(new)
    chunks = articles.chunk_diffs(old_body, new_body)
    kinds = {c['article']: c['kind'] for c in chunks}
    assert kinds == {'제2조': 'changed', '제2조의2': 'added', '제3조': 'removed', '부칙 <제21784호,2026.6.9>': 'added'}
    changed = next(c for c in chunks if c['article'] == '제2조')
    assert '+정의를 바꿈' in changed['diff'] and '-정의' in changed['diff']
    assert articles.added_addenda(old_body, new_body) == [
        '부칙 <제21784호,2026.6.9>\n\n제1조 (시행일) 이 법은 2026년 12월 10일부터 시행한다.'
    ]


# ---- 이벤트·상태 ----


def test_event_id_is_content_based():
    a = make_event_id('legalize_law', 'kr/근로기준법/법률.md', 'abc')
    assert a == make_event_id('legalize_law', 'kr/근로기준법/법률.md', 'abc')
    assert a != make_event_id('legalize_law', 'kr/근로기준법/법률.md', 'abd')
    assert len(a) == 40


def _event(event_id: str) -> dict:
    return {
        'event_id': event_id,
        'detected_at': '2026-11-02T06:30:00+09:00',
        'category': 1,
        'source': 'legalize_law',
        'change_type': 'law_amended',
        'target': {},
        'dates': {},
        'old': None,
        'new': None,
        'diff': {'kind': 'unified_text', 'path': None, 'chunks': []},
        'context': {},
    }


def test_event_store_writes_json_and_diff_once(tmp_path):
    paths = Paths(tmp_path)
    store = EventStore(paths.home, paths.events)
    ev = _event('e1')
    path = store.write(ev, diff_text='--- a\n+++ b\n')
    store.write(ev, diff_text='--- a\n+++ b\n')
    assert path == paths.events / '2026-11-02' / 'e1.json'
    assert sorted(p.name for p in path.parent.iterdir()) == ['e1.diff', 'e1.json']
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['diff']['path'] == 'data/events/2026-11-02/e1.diff'  # HOME 기준 상대 경로
    for key in (
        'event_id',
        'detected_at',
        'category',
        'source',
        'change_type',
        'target',
        'dates',
        'old',
        'new',
        'diff',
        'context',
    ):
        assert key in saved


def test_state_tables_and_atomic_apply(tmp_path):
    state = State(tmp_path / 'state.db')
    tables = {r[0] for r in sqlite3.connect(tmp_path / 'state.db').execute('select name from sqlite_master')}
    assert {'git_cursor', 'items', 'seen_posts', 'events', 'runs'} <= tables

    item = ItemState('legalize_law', 'kr/a.md', 'blob1', 'blob1', 'kr/a.md')
    state.apply(cursors={'legalize-kr': 'c1'}, items=[item], removed=[], events=[_event('e1')], now='t')
    assert state.cursor('legalize-kr') == 'c1' and state.event_exists('e1')
    assert state.items('legalize_law')['kr/a.md'].version_key == 'blob1'

    bad = _event('e2') | {'event_id': None}  # NOT NULL 위반 → 전체 되돌림
    with pytest.raises(sqlite3.IntegrityError):
        state.apply(
            cursors={'legalize-kr': 'c2'},
            items=[ItemState('legalize_law', 'kr/a.md', 'b2', 'b2', 'kr/a.md')],
            removed=[],
            events=[bad],
            now='t',
        )
    assert state.cursor('legalize-kr') == 'c1'
    assert state.items('legalize_law')['kr/a.md'].version_key == 'blob1'


def test_make_config_helper_is_valid():
    cfg = make_config()
    assert [law.name for law in cfg.laws] == ['근로기준법', '최저임금법']
