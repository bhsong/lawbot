# tests/test_build_articles.py  →  조문 CSV 생성기 (lawbot_tools.build_articles, 계획 docs/archives/article-generator.md)
# 단위 테스트는 작은 git 저장소를 임시로 만들어 쓴다. 실제 legalize-kr로 레포 CSV와 바이트 비교는 -m network (LEGALIZE_KR)
import os
import re
import subprocess
from pathlib import Path

import pytest

from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES
from lawbot_tools import build_articles as b

# ── 부칙 시행일 ──────────────────────────────────────────────────────


def expr(s, promulgated):
    return b.date_expr(b.DATE_EXPR.search(s), promulgated)


def test_date_expressions():
    assert expr('공포한 날부터', '2026-04-07') == '2026-04-07'
    assert expr('공포 한 날', '2018-06-29') == '2018-06-29'  # 원문 띄어쓰기 (근로기준법 시행령 제29010호)
    assert expr('공포 후 8개월이 경과한 날부터', '2026-04-07') == '2026-12-08'
    assert expr('공포 후 6개월이 경과한 날부터', '2026-04-07') == '2026-10-08'
    assert expr('공포 후 1년이 경과한 날부터', '2026-06-09') == '2027-06-10'
    assert expr('2027년 1월 1일부터', '2026-04-07') == '2027-01-01'


def test_months_count_from_the_day_after_promulgation():
    """초일 불산입: 4월 30일 공포 + 6개월 → 11월 1일 (4월 30일 + 6개월 + 1일인 10월 31일이 아님)"""
    assert expr('공포 후 6개월이 경과한 날부터', '2019-04-30') == '2019-11-01'
    # 기산일 1/31 + 1개월: 2/31이 없어 2월 말일에 만료 → 3월 1일 시행
    assert expr('공포 후 1개월이 경과한 날부터', '2026-01-30') == '2026-03-01'
    assert expr('공포 후 1년이 경과한 날부터', '2027-02-28') == '2028-03-01'  # 기산일 3/1


def md(number, promulgated, effective, body, addendum=''):
    return (
        f"---\n제목: 시험법\n공포일자: {promulgated}\n공포번호: '{number}'\n시행일자: {effective}\n---\n\n"
        f'# 시험법\n\n{body}\n\n## 부칙\n\n{addendum}\n'
    )


ORDER = ['제1조', '제2조', '제3조', '제4조', '제5조', '제16조']
PROVISO = (
    '부칙 <제21533호,2026.4.7>\n\n\n제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다. '
    '다만, 제2조, 제11장의 제목, 제3조부터 제4조까지 및 부칙 제16조제2항의 개정규정은 공포 후 8개월이 경과한 날부터 '
    '시행하고, 제5조제1항제2호ㆍ제3호의 개정규정은 2027년 1월 1일부터 시행한다.\n\n\n제2조(적용례) 생략\n'
)


def test_schedule_reads_proviso_articles_ranges_and_chapters():
    m = md('21533', '2026-04-07', '2026-10-08', '', PROVISO)
    s = b.schedule(m, b.front(m), ORDER, '2026-10-20')
    assert s.main == '2026-10-08'
    assert s.articles == {'제2조': '2026-12-08', '제3조': '2026-12-08', '제4조': '2026-12-08', '제5조': '2027-01-01'}
    assert s.chapter('제11장 노동감독관 등') == '2026-12-08'
    assert s.article('제1조') == '2026-10-08'
    assert s.article('제16조') == '2026-10-08'  # '부칙 제16조'는 본문 조문이 아님


def test_schedule_uses_own_addendum_only():
    """파일에는 지난 부칙이 다 쌓여 있다. 이 버전 공포번호의 부칙만 읽는다"""
    older = '부칙 <제100호,2020.1.1>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 제1조의 개정규정은 2030년 1월 1일부터 시행한다.\n'
    m = md('21533', '2026-04-07', '2026-10-08', '', older + '\n' + PROVISO)
    assert b.schedule(m, b.front(m), ORDER).article('제1조') == '2026-10-08'


