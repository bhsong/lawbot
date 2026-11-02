# tests/test_build_articles.py  →  조문 CSV 생성기 (lawbot_tools.build_articles, 계획 docs/plans/article-generator.md)
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


def test_schedule_fails_on_late_date_it_cannot_place():
    """'②'로 나눈 형식처럼 '다만'이 아닌 곳의 기준일 뒤 날짜를 못 읽으면 조용히 넘어가지 않는다"""
    add = (
        '부칙 <제300호,2026.6.1>\n\n제1조(시행일) ① 이 법은 공포한 날부터 시행한다.\n\n'
        '② 제2조의 개정규정은 2027년 1월 1일부터 시행한다.\n\n\n제2조(경과조치) 생략\n'
    )
    m = md('300', '2026-06-01', '2026-06-01', '', add)
    with pytest.raises(ValueError, match='읽지 못했습니다'):
        b.schedule(m, b.front(m), ORDER, '2026-10-20')
    assert b.schedule(m, b.front(m), ORDER, '2028-01-01').main == '2026-06-01'  # 모두 지난 날짜면 상관없음


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
    arts, used = b.law_in_force(repo, PATH, '2026-10-20')
    got = {a['no']: (a['text'], a['eff']) for a in arts}
    assert got == {
        '제1조': ('바뀐 목적 <개정 2026.4.7>', '2026-10-08'),  # 본 시행일 지남 → 새 본문
        '제2조': ('처음 정의', '2020-01-01'),  # 단서 시행일 전 → 이전 본문
        '제3조': ('처음 벌칙', '2020-01-01'),
    }  # 제4조(시행 전 신설)는 없음
    assert used[-1]['시행 전'] == 2
    later = {a['no']: a['text'] for a in b.law_in_force(repo, PATH, '2027-01-01')[0]}
    assert later['제2조'] == '시행 전 정의 <개정 2026.4.7>' and '제4조' in later


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
    got = {a['no']: (a['text'], a['eff']) for a in b.law_in_force(repo, PATH, '2026-10-20')[0]}
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
        b.law_in_force(repo, PATH, '2026-10-20')


# ── 조문 참조·보정·CSV ──────────────────────────────────────────────

NAMES = b.doc_names()


def test_refs_skip_articles_of_another_laws_addendum():
    """'법률 제7379호 근로자퇴직급여보장법 부칙 제2조제1항'은 이 법 제2조가 아니다 (임금채권보장법 제10조)"""
    text = '제9조에 따른 부담금. 3. 법률 제7379호 근로자퇴직급여보장법 부칙 제2조제1항에 따른 퇴직보험'
    own = '임금채권보장법'
    assert [(d, n) for d, n, _ in b.refs(text, own, {}, NAMES) if d == own] == [(own, '제9조')]


def test_overrides_apply_and_fail_when_source_changed():
    rows = [{'article_id': 'A 제1조', 'text': '2. 대상 근로자의 범위\n3. 끝', 'deleted': 0}]
    b.apply_overrides(
        rows,
        [
            {'article_id': 'A 제1조', 'field': 'text', 'find': '범위\n', 'replace': '범위 [실효]\n'},
            {'article_id': 'A 제1조', 'field': 'deleted', 'set': 1},
        ],
    )
    assert rows[0] == {'article_id': 'A 제1조', 'text': '2. 대상 근로자의 범위 [실효]\n3. 끝', 'deleted': 1}
    with pytest.raises(ValueError, match='찾지 못했습니다'):
        b.apply_overrides(rows, [{'article_id': 'A 제1조', 'field': 'text', 'find': '없는 문구', 'replace': ''}])
    with pytest.raises(ValueError, match='대상 조문이 없습니다'):
        b.apply_overrides(rows, [{'article_id': 'A 제9조', 'field': 'deleted', 'set': 1}])


def test_override_file_has_reasons_and_known_fields():
    for o in b.load_overrides():
        assert o['reason'] and o['field'] in b.LAW_COLS + ['parent_ids', 'child_ids']
        assert ('set' in o) != ('find' in o and 'replace' in o)


def test_csv_bytes_bom_and_line_endings():
    import pandas as pd

    df = pd.DataFrame({'a': ['줄1\n줄2', None], 'b': [1, 0]})
    crlf, lf = b.to_csv_bytes(df, '\r\n'), b.to_csv_bytes(df, '\n')
    assert crlf.startswith('﻿'.encode()) and crlf.count(b'\r\n') == 3
    assert b'"\xec\xa4\x841\n\xec\xa4\x842"' in crlf  # 본문 안 줄바꿈은 LF 그대로
    assert b'\r' not in lf


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
