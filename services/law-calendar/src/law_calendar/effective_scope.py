"""Read the targets of a commencement rule using Korean legislative references."""

import re
from collections.abc import Iterator, Sequence
from itertools import pairwise

ARTICLE = r'제\d+조(?:의\d+)*'
PART_NUMBER = r'제?\d+[항호](?:의\d+)*'
PART = re.compile(rf'{PART_NUMBER}|[가-힣]목')
RANK = {'항': 0, '호': 1, '목': 2}
TITLE = re.compile(r'제\d+장(?:의\d+)*(?:제\d+절(?:의\d+)*)?의제목')
EARLIER = re.compile(
    r'(?:법률|대통령령|[가-힣]+부령|총리령)\s+제(\d+)호\s+([^,]+?)\s*(?:일부|전부)(?:개정법률|개정령)\s*'
)
REFERENCE = re.compile(rf'({ARTICLE}|같은[조항호목])?((?:{PART.pattern})*)(.*)')
BODY = re.compile(r'(?:각[호목]외의부분|본문)')
QUALIFIER = re.compile(r'(?:[([]|중|단서|전단|후단|제목)')
RANGE = re.compile(r'(.+?)(?:부터|내지)(.+)')
SEPARATORS = re.compile(r'[,ㆍ·]|\s+및\s+|(?<=[조항호목\d])[과와]\s+')
PAIR = {'(': ')', '[': ']', '「': '」', '“': '”', '"': '"'}


def split_targets(text: str) -> Iterator[str]:
    """Split coordinated references while keeping quoted and parenthesized qualifications."""
    tokens = re.compile(rf'{SEPARATORS.pattern}|[()\[\]「」“”"]')
    ends = []
    start = 0
    for match in tokens.finditer(text):
        token = match[0]
        if ends and token == ends[-1]:
            ends.pop()
        elif token in PAIR:
            ends.append(PAIR[token])
        elif not ends and SEPARATORS.fullmatch(token):
            yield text[start : match.start()].strip()
            start = match.end()
    yield text[start:].strip()


def _rank(part: str) -> int:
    return RANK[part.rstrip('0123456789의')[-1]]


def _reference(text: str, previous: tuple[str, ...]) -> tuple[tuple[str, ...], str] | None:
    match = REFERENCE.fullmatch(text)
    assert match is not None
    anchor, suffix, rest = match.groups()
    parts = tuple(token[0] if not token[0].isdigit() else f'제{token[0]}' for token in PART.finditer(suffix))
    ranks = [_rank(part) for part in parts]
    if any(a >= b for a, b in pairwise(ranks)):
        return None
    if anchor and not anchor.startswith('같은'):
        return (anchor, *parts), rest
    if not previous or not (anchor or parts):
        return None
    keep = '조항호목'.index(anchor[-1]) if anchor else ranks[0]
    prefix = tuple(part for part in previous[1:] if _rank(part) < keep)
    if anchor and keep and not any(_rank(part) == keep - 1 for part in prefix):
        return None
    if parts and prefix and ranks[0] <= _rank(prefix[-1]):
        return None
    return (previous[0], *prefix, *parts), rest


def _range(first: tuple[str, ...], last: tuple[str, ...], order: Sequence[str]) -> tuple[str, ...] | None:
    if len(first) == len(last) == 1:
        if first[0] not in order or last[0] not in order:
            return None
        start, end = order.index(first[0]), order.index(last[0])
        return tuple(order[start : end + 1]) if start <= end else None
    if first[:-1] != last[:-1] or _rank(first[-1]) != _rank(last[-1]):
        return None
    return (''.join(first) + '~' + ''.join(last[1:]),)


def parse_targets(text: str, order: Sequence[str], law: str) -> tuple[dict[str, tuple[bool, str]], bool] | None:
    """Return canonical scopes, whether each is exact, and whether article order was used."""
    scopes: dict[str, tuple[bool, str]] = {}
    previous: tuple[str, ...] = ()
    uses_order = False
    earlier = ''
    for label in split_targets(text):
        if prefix := EARLIER.match(label):
            earlier = prefix[1] if ''.join(prefix[2].split()) == ''.join(law.split()) else 'other'
            label = label[prefix.end() :]
            if label.startswith('(') and ')' in label:
                _, _, label = label.partition(')')
                label = label.lstrip()
                earlier = 'other'
        label = re.sub(r'\s+', '', label)
        label, _, qualification = label.partition('개정규정')
        closes_earlier = label.endswith('의')
        if closes_earlier:
            label = label[:-1]
        if label.startswith(('별표', '별지', '부칙')) or TITLE.fullmatch(label):
            if closes_earlier:
                earlier = ''
            continue
        if interval := RANGE.fullmatch(re.split(r'[("“]', label, maxsplit=1)[0]):
            start = _reference(interval[1], previous)
            stop = _reference(interval[2].removesuffix('까지'), start[0]) if start and not start[1] else None
            if not start or not stop or stop[1] or (expanded := _range(start[0], stop[0], order)) is None:
                if not start or not stop or start[0][0] != stop[0][0]:
                    return None
                scopes[start[0][0]] = (False, '')
                previous = stop[0]
                continue
            previous = stop[0]
            uses_order |= len(start[0]) == 1
            exact = not (qualification or '(' in label or '"' in label or '“' in label)
        else:
            resolved = _reference(label, previous)
            if not resolved:
                return None
            previous, rest = resolved
            body = BODY.match(rest)
            expanded = (''.join(previous) + ('본문' if body else ''),)
            rest = rest[body.end() :] if body else rest
            if rest and not QUALIFIER.match(rest):
                if re.search(ARTICLE, rest):
                    return None
                expanded = (previous[0],)
            exact = not (qualification or rest)
        for scope in expanded:
            prior, origin = scopes.get(scope, (True, earlier))
            scopes[scope] = (prior and exact and origin == earlier, earlier)
        if closes_earlier:
            earlier = ''
    return scopes, uses_order
