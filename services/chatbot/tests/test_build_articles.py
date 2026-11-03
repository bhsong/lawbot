# tests/test_build_articles.py  →  조문 CSV 생성기 (lawbot_tools.build_articles, 계획 docs/archives/article-generator.md)
# 단위 테스트는 작은 git 저장소를 임시로 만들어 쓴다. 실제 legalize-kr로 레포 CSV와 비교는 -m network (LEGALIZE_KR)
import io
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


def test_date_expression_years_and_months():
    """'공포 후 1년 6개월이 경과한 날' = 18개월 (노인장기요양보험법 제18610호)"""
    assert expr('공포 후 1년 6개월이 경과한 날부터', '2021-12-21') == '2023-06-22'


def test_schedule_skips_articles_of_another_amending_law():
    """'법률 제9911호 ○○법 일부개정법률 제21조의 개정규정'은 다른 개정법률의 조문이라 이 법 제21조가 아니다 (국세기본법 제21212호)"""
    add = (
        '부칙 <제300호,2025.12.23>\n\n제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다. 다만, 제53조의 개정규정은 '
        '2026년 7월 1일부터 시행하고, 법률 제9911호 시험법 일부개정법률 제53조의 개정규정은 법률 제9346호 다른법 폐지법률의 '
        '시행일부터 시행한다.\n\n\n제2조(경과조치) 생략\n'
    )
    s = sched(add, promulgated='2025-12-23', effective='2026-01-01', asof='2026-11-03')
    assert s.articles == {'제53조': '2026-07-01'}


def test_schedule_of_another_laws_addendum_uses_the_front_matter_date():
    """타법개정의 '부칙(정부조직법) <제21065호>'에 적힌 조문은 그 법의 조문이다. 이 법에는 머리말 시행일자만 쓴다 (소득세법)"""
    add = (
        '부칙(정부조직법) <제300호,2025.10.1>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 다음 각 호의 개정규정은 '
        '해당 호에서 정하는 날부터 시행한다.\n\n1. 다음 각 목의 개정규정은 2026년 1월 2일부터 시행한다.\n\n'
        '가. 제2조제4항 및 제53조의 개정규정\n\n2. 제110조의 개정규정: 대통령령으로 정하는 날\n\n\n제2조(다른 법률의 개정) 생략\n'
    )
    s = sched(add, promulgated='2025-10-01', effective='2025-10-01', asof='2025-11-03')
    assert (s.main, s.articles) == ('2025-10-01', {})


def test_schedule_reads_each_item_when_the_proviso_names_no_article():
    """'다만, 다음 각 호의 개정규정은 해당 호에서 정하는 날부터': 호마다 대상과 날짜. 별표·서식만 적힌 호의 날짜는 그 별표의 것
    (국민건강보험법 시행령 제36675호, 소득세법 시행규칙 제22호)"""
    add = (
        '부칙 <제300호,2026.9.15>\n\n제1조(시행일) 이 영은 2026년 10월 1일부터 시행한다. 다만, 다음 각 호의 개정규정은 '
        '해당 호에서 정하는 날부터 시행한다.\n\n1. 제53조 및 별표 4의3 제1호너목의2의 개정규정: 2026년 12월 24일\n\n'
        '2. 별표 2 제5호의2의 개정규정: 2027년 1월 1일\n\n3. 별지 제24호서식 부표의 개정규정: 2027년 1월 1일\n\n\n'
        '제2조(경과조치) 생략\n'
    )
    s = sched(add, promulgated='2026-09-15', effective='2026-10-01', asof='2026-11-03')
    assert s.articles == {'제53조': '2026-12-24'}
    # 호에 '개정규정'이 없어도 문장이 '다음 각 호의 개정규정'이면 호마다 읽는다 (소득세법 제21221호)
    add = (
        '부칙 <제300호,2025.12.23>\n\n제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다. 다만, 다음 각 호의 개정규정은 '
        '해당 호에서 정하는 날부터 시행한다.\n\n1. 제53조제1항ㆍ제2항(제2호나목으로 한정한다) 및 제110조제8항부터 제11항까지: '
        '2026년 7월 1일\n\n2. 제2조 및 제33조부터 제33조의3까지: 2027년 1월 1일\n\n\n제2조(경과조치) 생략\n'
    )
    s = sched(add, promulgated='2025-12-23', effective='2026-01-01', asof='2026-11-03')
    assert s.articles == {
        '제53조': '2026-07-01',
        '제110조': '2026-07-01',
        '제2조': '2027-01-01',
        '제33조': '2027-01-01',
        '제33조의2': '2027-01-01',
        '제33조의3': '2027-01-01',
    }


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
    """versions: [(공포일, 공포번호, 시행일자, 본문, 부칙[, 개정 종류])] 오래된 것부터. 커밋 날짜 = 공포일"""
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
    for i, (date, number, effective, body, add, *kind) in enumerate(versions):
        addenda += add
        kind = kind[0] if kind else '제정' if i == 0 else '일부개정'
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