def test_schedule_takes_earliest_date_of_size_based_items():
    """'다음 각 호의 구분에 따른 날'(사업장 규모별): 그 조문이 처음 시행되는 가장 이른 날"""
    add = (
        '부칙 <제17862호,2021.1.5>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 제2조의 개정규정은 '
        '다음 각 호의 구분에 따른 날부터 시행한다.\n\n1. 상시 50명 이상: 공포 후 3개월이 경과한 날\n\n'
        '2. 상시 5명 이상 50명 미만: 2021년 7월 1일\n\n\n제2조(경과조치) 생략\n'
    )
    m = md('17862', '2021-01-05', '2021-01-05', '', add)
    assert b.schedule(m, b.front(m), ORDER).article('제2조') == '2021-04-06'


ORDER2 = ['제1조', '제2조', '제33조', '제33조의2', '제33조의3', '제53조', '제59조', '제110조', '제114조']


def sched(add, number='300', promulgated='2018-03-20', effective='2018-07-01', asof='2026-10-20'):
    m = md(number, promulgated, effective, '', add)
    return b.schedule(m, b.front(m), ORDER2, asof)


def test_schedule_reads_paragraphs_inside_article_one():
    """'제1조(시행일) ① … ② ○의 개정규정은 … ③ …': 항으로 나눈 시행일도 읽는다 (근로기준법 제15513호 형식)"""
    add = (
        '부칙 <제300호,2018.3.20>\n\n제1조(시행일) ① 이 법은 2018년 7월 1일부터 시행한다. \n\n'
        '② 제2조제1항의 개정규정은 다음 각 호의 구분에 따른 날부터 시행한다. \n\n'
        '1. 상시 300명 이상: 2018년 7월 1일(제59조의 개정규정에 따라 특례를 적용받지 아니하게 되는 업종의 경우 2019년 7월 1일)\n\n'
        '2. 상시 50명 이상 300명 미만: 2020년 1월 1일\n\n'
        '③ 제53조제3항 및 제6항, 제110조제1호 및 제2호, 제114조제1호의 개정규정은 2021년 7월 1일부터 시행한다. '
        '다만, 제110조제1호의 개정규정 중 제59조제2항의 개정규정과 관련한 부분은 2018년 9월 1일부터 시행한다.\n\n\n'
        '제2조(유효기간 등) 생략\n'
    )
    s = sched(add)
    assert s.articles == {
        '제2조': '2018-07-01',
        '제53조': '2021-07-01',
        '제110조': '2021-07-01',
        '제114조': '2021-07-01',
    }
    assert s.article('제59조') == '2018-07-01'  # '…중 제59조제2항의 개정규정과 관련한 부분'은 대상이 아님


def test_schedule_old_paragraph_form_reads_only_the_effective_date_paragraph():
    """'①(시행일) … ②(적용례) ○의 개정규정은 … 적용한다': 적용례·경과조치의 '개정규정'은 시행일이 아니다"""
    add = (
        '부칙 <제300호,2007.1.26>\n\n①(시행일) 이 법은 2007년 7월 1일부터 시행한다. 다만, 제33조의2 내지 제33조의3의 개정규정은 '
        '2008년 1월 1일부터 시행하되, 별표1중 법 제82조의 적용에 관한 것은 2009년 1월 1일부터 시행한다.\n\n'
        '②(적용례) 제53조의 개정규정은 이 법 시행 후 최초로 발생한 경우부터 적용한다.\n'
    )
    s = sched(add, promulgated='2007-01-26', effective='2007-07-01')
    assert s.articles == {'제33조의2': '2008-01-01', '제33조의3': '2008-01-01'}  # '내지' 범위


def test_schedule_items_after_main_sentence_are_not_the_provisos():
    """'이 영의 시행일은 다음 각호와 같다. 다만, 별표 2의 개정규정은 공포한 날부터 시행한다. 1. …': 번호 목록은 본문 문장의 것"""
    add = (
        '부칙 <제300호,2003.12.11>\n\n①(시행일) 이 영의 시행일은 다음 각호와 같다. 다만, 별표 2의 개정규정은 공포한 날부터 '
        '시행한다.\n\n1. 상시 1천인 이상: 2004년 7월 1일\n\n2. 상시 20인 미만: 2011년을 초과하지 아니하는 기간 내에서 따로 '
        '대통령령이 정하는 날\n\n②(특례) 생략\n'
    )
    assert sched(add, promulgated='2003-12-11', effective='2003-12-11').articles == {}


