# tests/test_build_articles.py  →  조문 CSV 생성기 (lawbot_tools.build_articles, 계획 docs/archives/article-generator.md)
# 단위 테스트는 작은 git 저장소를 임시로 만들어 쓴다. 실제 legalize-kr로 레포 CSV와 비교는 -m network (LEGALIZE_KR)
import io
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES
from lawbot_common import law_scope
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


def test_schedule_reads_dates_given_to_an_earlier_amendment_of_the_same_document():
    """'대통령령 제32516호 소득세법 시행령 일부개정령 제216조의4의 개정규정: 2027년 1월 1일' (제35349호 부칙): 같은 문서의 앞 개정이
    정한 조문을 이번 개정이 다시 고치며 시행일을 정한 것이라 이 문서 조문의 날짜다. 다른 문서의 개정법률이나 날짜가 없는 것은 건너뛴다"""
    add = (
        '부칙 <제300호,2025.2.28>\n\n제1조(시행일) 이 영은 공포한 날부터 시행한다. 다만, 다음 각 호의 개정규정은 해당 호에서 정하는 날부터 '
        '시행한다.\n\n1. 대통령령 제200호 시험법 일부개정령 제53조의 개정규정: 2027년 1월 1일\n\n'
        '2. 대통령령 제200호 시험법 일부개정령(대통령령 제250호 시험법 일부개정령에 따라 개정된 내용을 포함한다) 제59조의 개정규정: '
        '2027년 1월 1일\n\n3. 법률 제77호 다른법 일부개정법률 제110조의 개정규정: 2027년 1월 1일\n\n\n제2조(경과조치) 생략\n'
    )
    s = sched(add, promulgated='2025-02-28', effective='2025-02-28', asof='2026-11-03')
    assert s.articles == {'제53조': '2027-01-01', '제59조': '2027-01-01'}


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
        addenda = (
            add(addenda) if callable(add) else addenda + add
        )  # 함수면 쌓인 부칙을 고친다 (나중 법률이 옛 부칙을 개정)
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


def test_in_force_reads_addenda_as_later_amended(tmp_path):
    """나중 법률이 옛 부칙의 시행일을 고친다 (소득세법 제17757호 부칙: 가상자산 과세 2022 → 2027).
    부칙은 기준일 버전 파일에 실린 것(나중 개정 반영본)을 읽고, 부칙 안의 '<개정 …>' 표시는 문장 나누기에서 뺀다"""
    v2 = V1 + article('제4조', '신설', '시행이 미뤄진 신설 <신설 2020.12.29>')
    add2 = (
        '부칙 <제200호,2020.12.29>\n\n제1조(시행일) 이 법은 2021년 1월 1일부터 시행한다. 다만, 다음 각 호의 개정규정은 '
        '각 호의 구분에 따른 날부터 시행한다.\n\n1. 제4조의 개정규정: 2022년 1월 1일\n\n\n제2조(경과조치) 생략\n\n'
    )

    def postpone(addenda):
        later = addenda.replace(
            '시행한다.\n\n1. 제4조의 개정규정: 2022년 1월 1일',
            '시행한다. <개정 2021.12.8>\n\n1. 제4조의 개정규정: 2027년 1월 1일',
        )
        return later + '부칙 <제300호,2021.12.8>\n\n이 법은 공포한 날부터 시행한다.\n\n'

    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', V1, ''),
            ('2020-12-29', '200', '2021-01-01', v2, add2),
            ('2021-12-08', '300', '2021-12-08', v2, postpone),
        ],
    )
    assert '제4조' not in {a['no'] for a in b.in_force(repo, PATH, '2026-11-03')[0]}  # 2027년 시행으로 미뤄짐
    later = {a['no']: (a['text'], a['eff']) for a in b.in_force(repo, PATH, '2027-01-01')[0]}
    assert later['제4조'] == ('시행이 미뤄진 신설 <신설 2020.12.29>', '2027-01-01')


