"""Parse source quantities without relying on a model's numeric conversions."""

import re
import unicodedata
from collections.abc import Iterator
from decimal import Decimal
from typing import Literal

_DIGITS = '영공일이삼사오육칠팔구'
_VALUES = str.maketrans(_DIGITS, '00123456789')
_ARABIC = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
_SMALL_UNITS = '천백십'
_LARGE_UNITS = '조억만'
_SMALL_VALUES = dict(zip(_SMALL_UNITS, (1000, 100, 10)))
_LARGE_VALUES = dict(zip(_LARGE_UNITS, (10**12, 10**8, 10**4)))
_CHARACTERS = f'{_DIGITS}{_SMALL_UNITS}{_LARGE_UNITS}'
_MONEY_START = f'0-9０-９{_CHARACTERS}'
_MONEY_CHARS = f'{_MONEY_START}.,，．'
_MONEY_TOKEN = re.compile(
    rf'(?:(?<![0-9０-９.,，．])|(?<=원[,，]))'
    rf'(?P<sign>[-−]?[ \t]*)(?P<amount>[{_MONEY_START}][{_MONEY_CHARS}]*'
    rf'(?:[ \t]+[{_MONEY_CHARS}]+)*[ \t]*원)'
)
_MAX_SAFE_INTEGER = 2**53 - 1


def _plain_number(text: str) -> Decimal:
    if text and all(char in _DIGITS for char in text):
        return Decimal(text.translate(_VALUES))
    if not re.fullmatch(_ARABIC, text) or len(text) > 25:
        raise ValueError('invalid number')
    return Decimal(text.replace(',', ''))


def _small_number(text: str) -> Decimal:
    if not any(unit in text for unit in _SMALL_UNITS):
        return _plain_number(text)
    total, previous = Decimal(0), 10_000
    for coefficient, unit in re.findall(r'([^천백십]*)([천백십]|$)', text):
        if not coefficient and not unit:
            continue
        value = _plain_number(coefficient) if coefficient else Decimal(1)
        place = _SMALL_VALUES[unit] if unit else 1
        if place >= previous or not 0 < value * place < previous:
            raise ValueError('invalid unit order')
        if unit and value >= 10:
            raise ValueError('invalid unit coefficient')
        total += value * place
        previous = place
    return total


def _amount_in_won(text: str) -> Decimal:
    value = unicodedata.normalize('NFKC', text).strip()
    with_won = value.endswith('원')
    value = value.removesuffix('원').strip()
    if not re.fullmatch(rf'[0-9.,{_CHARACTERS}]+(?:[ \t]+[0-9.,{_CHARACTERS}]+)*', value):
        raise ValueError('invalid amount')
    # A space can separate unit groups, but cannot join two coefficients.
    if re.search(rf'[0-9{_DIGITS}][ \t]+[0-9{_DIGITS}]', value):
        raise ValueError('separated coefficients')
    value = ''.join(value.split())
    if len(value) > 35:
        raise ValueError('invalid amount length')
    total, previous = Decimal(0), 10**16
    for coefficient, unit in re.findall(r'([^조억만]*)([조억만]|$)', value):
        if not coefficient and not unit:
            continue
        number = _small_number(coefficient) if coefficient else Decimal(1)
        place = _LARGE_VALUES[unit] if unit else 1
        if place >= previous or not 0 <= number * place < previous:
            raise ValueError('invalid unit order')
        if not unit and previous != 10**16 and not with_won and not any(u in coefficient for u in _SMALL_UNITS):
            raise ValueError('ambiguous remainder')
        total += number * place
        previous = place
    return total


def parse_amount(text: str) -> int:
    """Read a nonnegative, exact amount in won, with optional Korean unit groups."""
    amount = _amount_in_won(text)
    if amount != amount.to_integral_value() or amount > _MAX_SAFE_INTEGER:
        raise ValueError('amount is not an exact supported integer')
    return int(amount)


def parse_quantity(text: str, unit: Literal['%', '개월']) -> Decimal:
    """Read a percentage in percentage points or a duration in whole months."""
    value = unicodedata.normalize('NFKC', text).strip()
    if not value.endswith(unit):
        raise ValueError('unit is missing')
    number = _plain_number(value.removesuffix(unit).strip())
    if unit == '개월' and number != number.to_integral_value():
        raise ValueError('duration is not a whole number of months')
    return number


def parse_year(text: str) -> int:
    source = unicodedata.normalize('NFKC', text).strip()
    if not (match := re.fullmatch(r'([0-9]{4})(?:\s*년도?)?', source)):
        raise ValueError('invalid tax year')
    return int(match[1])


def amount_spans(text: str) -> Iterator[tuple[str, int | Decimal]]:
    """Yield explicit won amounts, including fractional values displayed in calculation steps."""
    previous_end: int | None = None
    for match in _MONEY_TOKEN.finditer(text):
        try:
            amount = _amount_in_won(match['amount'])
        except ValueError:
            continue
        value = int(amount) if amount == amount.to_integral_value() else amount
        source = match[0].strip()
        if match['sign'].strip():
            # A minus between adjacent won amounts is a subtraction operator.
            if previous_end is not None and not text[previous_end : match.start('sign')].strip(' \t'):
                source = match['amount']
            else:
                value = -value
        previous_end = match.end()
        yield source, value


def complete_amount_span(question: str, source: str) -> bool:
    """Reject excerpts taken from inside a longer number or its units."""
    normalized_source = unicodedata.normalize('NFKC', source).strip()
    for match in re.finditer(re.escape(source), question):
        before = unicodedata.normalize('NFKC', question[: match.start()])
        after = unicodedata.normalize('NFKC', question[match.end() :])
        if re.search(rf'[0-9{_SMALL_UNITS}{_LARGE_UNITS}]$|[0-9][.,]$|[-−+~～]\s*$', before):
            continue
        if re.search(rf'[{_DIGITS}]$', before) and re.match(rf'[{_CHARACTERS}]', normalized_source):
            continue
        if re.match(r'[.,]?[0-9]', after):
            continue
        if re.search(rf'[0-9{_CHARACTERS}]\s*[{_SMALL_UNITS}{_LARGE_UNITS}][ \t]+$', before):
            continue
        if normalized_source.endswith('원'):
            return True
        if re.match(rf'\s*(?:[{_SMALL_UNITS}{_LARGE_UNITS}%]|년|개월|시간|일|세|명)', after):
            continue
        if re.match(rf'\s*[{_DIGITS}]+(?:[{_SMALL_UNITS}{_LARGE_UNITS}]|\s*원|$)', after):
            continue
        if re.search(rf'[{_SMALL_UNITS}{_LARGE_UNITS}]$', normalized_source) and re.match(r'\s*[0-9]', after):
            continue
        return True
    return False