def test_schedule_quoted_parts_and_unknown_dates():
    """따옴표 속 조문은 대상이 아님. 날짜를 알 수 없는 시행일('동법의 시행일', '대통령령으로 정하는 날')은 UNKNOWN"""
    add = (
        '부칙 <제300호,2021.1.5>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 제53조의 개정규정 및 제110조제1호의 '
        '개정규정 중 "제59조"에 관한 부분은 공포 후 3개월이 경과한 날부터 시행하고, 제114조의 개정규정 중 「다른 법」에 관한 '
        '부분은 동법의 시행일부터 시행한다.\n\n\n제2조(경과조치) 생략\n'
    )
    s = sched(add, promulgated='2021-01-05', effective='2021-01-05')
    assert s.articles == {'제53조': '2021-04-06', '제110조': '2021-04-06', '제114조': b.UNKNOWN}


def test_schedule_targets_listed_in_items():
    """'다만, 다음 각 호의 부분은 각 호에서 정한 날부터 시행한다. 1. 제53조…의 개정규정 중 …: 날짜' (근로기준법 시행령 2021년 형식)"""
    add = (
        '부칙 <제300호,2021.3.30>\n\n이 영은 2021년 4월 6일부터 시행한다. 다만, 다음 각 호의 부분은 각 호에서 정한 날부터 '
        '시행한다.\n\n1. 제53조제1호 및 제110조제1항제8호의 개정규정 중 다음 각 목의 부분: 다음 각 목의 구분에 따라 정한 날\n\n'
        '가. 법 제51조의2제1항에 관한 부분: 2021년 7월 1일\n\n나. 법 제55조제2항 단서에 관한 부분: 2022년 1월 1일\n\n'
        '2. 제33조, 제59조제2호의2, 별표 7 제2호사목의 개정규정 중 상시 5명 이상 50명 미만의 사업에 관한 부분: 2021년 7월 1일\n'
    )
    s = sched(add, promulgated='2021-03-30', effective='2021-04-06')
    assert s.articles == {
        '제53조': '2021-07-01',
        '제110조': '2021-07-01',
        '제33조': '2021-07-01',
        '제59조': '2021-07-01',
    }


def test_schedule_part_of_article_waits_for_main_date():
    """'제74조제1항의 개정규정: 공포 후 4개월' + 본 시행일 1년: 나머지 항은 1년 뒤라 조문 전체는 본 시행일.
    조문 전체가 적힌 '제56조의 개정규정은 공포한 날부터'는 본 시행일보다 이르게 (근로기준법 제20520호·제15513호)"""
    add = (
        '부칙 <제300호,2024.10.22>\n\n이 법은 공포 후 1년이 경과한 날부터 시행한다. 다만, 다음 각 호의 사항은 그 구분에 따른 날부터 '
        '시행한다.\n\n1. 제53조의 개정규정: 공포한 날\n\n2. 제74조제1항 및 제7항의 개정규정: 공포 후 4개월이 경과한 날\n'
    )
    s = sched(add, promulgated='2024-10-22', effective='2025-10-23')
    assert s.articles == {'제53조': '2024-10-22', '제74조': '2025-10-23'}


def test_schedule_fails_on_late_date_it_cannot_place():
    """시행일 조항에 기준일 뒤 날짜가 있는데 어느 조문의 것인지 못 읽으면 조용히 넘어가지 않는다"""
    add = (
        '부칙 <제300호,2026.6.1>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 별표 3은 2027년 1월 1일부터 '
        '시행한다.\n\n\n제2조(경과조치) 생략\n'
    )
    with pytest.raises(ValueError, match='읽지 못했습니다'):
        sched(add, promulgated='2026-06-01', effective='2026-06-01')
    assert sched(add, promulgated='2026-06-01', effective='2026-06-01', asof='2028-01-01').main == '2026-06-01'


# ── 본문 파싱 ────────────────────────────────────────────────────────

BODY1 = """## 제1장 총칙 <개정 2020.1.1>

##### 제1조 (목적)

이 법은 시험을 목적으로 한다.

##### 제2조 (정의)

**①** 근로자란 사람이다. <개정 2020.1.1>

## 제2장 근로계약

### 제1절 계약

##### 제3조

삭제 <2020.1.1>"""