def test_in_force_other_law_change_to_a_pending_article_waits_with_it(tmp_path):
    """타법개정 부칙의 '시행일이 도래하지 아니한 법률을 개정한 부분은 각각 해당 법률의 시행일부터': 지금 삭제된 조문을 시행 전 개정이
    다시 만들고 있으면, 그 조문을 고친 것은 시행 전 내용을 고친 것이라 그 개정과 함께 시행 전 (소득세법 시행령 제216조의4,
    정부조직법 제35947호). 지금 시행 중인 본문이 있는 조문은 어느 부분을 고쳤는지 나눌 수 없어 지금처럼 주의 표시"""
    v1 = V1.replace(article('제2조', '정의', '처음 정의'), article('제2조', None, '삭제 <2019.1.1>'))
    v2 = V2.replace(article('제3조', '벌칙', '처음 벌칙'), article('제3조', '벌칙', '시행 전 벌칙 <개정 2026.4.7>'))
    add2 = ADD2.replace('제2조 및 제4조의 개정규정', '제2조, 제3조 및 제4조의 개정규정')
    v3 = v2.replace('시행 전 정의', '부처 이름만 바뀐 시행 전 정의').replace('시행 전 벌칙', '부처 이름만 바뀐 벌칙')
    other = (
        '부칙(정부조직법) <제300호,2026.8.4>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 부칙 제2조에 따라 개정되는 법률 중 '
        '이 법 시행 전에 공포되었으나 시행일이 도래하지 아니한 법률을 개정한 부분은 각각 해당 법률의 시행일부터 시행한다.\n\n'
        '제2조(다른 법률의 개정) 생략\n\n'
    )
    repo = make_repo(
        tmp_path,
        [
            ('2020-01-01', '100', '2020-01-01', v1, ''),
            ('2026-04-07', '200', '2026-10-08', v2, add2),
            ('2026-08-04', '300', '2026-08-04', v3, other, '타법개정'),
        ],
    )
    arts, used = b.in_force(repo, PATH, '2026-10-20')
    got = {a['no']: a['text'] for a in arts}
    assert got['제2조'] == '삭제 <2019.1.1>'  # 시행 전 개정과 함께 기다림
    assert got['제3조'].startswith('부처 이름만 바뀐 벌칙') and '[주의:' in got['제3조']  # 시행 중 본문이 있음
    assert ('제2조', '2027-01-01') in used[-1]['pending'] and [no for no, _ in used[-1]['mixed']] == ['제3조']
    later = {a['no']: a['text'] for a in b.in_force(repo, PATH, '2027-01-01')[0]}
    assert later['제2조'] == '부처 이름만 바뀐 시행 전 정의 <개정 2026.4.7>'
    # 시행령 형식 (제35947호 '부칙(재정경제부 직제)'): '도래하지 않은 대통령령을 개정한 부분은 각각 해당 대통령령의 시행일부터'
    decree = '…이 영 시행 전에 공포되었으나 시행일이 도래하지 않은 대통령령을 개정한 부분은 각각 해당 대통령령의 시행일부터 시행한다.'
    assert b.FOLLOWS_PENDING.search(re.sub(r'\s+', '', decree))


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


@pytest.fixture
def blob_clones(tmp_path, monkeypatch):
    source, remote = tmp_path / 'source', tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    laws = ['시험법', '검증법']
    entries = tuple(
        law_scope.LawScope(name=law, folder=law, category=1, keywords=(law,), decrees=('시행령', '시행규칙'))
        for law in laws
    )
    selection = tmp_path / 'scope.csv'
    selection.write_text('law,kind,article_no\n', encoding='utf-8')
    monkeypatch.setattr(law_scope, 'load_law_scope', lambda: entries)
    monkeypatch.setattr(b, 'TAX_SCOPE', selection)
    paths = {b.doc_name(law, kind): f'kr/{law}/{kind} 원문.md' for law in laws for kind in b.KINDS}
    for i, change in enumerate(('제정', '일부개정', '전부개정', '일부개정', '전부개정')):
        date = f'{2020 + i}-01-01'
        effective = date if i < 4 else '2027-01-01'
        commit(
            source,
            {
                path: md(str(100 + i), date, effective, article('제1조', '목적', f'{doc} 본문 {i}')).replace(
                    '시험법', doc
                )
                for doc, path in paths.items()
            },
            date,
            f'법령 묶음 ({change})',
        )
    subprocess.run(['git', 'clone', '-q', '--bare', str(source), str(remote)], check=True)
    origin = b.Repo(remote)
    origin.git('config', 'uploadpack.allowFilter', 'true')
    origin.git('config', 'uploadpack.allowAnySHA1InWant', 'true')
    partial, full = tmp_path / 'partial.git', tmp_path / 'full.git'
    for target, options in ((partial, ['--filter=blob:none']), (full, [])):
        subprocess.run(['git', 'clone', '-q', '--bare', *options, remote.as_uri(), str(target)], check=True)
    monkeypatch.setenv('GIT_TRACE2_EVENT', str(tmp_path / 'trace.jsonl'))
    monkeypatch.setenv('GIT_TRACE_PACKET', str(tmp_path / 'packet.log'))
    monkeypatch.setenv('LC_ALL', 'C')
    return b.Repo(partial), b.Repo(full), paths


