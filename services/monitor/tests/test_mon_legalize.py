"""legalize_git 수집기: 임시 git 저장소로 법령(legalize-kr)과 행정규칙(admrule-kr) 변경을 재현한다."""

import json
import logging

import pytest

from mon_helpers import (
    LAW_PATH,
    WAGE_PATH,
    SourceRepo,
    admrule_md,
    attachment,
    git,
    law_md,
    make_config,
    make_monitor,
)

LABOR_V1 = {'제1조 (목적)': '이 법은 근로조건의 기준을 정한다.', '제54조 (휴게)': '휴게시간을 준다.'}
LABOR_V2 = {'제1조 (목적)': '이 법은 근로조건의 기준을 정한다.', '제54조 (휴게)': '휴게시간을 30분 이상 준다.'}


@pytest.fixture
def legalize(tmp_path):
    repo = SourceRepo(tmp_path / 'src-legalize')
    repo.commit(
        {
            LAW_PATH: law_md('근로기준법', LABOR_V1),
            WAGE_PATH: law_md('최저임금법', {'제1조 (목적)': '최저임금'}),
            'kr/민법/법률.md': law_md('민법', {'제1조 (법원)': '민사'}),
        },
        '법률: 초기',
    )
    return repo


@pytest.fixture
def admrule(tmp_path):
    repo = SourceRepo(tmp_path / 'src-admrule')
    repo.commit(
        {
            '고용노동부/_본부/고시/2026년 적용 최저임금 고시/본문.md': admrule_md(
                '100', '2026년 적용 최저임금 고시', '고용노동부', '시간급 10,320원'
            ),
            '재정경제부/국세청/고시/원천징수 사무처리 고시/본문.md': admrule_md(
                '200', '원천징수 사무처리 고시', '국세청', '원천징수 절차'
            ),
            '재정경제부/국세청/고시/2025년 귀속 경비율 고시/본문.md': admrule_md(
                '300', '2025년 귀속 경비율 고시', '국세청', '경비율'
            ),
        },
        '현행 스냅샷: 초기',
    )
    return repo


def _law_events(summary):
    return [e for e in summary.events if e['source'] == 'legalize_law']