def test_parse_reads_chapter_section_title_and_cleans_text():
    arts = b.parse(md('100', '2020-01-01', '2020-01-01', BODY1))
    assert [(a['no'], a['title'], a['chapter']) for a in arts] == [
        ('제1조', '목적', '제1장 총칙'),
        ('제2조', '정의', '제1장 총칙'),
        ('제3조', None, '제2장 근로계약 제1절 계약'),
    ]
    assert arts[1]['text'] == '① 근로자란 사람이다. <개정 2020.1.1>'
    assert arts[2]['text'] == '삭제 <2020.1.1>'


def test_squash_ignores_whitespace_and_effective_date_notes():
    a = '① 본문이다.\n[시행일] 제2조의 개정규정은 다음 각 호의 구분에 따른 날부터 시행한다.\n1. 상시 300명 이상: 2018년 7월 1일'
    assert b.squash(a) == b.squash('①  본문이다.')
    assert b.squash('4. 삭제 <2026.4.7>') == b.squash('4. 삭제<2026.4.7>')
    assert b.squash('본문이다.') != b.squash('본문이었다.')


# ── 조문별 시행일 조합 (임시 git 저장소) ─────────────────────────────

PATH = 'kr/시험법/법률.md'


def article(no, title, text):
    return f'##### {no}' + (f' ({title})' if title else '') + f'\n\n{text}\n'


def make_repo(tmp_path, versions):
    """versions: [(공포일, 공포번호, 시행일자, 본문, 부칙)] 오래된 것부터. 커밋 날짜 = 공포일"""
    repo = tmp_path / 'legalize'
    (repo / 'kr/시험법').mkdir(parents=True)
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 't',
        'GIT_AUTHOR_EMAIL': 't@t',
        'GIT_COMMITTER_NAME': 't',
        'GIT_COMMITTER_EMAIL': 't@t',
    }
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    addenda = ''
    for i, (date, number, effective, body, add) in enumerate(versions):
        addenda += add
        kind = '제정' if i == 0 else '일부개정'
        (repo / PATH).write_text(md(number, date, effective, body, addenda), encoding='utf-8')
        stamp = f'{date}T03:00:00Z'
        subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
        subprocess.run(
            ['git', '-C', str(repo), 'commit', '-qm', f'법률: 시험법 ({kind})'],
            check=True,
            env={**env, 'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': stamp},
        )
    r = b.Repo(repo)
    r.load_history([PATH])
    return r


V1 = (
    article('제1조', '목적', '처음 목적')
    + article('제2조', '정의', '처음 정의')
    + article('제3조', '벌칙', '처음 벌칙')
)
ADD2 = (
    '부칙 <제200호,2026.4.7>\n\n제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다. '
    '다만, 제2조 및 제4조의 개정규정은 2027년 1월 1일부터 시행한다.\n\n\n제2조(경과조치) 생략\n\n'
)
V2 = (
    article('제1조', '목적', '바뀐 목적 <개정 2026.4.7>')
    + article('제2조', '정의', '시행 전 정의 <개정 2026.4.7>')
    + article('제3조', '벌칙', '처음 벌칙')
    + article('제4조', '신설', '시행 전 신설 <신설 2026.4.7>')
)


def test_in_force_composes_articles_by_their_own_effective_date(tmp_path):
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, '부칙 <제100호,2020.1.1>\n\n이 법은 공포한 날부터 시행한다.\n\n'),
            ('2026-04-07', '200', '2026-10-08', V2, ADD2),
        ],
    )
    arts, used = b.in_force(repo, PATH, '2026-10-20')
    got = {a['no']: (a['text'], a['eff']) for a in arts}
    assert got == {
        '제1조': ('바뀐 목적 <개정 2026.4.7>', '2026-10-08'),  # 본 시행일 지남 → 새 본문
        '제2조': ('처음 정의', '2020-01-01'),  # 단서 시행일 전 → 이전 본문
        '제3조': ('처음 벌칙', '2020-01-01'),
    }  # 제4조(시행 전 신설)는 없음
    assert used[-1]['시행 전'] == 2
    assert used[-1]['pending'] == [('제2조', '2027-01-01'), ('제4조', '2027-01-01')]  # 조문과 그 시행일
    later = {a['no']: a['text'] for a in b.in_force(repo, PATH, '2027-01-01')[0]}
    assert later['제2조'] == '시행 전 정의 <개정 2026.4.7>' and '제4조' in later