def traced_fetches(tmp_path):
    trace = tmp_path / 'trace.jsonl'
    if not trace.exists():
        return []
    events = (json.loads(line) for line in trace.read_text().splitlines())
    return [event['argv'] for event in events if event['event'] == 'start' and 'fetch' in event['argv']]


def test_show_many_batches_twenty_missing_blobs(blob_clones, tmp_path):
    partial, full, paths = blob_clones
    items = [(sha, path) for sha in full.git('rev-list', 'HEAD').splitlines() for path in paths.values()][:20]
    expected = full.show_many(items)
    assert len(set(expected)) == 20 and traced_fetches(tmp_path) == []
    assert partial.show_many(items) == expected
    assert len(traced_fetches(tmp_path)) == 1
    assert partial.show_many(items[::-1]) == expected[::-1]
    assert b.Repo(partial.path).show_many(items) == expected
    assert len(traced_fetches(tmp_path)) == 1


def test_prefetch_requests_only_missing_oids_once(blob_clones, tmp_path):
    partial, full, paths = blob_clones
    first, second, third = list(paths.values())[:3]
    partial.show('HEAD', first)
    fetch_count = len(traced_fetches(tmp_path))
    packet = tmp_path / 'packet.log'
    offset = packet.stat().st_size
    items = [('HEAD', first), ('HEAD', second), (full.head(), second), ('HEAD', third)]
    assert b.Repo(partial.path).show_many(items) == full.show_many(items)
    assert len(traced_fetches(tmp_path)) - fetch_count == 1
    wants = re.findall(rb'fetch> want ([0-9a-f]+)', packet.read_bytes()[offset:])
    assert wants == [full.git('rev-parse', f'HEAD:{path}').strip().encode() for path in (second, third)]


def test_prefetch_failure_keeps_cause_without_fallback(blob_clones, tmp_path):
    partial, _, paths = blob_clones
    partial.git('remote', 'set-url', 'origin', (tmp_path / 'missing-origin.git').as_uri())
    with pytest.raises(RuntimeError, match='blob 사전 수신 실패') as error:
        partial.show_many([('HEAD', path) for path in paths.values()])
    assert 'missing-origin.git' in str(error.value)
    assert isinstance(error.value.__cause__, subprocess.CalledProcessError)
    assert len(traced_fetches(tmp_path)) == 1


def test_prefetch_reports_a_missing_path_without_fetching(blob_clones, tmp_path):
    partial, _, paths = blob_clones
    with pytest.raises(FileNotFoundError, match=r'HEAD:absent\.md'):
        partial.show_many([('HEAD', next(iter(paths.values()))), ('HEAD', 'absent.md')])
    assert traced_fetches(tmp_path) == []


def test_cat_file_does_not_lazy_fetch_if_prefetch_did_not_fill_objects(blob_clones, tmp_path, monkeypatch):
    partial, _, paths = blob_clones
    run = subprocess.run
    calls = []

    def empty_fetch(args, **kwargs):
        if 'fetch' in args:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', empty_fetch)
    with pytest.raises(FileNotFoundError, match='HEAD:kr/'):
        partial.show_many([('HEAD', path) for path in paths.values()])
    assert len(calls) == 1 and traced_fetches(tmp_path) == []