def test_in_force_marks_a_pending_article_amended_again(tmp_path):
    """시행 전 개정이 남은 조문을 그 뒤 개정이 다시 바꾸면 조문 단위로 나눌 수 없다: 최신 본문에 시행 전 내용이 섞였다고 표시
    (2026-11-03 사용자 결정. 시행일이 지나면 표시가 없어진다)"""
    v3 = V2.replace('시행 전 정의', '다시 바뀐 정의')
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2026-04-07', '200', '2026-10-08', V2, ADD2),
            ('2026-08-04', '300', '2026-08-04', v3, '부칙 <제300호,2026.8.4>\n\n이 법은 공포한 날부터 시행한다.\n\n'),
        ],
    )
    arts, used = b.in_force(repo, PATH, '2026-10-20')
    got = {a['no']: (a['text'], a['eff']) for a in arts}
    assert got['제2조'] == (
        '다시 바뀐 정의 <개정 2026.4.7>\n[주의: 2027.1.1. 시행 예정인 개정 내용이 함께 들어 있음]',
        '2026-08-04',
    )
    assert used[-1]['mixed'] == [('제2조', ['2027-01-01'])]
    later = {a['no']: a['text'] for a in b.in_force(repo, PATH, '2027-01-01')[0]}
    assert later['제2조'] == '다시 바뀐 정의 <개정 2026.4.7>'


def test_in_force_starts_from_the_last_full_revision(tmp_path):
    """전부개정은 본문 전체를 새로 쓴다: 그 앞 버전은 조합하지 않는다 (앞 버전에서 조합할 수 없는 개정이 있어도 됨).
    전부개정 때 그대로 둔 조문도 시행일은 전부개정의 시행일"""
    v3 = V2.replace('시행 전 정의', '다시 바뀐 정의')
    v4 = (
        article('제1조', '목적', '새 목적')
        + article('제2조', '정의', '새 정의')
        + article('제3조', '벌칙', '처음 벌칙')
    )
    full = '부칙 <제400호,2026.9.1>\n\n이 법은 2026년 10월 1일부터 시행한다.\n\n'
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2026-04-07', '200', '2026-10-08', V2, ADD2),
            ('2026-08-04', '300', '2026-08-04', v3, '부칙 <제300호,2026.8.4>\n\n이 법은 공포한 날부터 시행한다.\n\n'),
            ('2026-09-01', '400', '2026-10-01', v4, full, '전부개정'),
        ],
    )
    got = {a['no']: (a['text'], a['eff']) for a in b.in_force(repo, PATH, '2026-10-20')[0]}
    assert got == {
        '제1조': ('새 목적', '2026-10-01'),
        '제2조': ('새 정의', '2026-10-01'),
        '제3조': ('처음 벌칙', '2026-10-01'),
    }
    before = {a['no']: a['text'] for a in b.in_force(repo, PATH, '2026-08-31')[0]}  # 전부개정 시행 전: 앞 버전부터
    assert before['제2조'].startswith('다시 바뀐 정의')


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
def test_generated_csvs_match_repo_except_known_rule_changes():
    """LEGALIZE_KR=<legalize-kr 클론> uv run pytest -m network -k known_rule_changes
    data/v4는 2026-11-03 규칙 변경 전 생성기 결과다. 바뀐 것은 전부개정 시작(근로자퇴직급여 보장법 제1조 effective_date)과
    부칙 특례 1행뿐이고 나머지는 칸 단위로 같다 (docs/evals/2026-11-03-v5-generator.md)"""
    import pandas as pd

    src = os.environ.get('LEGALIZE_KR')
    if not src or not (Path(src, '.git').exists() or Path(src, 'HEAD').exists()):  # 일반·bare 클론
        pytest.skip('LEGALIZE_KR에 legalize-kr 클론 경로를 넣으세요')
    files, _, stats = b.build(src, '2026-10-20')
    assert re.fullmatch(r'[0-9a-f]{40}', stats['head'])
    for name, repo_csv in ((b.LAW_CSV, V4_ARTICLES), (b.DECREE_CSV, V4_DECREE_ARTICLES)):
        new = pd.read_csv(io.BytesIO(files[name]), encoding='utf-8-sig', dtype=str, keep_default_na=False)
        old = pd.read_csv(repo_csv, encoding='utf-8-sig', dtype=str, keep_default_na=False)
        added = new[new['article_no'].str.startswith('부칙<')]
        assert list(added['article_id']) == ['근로자퇴직급여 보장법 부칙<제21475호>제2조']
        new = new.drop(added.index).reset_index(drop=True)
        changed = new != old
        cells = [(new.at[i, 'article_id'], new.columns[j]) for i, j in zip(*changed.to_numpy().nonzero())]
        assert cells == [('근로자퇴직급여 보장법 제1조', 'effective_date')]