def test_commits_since_lists_amendments_after_source_ref(tmp_path):
    """매니페스트 source.ref 이후 들어온 개정: 커밋·공포일·공포번호·시행일자. ref가 클론에 없으면 None"""
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2026-04-07', '200', '2026-10-08', V2, ADD2),
        ],
    )
    first = repo.history[PATH][-1][0]
    got = b.commits_since(repo, first, {'시험법': PATH})
    assert [(c['doc'], c['date'], c['공포번호'], c['시행일자']) for c in got] == [
        ('시험법', '2026-04-07', '200', '2026-10-08')
    ]
    assert got[0]['subject'] == '법률: 시험법 (일부개정)'
    assert b.commits_since(repo, repo.head(), {'시험법': PATH}) == []
    assert b.commits_since(repo, 'f' * 40, {'시험법': PATH}) is None
    assert b.commits_since(repo, None, {'시험법': PATH}) is None


def test_in_force_ignores_format_only_change_of_a_pending_article(tmp_path):
    """시행 전 개정이 남은 조문의 공백만 바뀐 버전: 이전 본문 그대로. 시행 전 개정이 없으면 형식 변경을 따라가되 시행일은 그대로"""
    v3 = V2.replace('시행 전 정의 <개정', '시행 전 정의<개정').replace('처음 벌칙', '처음  벌칙')
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2026-04-07', '200', '2026-10-08', V2, ADD2),
            ('2026-08-04', '300', '2026-10-02', v3, '부칙 <제300호,2026.8.4>\n\n이 법은 공포한 날부터 시행한다.\n\n'),
        ],
    )
    got = {a['no']: (a['text'], a['eff']) for a in b.in_force(repo, PATH, '2026-10-20')[0]}
    assert got['제2조'] == ('처음 정의', '2020-01-01')
    assert got['제3조'] == ('처음  벌칙', '2020-01-01')


def test_in_force_fails_when_a_pending_article_is_amended_again(tmp_path):
    v3 = V2.replace('시행 전 정의', '다시 바뀐 정의')
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2026-04-07', '200', '2026-10-08', V2, ADD2),
            ('2026-08-04', '300', '2026-08-04', v3, '부칙 <제300호,2026.8.4>\n\n이 법은 공포한 날부터 시행한다.\n\n'),
        ],
    )
    with pytest.raises(ValueError, match='조합할 수 없음'):
        b.in_force(repo, PATH, '2026-10-20')


UNKNOWN_ADD = (
    '부칙 <제300호,2026.6.1>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 제3조의 개정규정은 대통령령으로 정하는 날부터 '
    '시행한다.\n\n\n제2조(경과조치) 생략\n\n'
)


def test_in_force_fails_when_current_text_has_unknown_effective_date(tmp_path):
    v2 = V1.replace('처음 벌칙', '날짜 모를 벌칙')
    repo = make_repo(
        tmp_path, [('2020-01-01', '100', '2020-01-01', V1, ''), ('2026-06-01', '300', '2026-06-01', v2, UNKNOWN_ADD)]
    )
    with pytest.raises(ValueError, match='날짜로 읽을 수 없는'):
        b.in_force(repo, PATH, '2026-10-20')


def test_in_force_accepts_unknown_date_superseded_by_later_amendment(tmp_path):
    v2 = V1.replace('처음 벌칙', '날짜 모를 벌칙')
    v3 = V1.replace('처음 벌칙', '다시 바뀐 벌칙')
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2026-06-01', '300', '2026-06-01', v2, UNKNOWN_ADD),
            ('2026-08-01', '400', '2026-08-01', v3, '부칙 <제400호,2026.8.1>\n\n이 법은 공포한 날부터 시행한다.\n\n'),
        ],
    )
    got = {a['no']: (a['text'], a['eff']) for a in b.in_force(repo, PATH, '2026-10-20')[0]}
    assert got['제3조'] == ('다시 바뀐 벌칙', '2026-08-01')


# ── 유효기간 ─────────────────────────────────────────────────────────


