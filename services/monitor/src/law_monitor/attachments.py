"""별표·서식 비교 (계획 docs/archives/monitor-lawgo.md 2.3).

파일 바이트는 판마다 달라 쓸 수 없고(링크·PDF 모두 다시 만들어짐), 본문 API의 별표내용 텍스트로 비교한다.
"""

import re
from dataclasses import dataclass

from law_monitor.lawgo import flat, root

KEY = ('별표구분', '별표번호', '별표가지번호')
EFFECTIVE = re.compile(r'(\d{8}):(.*?)(?=\d{8}:|$)')


@dataclass(frozen=True)
class Change:
    kind: str  # changed | added | removed
    old: dict | None
    new: dict | None


def key(unit: dict) -> tuple[str, str, str]:
    return tuple(str(unit.get(k, '') or '') for k in KEY)


def item_id(unit: dict) -> str:
    kind, number, branch = key(unit)
    return f'{kind} {number}-{branch}'


def label(unit: dict) -> str:
    """별표시행일자문자열에 쓰는 이름: 서식21, 서식24의5, 별표2."""
    kind, number, branch = key(unit)
    out = f'{kind}{int(number) if number.isdigit() else number}'
    if branch.isdigit() and int(branch):
        out += f'의{int(branch)}'
    return out


def text(unit: dict) -> str:
    return '\n'.join(line.rstrip() for line in flat(unit.get('별표내용'))).strip('\n')


def units(body: dict | None) -> list[dict]:
    value = (root(body).get('별표') or {}).get('별표단위') if body else None
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_effective(value: str | None) -> dict[str, list[str]]:
    """'20260522:서식21,서식2320260701:서식24' → {'서식21': ['2026-05-22'], …}. 날짜 사이에 구분자가 없다."""
    out: dict[str, list[str]] = {}
    for day, names in EFFECTIVE.findall(value or ''):
        iso = f'{day[:4]}-{day[4:6]}-{day[6:]}'
        for name in filter(None, (n.strip() for n in names.split(','))):
            out.setdefault(name, []).append(iso)
    return out


def compare(old: dict | None, new: dict | None, *, kinds: tuple[str, ...] | None) -> list[Change]:
    def pick(body):
        return {key(u): u for u in units(body) if kinds is None or u.get('별표구분') in kinds}

    before, after = pick(old), pick(new)
    out = []
    for k, unit in after.items():
        if k not in before:
            out.append(Change('added', None, unit))
        elif text(before[k]) != text(unit):
            out.append(Change('changed', before[k], unit))
    out += [Change('removed', unit, None) for k, unit in before.items() if k not in after]
    return out
