import pytest

from lawbot.calc_numbers import amount_spans


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('각 2,000,000원, 200,000원', [('2,000,000원', 2_000_000), ('200,000원', 200_000)]),
        ('100만원, 20만원', [('100만원', 1_000_000), ('20만원', 200_000)]),
        ('100만원,20만원', [('100만원', 1_000_000), ('20만원', 200_000)]),
        ('금액은 １，０００원，−２００원입니다.', [('１，０００원', 1_000), ('−２００원', -200)]),
        ('1,000원, 지방소득세는 999,999원', [('1,000원', 1_000), ('999,999원', 999_999)]),
        ('오류 값은 1,00원, 다음 금액은 50원', [('50원', 50)]),
    ],
)
def test_amount_spans_distinguishes_list_separators_and_digit_grouping(text, expected):
    assert list(amount_spans(text)) == expected