def test_build_batches_relevant_history_and_keeps_bytes(blob_clones, tmp_path):
    partial, full, paths = blob_clones
    asof = '2026-11-08'
    expected = b.build(full.path, asof)
    assert traced_fetches(tmp_path) == []
    assert b.build(partial.path, asof) == expected
    assert len(traced_fetches(tmp_path)) == 3
    old_oid = full.git('rev-parse', f'HEAD~3:{next(iter(paths.values()))}').strip()
    assert old_oid.encode() not in (tmp_path / 'packet.log').read_bytes()


def test_commits_since_batches_and_keeps_report(blob_clones, tmp_path):
    partial, full, paths = blob_clones
    ref = full.git('rev-parse', 'HEAD~4').strip()
    expected = b.commits_since(full, ref, paths)
    assert len(expected) == 24 and traced_fetches(tmp_path) == []
    assert b.commits_since(partial, ref, paths) == expected
    assert len(traced_fetches(tmp_path)) == 1


def test_find_files_skips_repealed_file_with_the_same_title(tmp_path, monkeypatch):
    """근로기준법 폴더처럼 같은 제목 파일이 둘: 1997년 '(폐지)' 커밋으로 끝난 법률.md는 빼고 법률(법률).md"""
    entry = law_scope.LawScope(
        name='근로기준법',
        folder='근로기준법_원문',
        category=1,
        keywords=('근로기준법',),
        decrees=('시행령', '시행규칙'),
    )
    folder = f'kr/{entry.folder}'
    monkeypatch.setattr(law_scope, 'load_law_scope', lambda: (entry,))
    repo = tmp_path / 'legalize'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    head = "---\n제목: {t}\n공포일자: {d}\n공포번호: '1'\n시행일자: {d}\n상태: 시행\n---\n"
    commit(
        repo,
        {f'{folder}/법률.md': head.format(t='근로기준법', d='1997-03-13')},
        '1997-03-13',
        '법률: 근로기준법 (폐지)',
    )
    commit(
        repo,
        {
            f'{folder}/법률(법률).md': head.format(t='근로기준법', d='2007-04-11'),
            f'{folder}/시행령.md': head.format(t='근로기준법 시행령', d='2007-06-29'),
            f'{folder}/시행규칙.md': head.format(t='근로기준법 시행규칙', d='2007-07-24'),
        },
        '2007-07-24',
        '법률: 근로기준법 (전부개정)',
    )
    documents = [('근로기준법', kind, None) for kind in b.KINDS]
    paths = b.find_files(b.Repo(repo), '2026-10-20', documents)
    assert paths == {
        '근로기준법': f'{folder}/법률(법률).md',
        '근로기준법 시행령': f'{folder}/시행령.md',
        '근로기준법 시행규칙': f'{folder}/시행규칙.md',
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
    """법률은 공통 범위 순서, 하위법령은 법령 이름 가나다순 (임베딩 batch·data/decree_exp 후보 번호가 이 순서에 묶여 있음)"""
    order = b.doc_order()
    scoped = {law for law, _ in b.load_scope(b.TAX_SCOPE)}
    laws = [entry for entry in law_scope.load_law_scope() if entry.name not in scoped]
    law_count = len(laws)
    assert order[:law_count] == [(entry.name, '법률') for entry in laws]
    assert order[law_count:] == [
        (entry.name, kind) for entry in sorted(laws, key=lambda law: law.name) for kind in entry.decrees
    ]


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


def test_full_laws_use_manifest_names_and_exclude_v5_article_selections(monkeypatch):
    first, *others = [law for law, kind in b.doc_order() if kind == '법률']
    renamed_scope = tuple(
        entry.model_copy(update={'name': '시험법'}) if entry.name == first else entry
        for entry in law_scope.load_law_scope()
    )
    monkeypatch.setattr(law_scope, 'load_law_scope', lambda: renamed_scope)

    laws = [law for law, kind in b.doc_order() if kind == '법률']
    assert laws == ['시험법', *others]
    assert not set(laws) & {law for law, _ in b.load_scope(b.TAX_SCOPE)}


def test_canonical_v5_scope_rejects_a_law_absent_from_manifest(tmp_path, monkeypatch):
    scope = tmp_path / 'scope.csv'
    scope.write_text('law,kind,article_no\n시험법,법률,제1조\n', encoding='utf-8')
    monkeypatch.setattr(b, 'TAX_SCOPE', scope)

    with pytest.raises(ValueError, match='조문 범위 목록의 법령이 공통 범위에 없습니다: 시험법'):
        b.load_scope(scope)
    with pytest.raises(ValueError, match='조문 범위 목록의 법령이 공통 범위에 없습니다: 시험법'):
        b.doc_order()


def test_alternate_scope_selects_partial_laws_and_restores_omitted_full_laws(tmp_path):
    alternate = tmp_path / 'alternate.csv'
    alternate.write_text('law,kind,article_no\n근로기준법,법률,제1조\n국민연금법,법률,제2조\n', encoding='utf-8')

    documents = b.docs(b.load_scope(alternate))

    assert [doc for doc in documents if doc[0] == '근로기준법'] == [('근로기준법', '법률', {'제1조'})]
    assert [doc for doc in documents if doc[0] == '소득세법'] == [('소득세법', kind, None) for kind in b.KINDS]
    assert len({(law, kind) for law, kind, _ in documents}) == len(documents)


def test_build_full_law_with_no_subordinate_documents(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, [('2020-01-01', '100', '2020-01-01', V1, '')])
    entry = law_scope.LawScope(name='시험법', folder='시험법', category=1, keywords=('시험법',), decrees=())
    monkeypatch.setattr(law_scope, 'load_law_scope', lambda: (entry,))

    _, _, stats = b.build(repo.path, '2026-10-20', scope={})

    assert stats['paths'] == {'시험법': PATH}
    assert stats['law_rows'] == stats['decree_rows'] == 3
    with pytest.raises(ValueError, match='시험법 시행규칙'):
        b.build(repo.path, '2026-10-20', scope={('시험법', '시행규칙'): {'제1조'}})


@pytest.fixture
def law_with_decree(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, [('2020-01-01', '100', '2020-01-01', V1, '')])
    entry = law_scope.LawScope(name='시험법', folder='시험법', category=1, keywords=('시험법',), decrees=('시행령',))
    selection = tmp_path / 'scope.csv'
    selection.write_text('law,kind,article_no\n', encoding='utf-8')
    monkeypatch.setattr(law_scope, 'load_law_scope', lambda: (entry,))
    monkeypatch.setattr(b, 'TAX_SCOPE', selection)
    decree = 'kr/시험법/시행령.md'
    first = md('101', '2021-01-01', '2021-01-01', article('제1조', '목적', '시행령 본문'))
    first = first.replace('시험법', '시험법 시행령')
    commit(repo.path, {decree: first}, '2021-01-01', '대통령령: 시험법 시행령 (제정)')
    return repo, decree


@pytest.mark.parametrize('damage', ['title-changed', 'path-deleted', 'duplicate-title'])
def test_build_rejects_missing_or_ambiguous_required_subordinate_document(law_with_decree, damage):
    repo, decree = law_with_decree
    _, _, before = b.build(repo.path, '2022-01-01')
    assert before['paths'] == {'시험법': PATH, '시험법 시행령': decree}

    changes = {}
    if damage == 'title-changed':
        body = article('제1조', '목적', '시행령 본문')
        changes[decree] = md('102', '2023-01-01', '2023-01-01', body).replace('시험법', '다른 법 시행령')
    elif damage == 'duplicate-title':
        changes['kr/시험법/시행령(대통령령).md'] = (repo.path / decree).read_text(encoding='utf-8')
    else:
        (repo.path / decree).unlink()
    commit(repo.path, changes, '2023-01-01', '대통령령: 시험법 시행령 (일부개정)')

    with pytest.raises(ValueError, match='시험법 시행령'):
        b.build(repo.path, '2026-10-20')


def test_docs_put_scoped_documents_after_the_full_ones():
    """전체 문서 뒤에 범위 목록 문서를 법률 → 하위법령 이름순으로 붙인다."""
    scope = {('소득세법', '시행령'): {'제2조'}, ('국민연금법', '법률'): {'제1조'}, ('소득세법', '법률'): {'제1조'}}
    docs = b.docs(scope)
    order = b.doc_order(scope)
    doc_count = len(order)
    assert docs[:doc_count] == [(law, kind, None) for law, kind in order]
    assert docs[doc_count:] == [  # 법률은 목록에 처음 나온 법령 순서
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