def test_expiry_clauses_read_article_and_paragraphs():
    add = (
        '제1조(시행일) 생략\n\n제2조(유효기간 등) 제53조제3항 및 제6항의 개정규정은 2022년 12월 31일까지 효력을 가진다.\n\n'
        '제3조 (유효기간) 제16조의 개정규정은 2007년 6월 30일까지 효력을 가진다.\n\n'
        '제4조(경과조치) 종전의 최저임금은 1994년 8월 31일까지 효력을 가진다.\n'  # 개정규정이 아니고 유효기간 조도 아님
    )
    assert b.expiry_clauses(add) == [('제53조', [3, 6], '2022-12-31'), ('제16조', [], '2007-06-30')]


def test_expire_marks_paragraphs_and_deletes_whole_articles():
    text = '① 첫째 항.\n② 둘째 항.\n1. 호 하나\n2. 대상 근로자의 범위\n③ 셋째 항. <신설 2018.3.20>'
    arts = [{'no': '제53조', 'text': text}, {'no': '제16조', 'text': '본문'}, {'no': '제9조', 'text': '① 항.'}]
    exp = [('제53조', [2, 3], '2022-12-31'), ('제16조', [], '2007-06-30'), ('제9조', [1], '2030-01-01')]
    b.expire(arts, exp, '2026-10-20', 'kr/시험법/법률.md')
    assert arts[0]['text'] == (
        '① 첫째 항.\n② 둘째 항.\n1. 호 하나\n2. 대상 근로자의 범위 [실효: 2022.12.31.까지 유효]\n'
        '③ 셋째 항. <신설 2018.3.20> [실효: 2022.12.31.까지 유효]'
    )
    assert arts[1].get('expired') is True
    assert arts[2] == {'no': '제9조', 'text': '① 항.'}  # 아직 유효
    with pytest.raises(ValueError, match='찾지 못했습니다'):
        b.expire([{'no': '제1조', 'text': '① 항.'}], [('제1조', [4], '2020-01-01')], '2026-10-20', 'x')


# ── 파일 찾기 ────────────────────────────────────────────────────────


def commit(repo, files, date, subject):
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding='utf-8')
    stamp = f'{date}T03:00:00Z'
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 't',
        'GIT_AUTHOR_EMAIL': 't@t',
        'GIT_COMMITTER_NAME': 't',
        'GIT_COMMITTER_EMAIL': 't@t',
        'GIT_AUTHOR_DATE': stamp,
        'GIT_COMMITTER_DATE': stamp,
    }
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', subject], check=True, env=env)


def test_find_files_skips_repealed_file_with_the_same_title(tmp_path, monkeypatch):
    """근로기준법 폴더처럼 같은 제목 파일이 둘: 1997년 '(폐지)' 커밋으로 끝난 법률.md는 빼고 법률(법률).md"""
    monkeypatch.setattr(b, 'LAWS', ['근로기준법'])
    repo = tmp_path / 'legalize'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    head = "---\n제목: {t}\n공포일자: {d}\n공포번호: '1'\n시행일자: {d}\n상태: 시행\n---\n"
    commit(
        repo,
        {'kr/근로기준법/법률.md': head.format(t='근로기준법', d='1997-03-13')},
        '1997-03-13',
        '법률: 근로기준법 (폐지)',
    )
    commit(
        repo,
        {
            'kr/근로기준법/법률(법률).md': head.format(t='근로기준법', d='2007-04-11'),
            'kr/근로기준법/시행령.md': head.format(t='근로기준법 시행령', d='2007-06-29'),
            'kr/근로기준법/시행규칙.md': head.format(t='근로기준법 시행규칙', d='2007-07-24'),
        },
        '2007-07-24',
        '법률: 근로기준법 (전부개정)',
    )
    paths = b.find_files(b.Repo(repo), '2026-10-20')
    assert paths == {
        '근로기준법': 'kr/근로기준법/법률(법률).md',
        '근로기준법 시행령': 'kr/근로기준법/시행령.md',
        '근로기준법 시행규칙': 'kr/근로기준법/시행규칙.md',
    }


# ── 조문 참조·연결·CSV ──────────────────────────────────────────────


