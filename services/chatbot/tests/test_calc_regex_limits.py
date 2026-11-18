"""Calculator searches must stay bounded on long whitespace runs."""

import importlib
import re
import signal
import time

import pytest

MODULES = (
    'labor_annual',
    'labor_dates',
    'labor_language',
    'labor_minimum',
    'labor_premium',
    'labor_severance',
    'labor_weekly',
    'calc_numbers',
)
PREFIXES = (
    '',
    '연장',
    '야간',
    '휴일',
    '총 근로시간',
    '근로시간',
    '휴일 하루',
    '이내',
    '관리',
    '감시',
    '상시',
    '출근율',
    '월 적용 기준 시간',
    '주 소정',
    '주휴',
    '퇴직일',
    '1',
    '1시간',
    '소정',
    '시급',
)


def _patterns():
    for module in MODULES:
        for name, value in vars(importlib.import_module(f'lawbot.{module}')).items():
            entries = value.items() if isinstance(value, dict) else [(name, value)]
            for key, pattern in entries:
                if isinstance(pattern, re.Pattern):
                    yield pytest.param(pattern, id=f'{module}.{name}.{key}')


def _expired(*_):
    raise TimeoutError('calculator regex exceeded the safety deadline')


@pytest.mark.parametrize('pattern', list(_patterns()))
@pytest.mark.parametrize('size', [200, 2000, 8000])
def test_calculator_patterns_bound_whitespace_search(pattern, size):
    previous = signal.signal(signal.SIGALRM, _expired)
    try:
        for prefix in PREFIXES:
            text = prefix + ' ' * size + '가'
            signal.setitimer(signal.ITIMER_REAL, 0.5)
            start = time.perf_counter()
            pattern.search(text)
            elapsed = time.perf_counter() - start
            signal.setitimer(signal.ITIMER_REAL, 0)
            assert elapsed < 0.1, (prefix, size, elapsed)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize('sign', ['', '-', '−'])
def test_money_sign_preserves_quantity_offsets(sign):
    from lawbot.calc_numbers import _QUANTITY_PATTERNS, amount_spans, quantity_excerpt

    prefix = '금액 \t'
    quantity = f'{sign} \t123원' if sign else '123원'
    text = prefix + quantity
    match = _QUANTITY_PATTERNS['money'].search(text)
    assert match is not None
    assert match.span() == (len(prefix), len(text))
    assert match['sign'] == (f'{sign} \t' if sign else '')
    assert match.start('sign') == len(prefix)
    assert quantity_excerpt(text, 'money') == quantity
    assert list(amount_spans(text)) == [(quantity, -123 if sign else 123)]


@pytest.mark.parametrize('label', ['연장', '야간'])
@pytest.mark.parametrize('work', ['', '근로', '실근로', '근무'])
@pytest.mark.parametrize('unit', ['', '시간'])
@pytest.mark.parametrize('particle', ['', '은', '는', '이', '가', '를', '을', '에', ':', '：'])
@pytest.mark.parametrize('space', ['', ' ', '\t\n'])
def test_labeled_premium_hours_preserve_optional_words(label, work, unit, particle, space):
    from lawbot.labor_premium import question_issue

    text = space.join([label, work, unit, particle, '2시간'])
    assert '실근로시간' in (question_issue(text, {}) or '')