def test_row_order_laws_then_decrees_by_name():
    """법률은 LAWS 순서, 하위법령은 법령 이름 가나다순 (임베딩 batch·data/decree_exp 후보 번호가 이 순서에 묶여 있음)"""
    order = b.doc_order()
    assert order[:7] == [(law, '법률') for law in b.LAWS]
    assert [law for law, k in order[7:] if k == '시행령'] == sorted(b.LAWS)
    assert order[7:9] == [('근로기준법', '시행령'), ('근로기준법', '시행규칙')]


# ── 부칙 특례 조문 ───────────────────────────────────────────────────

SPECIAL = (
    '부칙 <제100호,2020.1.1>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다.\n\n'
    '제2조(옛 특례) 제3조에도 불구하고 2025년 12월 31일까지는 종전의 규정에 따른다.\n\n'
    '부칙 <제200호,2025.4.2>\n\n제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다.\n\n'
    '제2조(적용례) 제2조의 개정규정은 이 법 시행 이후부터 적용한다.\n\n'
    '제3조(요율에 관한 특례) ① 제2조제3항의 개정규정에도 불구하고 2026년부터 2032년까지는 다음 각 호의 비율로 한다.\n\n'
    '1. 2026년은 1만분의 475\n\n2. 2027년은 1만분의 500\n\n'
    '② 「다른 법」 제9조에도 불구하고 같다.\n\n'
    '제4조(다른 법 특례) 「다른 법」 제3조에도 불구하고 2030년까지는 그러하지 아니하다.\n\n'
    '부칙(정부조직법) <제250호,2025.10.1>\n\n제1조(시행일) 생략\n\n제2조(특례) 제3조에도 불구하고 2030년까지 같다.\n\n'
    '부칙 <제300호,2026.9.1>\n\n제1조(시행일) 이 법은 2027년 1월 1일부터 시행한다.\n\n'
    '제2조(신고 특례) 제3조의 개정규정에도 불구하고 2027년 3월 15일까지 신고하여야 한다.\n'
)
USED = [
    {'공포번호': '100', '시행일자': '2020-01-01'},
    {'공포번호': '200', '시행일자': '2026-01-01'},
    {'공포번호': '250', '시행일자': '2025-10-01'},
    {'공포번호': '300', '시행일자': '2027-01-01'},
]


def test_addendum_overrides_keep_running_special_cases_of_the_main_text():
    """부칙 조문이 '제N조…에도 불구하고'로 이 문서 본칙을 기한부로 대신하고, 그 기한이 기준일 이후이며, 그 부칙이 시행 중이면 넣는다.
    기한 지남(제100호), 다른 법 조문만 대신함(제200호 제4조), 타법개정 부칙(제250호), 부칙이 시행 전(제300호)은 뺀다"""
    m = md(
        '300', '2026-09-01', '2027-01-01', article('제2조', '요율', '본칙') + article('제3조', '신고', '본칙'), SPECIAL
    )
    got = b.addendum_overrides(m, USED, '2026-11-03', {'제2조', '제3조'})
    assert [(r['no'], r['title'], r['eff'], r['targets']) for r in got] == [
        ('부칙<제200호>제3조', '요율에 관한 특례', '2026-01-01', ['제2조'])
    ]
    assert got[0]['text'] == (
        '① 제2조제3항의 개정규정에도 불구하고 2026년부터 2032년까지는 다음 각 호의 비율로 한다.\n'
        '1. 2026년은 1만분의 475\n2. 2027년은 1만분의 500\n② 「다른 법」 제9조에도 불구하고 같다.'
    )
    assert got[0]['chapter'] == '부칙 <제200호,2025.4.2>'
    assert b.addendum_overrides(m, USED, '2026-11-03', {'제3조'}) == []  # 대신하는 본칙이 범위 밖
    assert b.addendum_overrides(m, USED, '2033-01-01', {'제2조', '제3조'}) == []  # 기한 지남