def test_refs_skip_articles_of_another_laws_addendum():
    """'법률 제7379호 근로자퇴직급여보장법 부칙 제2조제1항'은 이 법 제2조가 아니다 (임금채권보장법 제10조)"""
    text = '제9조에 따른 부담금. 3. 법률 제7379호 근로자퇴직급여보장법 부칙 제2조제1항에 따른 퇴직보험'
    own = '임금채권보장법'
    assert [(d, n) for d, n, _ in b.refs(text, own, {}, b.doc_names()) if d == own] == [(own, '제9조')]


def row(law, kind, no, text, deleted=0):
    doc = b.doc_name(law, kind)
    return {'law': doc, 'article_id': f'{doc} {no}', 'kind': kind, 'parent_law': law, 'text': text, 'deleted': deleted}


def test_link_uses_one_rule_for_every_document():
    """같은 문서 → related, 상위 문서 → parent, 그 역방향 → child. 삭제 조문으로는 잇지 않는다 (법률·하위법령 같음)"""
    law = '근로기준법'
    rows = [
        row(law, '법률', '제1조', '제2조와 제3조에 따른다.'),
        row(law, '법률', '제2조', '삭제 <2019.1.15>', deleted=1),
        row(law, '법률', '제3조', '본문'),
        row(law, '시행령', '제1조', '법 제1조 및 제3조에 따라 제2조를 정한다.'),
        row(law, '시행령', '제2조', '법 제2조에 따른다.'),
        row(law, '시행규칙', '제1조', '영 제1조와 법 제3조에 따른다.'),
    ]
    stats = b.link(rows)
    got = {r['article_id']: (r['related_ids'], r['parent_ids'], r['child_ids']) for r in rows}
    assert got == {
        '근로기준법 제1조': ('근로기준법 제3조', None, '근로기준법 시행령 제1조'),
        '근로기준법 제2조': (None, None, None),
        '근로기준법 제3조': (None, None, '근로기준법 시행령 제1조|근로기준법 시행규칙 제1조'),
        '근로기준법 시행령 제1조': (
            '근로기준법 시행령 제2조',
            '근로기준법 제1조|근로기준법 제3조',
            '근로기준법 시행규칙 제1조',
        ),
        '근로기준법 시행령 제2조': (None, None, None),
        '근로기준법 시행규칙 제1조': (None, '근로기준법 시행령 제1조|근로기준법 제3조', None),
    }
    assert stats['dangling'] == []


def test_csv_bytes_bom_and_lf():
    import pandas as pd

    data = b.to_csv_bytes(pd.DataFrame({'a': ['줄1\n줄2', None], 'b': [1, 0]}))
    assert data.startswith('\ufeff'.encode()) and b'\r' not in data
    assert data.decode('utf-8-sig') == 'a,b\n"줄1\n줄2",1\n,0\n'


def test_check_reports_first_difference():
    line, mine, theirs = b.first_diff(b'a\nb\nc', b'a\nx\nc')
    assert (line, mine, theirs) == (2, 'b', 'x')


# ── 합격 기준: 실제 legalize-kr로 레포 CSV와 바이트 단위 비교 ─────────


@pytest.mark.network
def test_generated_csvs_are_byte_identical_to_repo():
    """LEGALIZE_KR=<legalize-kr 클론> uv run pytest -m network -k byte_identical"""
    src = os.environ.get('LEGALIZE_KR')
    if not src or not Path(src, '.git').exists():
        pytest.skip('LEGALIZE_KR에 legalize-kr 클론 경로를 넣으세요')
    files, _, stats = b.build(src, '2026-10-20')
    assert re.fullmatch(r'[0-9a-f]{40}', stats['head'])
    assert files[b.LAW_CSV] == V4_ARTICLES.read_bytes()
    assert files[b.DECREE_CSV] == V4_DECREE_ARTICLES.read_bytes()


def test_row_order_laws_then_decrees_by_name():
    """법률은 LAWS 순서, 하위법령은 법령 이름 가나다순 (임베딩 batch·data/decree_exp 후보 번호가 이 순서에 묶여 있음)"""
    order = b.doc_order()
    assert order[:7] == [(law, '법률') for law in b.LAWS]
    assert [law for law, k in order[7:] if k == '시행령'] == sorted(b.LAWS)
    assert order[7:9] == [('근로기준법', '시행령'), ('근로기준법', '시행규칙')]