def test_bootstrap_makes_no_events_and_sets_cursor(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    summary = mon.bootstrap()
    assert summary.events == [] and summary.failed == {}
    assert mon.state.cursor('legalize-kr') == legalize.head()
    assert mon.state.cursor('admrule-kr') == admrule.head()
    assert set(mon.state.items('legalize_law')) == {LAW_PATH, WAGE_PATH}  # 대상 밖 민법은 없음
    assert set(mon.state.items('legalize_admrule')) == {'100', '200'}  # 경비율 고시는 키워드 없음
    assert list(mon.paths.events.glob('**/*.json')) == []
    clone = mon.paths.repos / 'legalize-kr'
    assert git(clone, 'config', 'remote.origin.partialclonefilter') == 'blob:none'  # 부분 클론 (bare)
    assert git(clone, 'rev-parse', '--is-bare-repository') == 'true'


def test_amendment_makes_one_event_then_zero(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    sha = legalize.commit(
        {
            LAW_PATH: law_md(
                '근로기준법',
                LABOR_V2,
                promulgated='2026-06-09',
                effective='2026-12-10',
                addenda=[
                    '부칙 <제1호,2000.1.1>\n\n제1조 (시행일) 이 법은 공포한 날부터 시행한다.',
                    '부칙 <제21784호,2026.6.9>\n\n제1조 (시행일) 이 법은 2026년 12월 10일부터 시행한다.',
                ],
            )
        },
        '법률: 근로기준법 (일부개정)',
        date='2026-06-09T12:00:00+09:00',
    )
    summary = mon.collect()
    events = _law_events(summary)
    assert len(events) == 1
    ev = events[0]
    assert ev['change_type'] == 'law_amended' and ev['category'] == 1
    assert ev['target'] == {'law_name': '근로기준법', 'item_id': LAW_PATH, 'law_id': '000001', 'status': '시행'}
    assert ev['dates'] == {'promulgation': '2026-06-09', 'effective': '2026-12-10'}
    assert ev['context']['commit']['sha'] == sha and ev['context']['commit']['subject'] == '법률: 근로기준법 (일부개정)'
    assert ev['context']['addenda_added'] == [
        '부칙 <제21784호,2026.6.9>\n\n제1조 (시행일) 이 법은 2026년 12월 10일부터 시행한다.'
    ]
    assert ev['context']['amendment_reason'] is None  # 법제처 API(PR 2)가 채움
    assert ev['meta_changes']['시행일자'] == {'old': '2026-01-01', 'new': '2026-12-10'}
    assert [c['article'] for c in ev['diff']['chunks']] == ['제54조', '부칙 <제21784호,2026.6.9>']

    home = mon.paths.home
    diff_text = (home / ev['diff']['path']).read_text(encoding='utf-8')
    assert '+휴게시간을 30분 이상 준다.' in diff_text and '공포일자' not in diff_text  # 본문만 diff
    assert (home / ev['old']['file']).read_text(encoding='utf-8') == law_md('근로기준법', LABOR_V1)
    assert (home / ev['new']['file']).exists()
    saved = json.loads((mon.paths.events / '2026-11-02' / f'{ev["event_id"]}.json').read_text(encoding='utf-8'))
    assert saved == ev
    assert mon.state.event_exists(ev['event_id'])

    again = mon.collect()
    assert again.events == []
    assert mon.state.cursor('legalize-kr') == sha


def test_non_target_and_backdated_commits(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    legalize.commit({'kr/민법/법률.md': law_md('민법', {'제1조 (법원)': '바뀜'})}, '법률: 민법')
    assert _law_events(mon.collect()) == []

    # legalize-kr은 커밋 날짜가 공포일: 오늘 들어온 커밋이 커서보다 과거 날짜일 수 있다
    legalize.commit(
        {WAGE_PATH: law_md('최저임금법', {'제1조 (목적)': '최저임금 바뀜'}, promulgated='2019-03-01')},
        '법률: 최저임금법',
        date='2019-03-01T12:00:00+09:00',
    )
    events = _law_events(mon.collect())
    assert [(e['target']['law_name'], e['category'], e['dates']['promulgation']) for e in events] == [
        ('최저임금법', 7, '2019-03-01')
    ]


def test_two_amendments_in_one_range_make_two_events(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    legalize.commit({LAW_PATH: law_md('근로기준법', LABOR_V2, promulgated='2026-03-17')}, '개정 1')
    v3 = {**LABOR_V2, '제55조 (휴일)': '유급휴일을 준다.'}
    legalize.commit({LAW_PATH: law_md('근로기준법', v3, promulgated='2026-06-09')}, '개정 2')
    events = _law_events(mon.collect())
    assert [e['dates']['promulgation'] for e in events] == ['2026-03-17', '2026-06-09']
    assert events[0]['new']['version_key'] == events[1]['old']['version_key']
    assert [c['kind'] for c in events[1]['diff']['chunks']] == ['added']


def test_new_and_repealed_law_files(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    legalize.commit({'kr/근로기준법/시행령.md': law_md('근로기준법 시행령', {'제1조 (목적)': '시행령'})}, '시행령 제정')
    legalize.commit({WAGE_PATH: law_md('최저임금법', {'제1조 (목적)': '최저임금'}, status='폐지')}, '폐지')
    legalize.commit({LAW_PATH: None}, '파일 삭제')
    events = _law_events(mon.collect())
    assert [(e['target']['item_id'], e['change_type']) for e in events] == [
        ('kr/근로기준법/시행령.md', 'law_new'),
        (WAGE_PATH, 'law_repealed'),
        (LAW_PATH, 'law_repealed'),
    ]
    assert events[0]['old'] is None and events[2]['new'] is None
    assert set(mon.state.items('legalize_law')) == {'kr/근로기준법/시행령.md', WAGE_PATH}


def test_attachment_links_only_vs_new_form(tmp_path, legalize, admrule):
    forms = [attachment('0001', '근로계약서', seq=10)]
    legalize.commit({LAW_PATH: law_md('근로기준법', LABOR_V1, attachments=forms)}, '서식 추가')
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()

    # 개정마다 모든 첨부 링크(flSeq)가 바뀐다: 링크만 바뀌면 첨부 변경으로 보지 않는다
    relinked = [attachment('0001', '근로계약서', seq=77)]
    legalize.commit({LAW_PATH: law_md('근로기준법', LABOR_V2, attachments=relinked)}, '개정')
    ev = _law_events(mon.collect())[0]
    assert ev['attachments'] == {'added': [], 'removed': [], 'retitled': []}

    more = [attachment('0001', '근로계약서', seq=80), attachment('0002', '취업규칙 신고서', seq=82)]
    legalize.commit({LAW_PATH: law_md('근로기준법', LABOR_V2, attachments=more)}, '서식 신설')
    ev = _law_events(mon.collect())[0]
    assert [a['제목'] for a in ev['attachments']['added']] == ['취업규칙 신고서']
    assert ev['diff']['chunks'] == []  # 본문은 그대로


def test_link_only_change_makes_no_event(tmp_path, legalize, admrule):
    legalize.commit({LAW_PATH: law_md('근로기준법', LABOR_V1, attachments=[attachment('0001', '서식', seq=1)])}, '첨부')
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    legalize.commit(
        {LAW_PATH: law_md('근로기준법', LABOR_V1, attachments=[attachment('0001', '서식', seq=9)])}, '링크만'
    )
    assert _law_events(mon.collect()) == []
    assert mon.state.items('legalize_law')[LAW_PATH].version_key != ''  # 상태는 새 blob으로 갱신


# ---- 행정규칙 ----


def _admrule_events(summary):
    return [e for e in summary.events if e['source'] == 'legalize_admrule']


def test_admrule_keyword_and_agency_filter(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    admrule.commit(
        {
            '재정경제부/국세청/고시/원천징수 사무처리 고시/본문.md': admrule_md(
                '200', '원천징수 사무처리 고시', '국세청', '원천징수 절차 개정', number='2026-5'
            ),
            '재정경제부/국세청/고시/2025년 귀속 경비율 고시/본문.md': admrule_md(
                '300', '2025년 귀속 경비율 고시', '국세청', '경비율 개정'
            ),  # 키워드 없음
            '행정안전부/_본부/고시/원천징수 협조 고시/본문.md': admrule_md(
                '400', '원천징수 협조 고시', '행정안전부', '다른 부처'
            ),  # 부처 다름
            '재정경제부/국세청/고시/지급명세서 제출 고시/본문.md': admrule_md(
                '500', '지급명세서 제출 고시', '국세청', '신설'
            ),
        },
        '고시: 여러 건',
    )
    events = _admrule_events(mon.collect())
    got = sorted((e['target']['item_id'], e['change_type'], e['category']) for e in events)
    assert got == [('200', 'admrule_amended', 3), ('500', 'admrule_new', 3)]
    ev = next(e for e in events if e['target']['item_id'] == '200')
    assert ev['target'] == {'law_name': '원천징수 사무처리 고시', 'item_id': '200', 'agency': '국세청'}
    assert ev['meta_changes']['발령번호'] == {'old': '2026-1', 'new': '2026-5'}


def test_admrule_yearly_name_pattern(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule, keyword_rules=[]))
    mon.bootstrap()
    admrule.commit(
        {
            '고용노동부/_본부/고시/2027년 적용 최저임금 고시/본문.md': admrule_md(
                '101', '2027년 적용 최저임금 고시', '고용노동부', '시간급 10,800원', issued='2026-08-05'
            )
        },
        '고시: 2027년 적용 최저임금 고시 (2026-50)',
    )
    events = _admrule_events(mon.collect())
    assert [(e['target']['law_name'], e['change_type'], e['category']) for e in events] == [
        ('2027년 적용 최저임금 고시', 'admrule_new', 7)
    ]
    assert events[0]['dates'] == {'promulgation': '2026-08-05', 'effective': '2026-01-01'}


def test_admrule_move_with_same_id_is_not_a_change(tmp_path, legalize, admrule):
    """부처 이름이 바뀌면 경로가 바뀐다 (기획재정부 → 재정경제부). 같은 행정규칙ID·같은 내용이면 이벤트 없음."""
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    admrule.move(
        '재정경제부/국세청/고시/원천징수 사무처리 고시/본문.md',
        '기획재정부/국세청/고시/원천징수 사무처리 고시/본문.md',
        '경로 이동',
    )
    assert _admrule_events(mon.collect()) == []
    assert (
        mon.state.items('legalize_admrule')['200'].file_path == '기획재정부/국세청/고시/원천징수 사무처리 고시/본문.md'
    )


def test_admrule_removed(tmp_path, legalize, admrule):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    admrule.commit({'재정경제부/국세청/고시/원천징수 사무처리 고시/본문.md': None}, '현행 스냅샷: 제외')
    events = _admrule_events(mon.collect())
    assert [(e['change_type'], e['removed'], e['new']) for e in events] == [('admrule_amended', True, None)]
    assert '200' not in mon.state.items('legalize_admrule')


# ---- 강제 푸시 ----


def test_force_push_falls_back_to_content_compare(tmp_path, legalize, admrule, caplog):
    mon = make_monitor(tmp_path, make_config(legalize, admrule))
    mon.bootstrap()
    legalize.commit({LAW_PATH: law_md('근로기준법', LABOR_V2)}, '개정')
    first = _law_events(mon.collect())
    assert len(first) == 1

    # 저장소 재구성: 모든 커밋 해시가 바뀜. 그 사이 최저임금법이 새로 바뀜
    legalize.commit({WAGE_PATH: law_md('최저임금법', {'제1조 (목적)': '최저임금 개정'})}, '개정 2')
    legalize.rewrite_history()
    with caplog.at_level(logging.WARNING, logger='law_monitor'):
        summary = mon.collect()
    events = _law_events(summary)
    assert [(e['target']['item_id'], e['change_type']) for e in events] == [(WAGE_PATH, 'law_amended')]
    assert events[0]['context']['commit'] is None and events[0]['context']['fallback'] == 'content_compare'
    assert '+최저임금 개정' in (mon.paths.home / events[0]['diff']['path']).read_text(encoding='utf-8')
    assert any('커서' in r.message for r in caplog.records)
    assert mon.state.cursor('legalize-kr') == legalize.head()
    assert _law_events(mon.collect()) == []
