from csv import DictReader
from itertools import pairwise
from pathlib import Path

import pytest

from lawbot_common.definition_links import Article, DefinitionLinks


def article(law, article_id, text):
    return Article(law, article_id, '제목', text)


def linked(*articles, total=80):
    filler = [article('나머지법', f'제{n}조', '다른 내용') for n in range(total - len(articles))]
    return DefinitionLinks([*articles, *filler])


def definition(law, text, article_id='제2조'):
    return article(law, article_id, text)


def test_parses_numbered_terms_and_only_related_paragraphs():
    links = linked(
        definition('원천법', '1. "계산단위"란 「대상법」 제2조의2제1항제6호에 따른 단위를 말한다.'),
        definition(
            '대상법',
            '① 용어의 뜻은 다음과 같다.\n'
            ' 6. "기준단위"이란 기준이 되는 값을 말한다.\n'
            '7. "그밖의단위"란 다른 값을 말한다.\n'
            '② 제1항제6호의 보충 규칙이다.\n'
            '1. 첫 번째 조건\n'
            '③ 제1항제7호에만 적용한다.\n'
            '④ 제1항제6호와 제1항제7호에 적용한다.',
            article_id='제2조의2',
        ),
        article('원천법', '제8조', '계산단위를 사용한다.'),
        article('다른법', '제8조', '계산단위를 사용한다.'),
        article('원천법', '제9조', '본문에 해당 용어가 없다.'),
    )
    (item,) = links.for_article('원천법', '제8조')
    assert (item.law, item.article_id, item.number, item.term) == ('대상법', '제2조의2', '6', '기준단위')
    assert item.text == (
        '6. "기준단위"이란 기준이 되는 값을 말한다.\n'
        '② 제1항제6호의 보충 규칙이다.\n'
        '1. 첫 번째 조건\n'
        '④ 제1항제6호와 제1항제7호에 적용한다.'
    )
    assert not links.for_article('원천법', '제2조')
    assert not links.for_article('다른법', '제8조')
    assert not links.for_article('원천법', '제9조')


@pytest.mark.parametrize('hops', [1, 3, 4])
def test_only_attaches_terminal_definition_within_three_hops(hops):
    laws = [f'법률{i}' for i in range(hops + 1)]
    links = linked(
        *(
            definition(law, f'1. "특수용어"란 「{target}」 제2조제1항제1호에 따른 값이다.')
            for law, target in pairwise(laws)
        ),
        definition(laws[-1], '1. "최종용어"란 최종 값이다.'),
        article(laws[0], '제8조', '특수용어로 정한다.'),
        total=200,
    )
    items = links.for_article(laws[0], '제8조')
    assert [(item.law, item.term) for item in items] == ([(laws[-1], '최종용어')] if hops <= 3 else [])


@pytest.mark.parametrize(
    'target_body',
    [
        '1. "특수용어"란 「원천법」 제2조제1항제1호에 따른 말이다.',
        '1. "특수용어"란 「원천법」 제3조제1항제1호에 따른 말이다.',
        '1. "특수용어"란 「누락법」 제2조제1항제1호에 따른 말이다.',
        '2. "다른용어"란 다른 뜻이다.',
    ],
)
def test_does_not_attach_cycles_same_law_endpoints_or_missing_refs(target_body):
    links = linked(
        definition('원천법', '1. "특수용어"란 「대상법」 제2조제1항제1호에 따른 말이다.'),
        definition('원천법', '1. "같은법용어"란 내부 정의이다.', article_id='제3조'),
        definition('대상법', target_body),
        article('원천법', '제8조', '특수용어와 같은법용어가 쓰였다.'),
    )
    assert not links.for_article('원천법', '제8조')


def test_frequency_counts_articles_once_and_excludes_only_above_five_percent():
    links = linked(
        definition(
            '원천법',
            '1. "한계어"란 「대상법」 제2조제1항제1호에 따른 말이다.\n'
            '2. "빈출어"란 「대상법」 제2조제1항제2호에 따른 말이다.',
        ),
        definition('대상법', '1. "기준말"이란 기준이다.\n2. "다른말"이란 예시다.'),
        article('원천법', '제8조', '한계어와 빈출어를 쓴다. 한계어를 다시 쓴다.'),
        article('다른법', '제1조', '빈출어를 쓴다.'),
        total=40,
    )
    assert [item.number for item in links.for_article('원천법', '제8조')] == ['1']


def test_v5_retirement_pay_links_only_average_wage_item_with_paragraph_two():
    csv_path = Path(__file__).resolve().parents[3] / 'data/v5/articles_v5.csv'
    with csv_path.open(encoding='utf-8-sig') as f:
        links = DefinitionLinks(
            Article(row['law'], row['article_no'], row['title'], row['text'])
            for row in DictReader(f)
            if row['deleted'] == '0'
        )

    (item,) = links.for_article('근로자퇴직급여 보장법', '제8조')
    assert (item.law, item.article_id, item.title, item.number) == ('근로기준법', '제2조', '정의', '6')
    line, paragraph = item.text.splitlines()
    assert line.startswith('6. "평균임금"이란')
    assert paragraph.startswith('② 제1항제6호에 따라')
    assert paragraph.endswith('통상임금보다 적으면 그 통상임금액을 평균임금으로 한다.')
    assert not links.for_article('근로자퇴직급여 보장법', '제2조')