# ── 범위 목록 (조문 일부만 넣는 문서) ─────────────────────────────────


def test_load_scope_reads_law_kind_article(tmp_path):
    p = tmp_path / 'scope.csv'
    p.write_text(
        '﻿law,kind,article_no,priority\n소득세법,법률,제1조,1\n소득세법,시행령,제2조,1\n소득세법,법률,제3조,2\n', 'utf-8'
    )
    assert b.load_scope(p) == {('소득세법', '법률'): {'제1조', '제3조'}, ('소득세법', '시행령'): {'제2조'}}


def test_docs_put_scoped_documents_after_the_full_ones():
    """노동 7개 법률 문서(전체)는 지금 순서 그대로, 범위 목록 문서는 그 뒤에 같은 규칙(법률 → 하위법령 이름순)으로"""
    scope = {('소득세법', '시행령'): {'제2조'}, ('국민연금법', '법률'): {'제1조'}, ('소득세법', '법률'): {'제1조'}}
    docs = b.docs(scope)
    assert [(law, kind) for law, kind, _ in docs[:21]] == b.doc_order()
    assert all(s is None for _, _, s in docs[:21])
    assert docs[21:] == [  # 법률은 목록에 처음 나온 법령 순서
        ('소득세법', '법률', {'제1조'}),
        ('국민연금법', '법률', {'제1조'}),
        ('소득세법', '시행령', {'제2조'}),
    ]
    assert b.docs(None) == [(law, kind, None) for law, kind in b.doc_order()]


def test_build_rows_keeps_only_scoped_articles_and_their_addendum_overrides(tmp_path):
    special = (
        '부칙 <제200호,2026.4.7>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다.\n\n'
        '제2조(벌칙 특례) 제3조에도 불구하고 2030년 12월 31일까지는 종전의 규정에 따른다.\n\n'
        '제3조(정의 특례) 제2조에도 불구하고 2030년까지 같다.\n\n'
    )
    v2 = V1.replace('처음 벌칙', '바뀐 벌칙')
    repo = make_repo(
        tmp_path, [('2020-01-01', '100', '2020-01-01', V1, ''), ('2026-04-07', '200', '2026-04-07', v2, special)]
    )
    rows, _ = b.build_rows(repo, {'시험법': PATH}, '2026-10-20', [('시험법', '법률', {'제1조', '제3조'})])
    assert [(r['article_id'], r['chapter'], r['effective_date']) for r in rows] == [
        ('시험법 제1조', None, '2020-01-01'),
        ('시험법 제3조', None, '2026-04-07'),
        ('시험법 부칙<제200호>제2조', '부칙 <제200호,2026.4.7>', '2026-04-07'),
    ]
    assert rows[-1]['title'] == '벌칙 특례' and rows[-1]['deleted'] == 0
    with pytest.raises(ValueError, match='범위 목록의 조문이 없습니다'):
        b.build_rows(repo, {'시험법': PATH}, '2026-10-20', [('시험법', '법률', {'제1조', '제9조'})])
    # 전체 문서(범위 None)도 같은 규칙: 모든 본칙이 대상 (2026-11-03 사용자: 노동 문서에도 켬)
    full, _ = b.build_rows(repo, {'시험법': PATH}, '2026-10-20', [('시험법', '법률', None)])
    assert [r['article_id'] for r in full] == [
        '시험법 제1조',
        '시험법 제2조',
        '시험법 제3조',
        '시험법 부칙<제200호>제2조',
        '시험법 부칙<제200호>제3조',
    ]


def test_csv_names_follow_the_dataset_version():
    assert b.csv_names('v4') == (b.LAW_CSV, b.DECREE_CSV) == ('articles_v4.csv', 'law_articles_v4_decree.csv')
    assert b.csv_names('v5') == ('articles_v5.csv', 'law_articles_v5_decree.csv')
