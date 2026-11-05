"""Partition changed saved law text by exact paragraph, item and subitem scope."""

import re
from collections.abc import Mapping

Path = tuple[int, tuple[int, ...], str]
Fragment = tuple[str, str | None, str | None]

CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿'
HEADER = re.compile(r'^#####[ \t]*(제[1-9]\d*조(?:의[1-9]\d*)*)(?=[ \t(\r\n]|$)')
PARAGRAPH = re.compile(rf'^\*\*([{CIRCLED}])\*\*(?=[ \t\r\n]|$)')
ITEM = re.compile(r'^ {2}([1-9]\d*(?:의[1-9]\d*)*)\\\.[ \t]+')
SUBITEM = re.compile(r'^ {4}([가-힣])\\\.[ \t]+')
SUBITEM_DETAIL = re.compile(r'^[ \t\u3000]+\d+\)[ \t]+')
OTHER_MARKER = re.compile(rf'^[ \t]*(?:\*{{0,2}}[{CIRCLED}]|(?:\d+(?:의\d+)*|[가-힣])\\?\.[ \t]+)')
SCOPE = re.compile(r'(?:제([1-9]\d*)항)?(?:제([1-9]\d*(?:의[1-9]\d*)*)호)?([가-힣]목)?(본문)?')
DATE = r'\d{4}\.[ \t]*\d{1,2}\.[ \t]*\d{1,2}\.?'
ANNOTATION = re.compile(
    rf'[ \t]+<(?:개정|신설|전문개정|제목개정)[ \t]+{DATE}(?:,[ \t]*{DATE})*>[ \t]*(?=\r?$)', re.MULTILINE
)


def _item_number(value: str) -> tuple[int, ...]:
    return tuple(map(int, value.split('의'))) if value else ()


def _parts(text: str) -> tuple[str, dict[Path, str]] | None:
    header = HEADER.match(text)
    if not header:
        return None
    parts = {}
    path: Path = (0, (), '')
    start = offset = 0
    for line in text.splitlines(keepends=True):
        if match := PARAGRAPH.match(line):
            next_path = (CIRCLED.index(match[1]) + 1, (), '')
        elif match := ITEM.match(line):
            next_path = (path[0], _item_number(match[1]), '')
        elif (match := SUBITEM.match(line)) and path[1]:
            next_path = (path[0], path[1], match[1] + '목')
        else:
            nested_detail = path[2] and SUBITEM_DETAIL.match(line)
            if not nested_detail and (
                OTHER_MARKER.match(line) or (path[1] and line.strip() and not line.startswith('    '))
            ):
                return None
            offset += len(line)
            continue
        if next_path in parts or next_path == path:
            return None
        parts[path] = text[start:offset]
        path, start = next_path, offset
        offset += len(line)
    parts[path] = text[start:]
    return header[1], parts


def _label(path: Path) -> str:
    paragraph, item, subitem = path
    number = '의'.join(map(str, item))
    if len(item) > 1:
        number = f'{item[0]}호의' + '의'.join(map(str, item[1:]))
    elif item:
        number += '호'
    return (f'제{paragraph}항' if paragraph else '') + (f'제{number}' if item else '') + subitem


def _comparison_text(text: str | None) -> str | None:
    return ANNOTATION.sub('', text).strip() if text is not None else None


def _scope_path(scope: str) -> tuple[Path, bool] | None:
    match = SCOPE.fullmatch(re.sub(r'호의(\d+(?:의\d+)*)', r'의\1호', scope))
    if not match:
        return None
    paragraph, item, subitem, body = match.groups()
    return (int(paragraph or 0), _item_number(item or ''), subitem or ''), bool(body)


def _covers(target: Path, path: Path, body: bool, last: Path | None = None) -> bool:
    if last is not None:
        depth = 3 if target[2] else 2 if target[1] else 1
        return target[:depth] <= path[:depth] <= last[:depth]
    if body:
        return target == path
    paragraph, item, subitem = target
    if subitem:
        return target == path
    if item:
        return (paragraph, item) == path[:2]
    return not paragraph or paragraph == path[0]


def scope_contains(parent: str, child: str) -> bool:
    outer, inner = _scope_path(parent), _scope_path(child)
    return bool(outer and inner and outer[0] != inner[0] and _covers(outer[0], inner[0], outer[1]))


def exclusive_parent_text(before: str, after: str, scope: str) -> Fragment | None:
    """Find a parent whose substantive changes all belong to the specified child."""
    target, old, new = _scope_path(scope), _parts(before), _parts(after)
    if target is None or old is None or new is None:
        return None
    (paragraph, item, subitem), body = target
    if not item:
        return None
    parent: Path = (paragraph, item if subitem else (), '')
    old_parts, new_parts = old[1], new[1]
    if parent not in old_parts or parent not in new_parts:
        return None
    if any(
        _covers(parent, path, False)
        and not _covers(target[0], path, body)
        and _comparison_text(old_parts.get(path)) != _comparison_text(new_parts.get(path))
        for path in old_parts.keys() | new_parts.keys()
    ):
        return None
    return _label(parent), old_parts[parent], new_parts[parent]


def partition_changes(
    before: str, after: str, scopes: Mapping[str, str | None]
) -> dict[str | None, list[Fragment]] | None:
    """Return exact changed spans grouped by the proviso scope or the default (None)."""
    old = _parts(before)
    new = _parts(after)
    if old is None or new is None or old[0] != new[0]:
        return None

    scope_paths: dict[str, tuple[Path, bool, Path | None]] = {}
    for scope in scopes:
        start, _, end = scope.partition('~')
        target = _scope_path(start)
        last = _scope_path(end) if end else None
        if target is None or (end and (last is None or target[1] or last[1] or last[0] < target[0])):
            return None
        scope_paths[scope] = (*target, last[0] if last else None)

    old_parts, new_parts = old[1], new[1]
    groups: dict[str | None, list[Fragment]] = {}
    paths = old_parts.keys() | new_parts.keys()
    if any(
        not any(_covers(target, path, body) for path in paths)
        or (last is not None and not any(_covers(last, path, False) for path in paths))
        for target, body, last in scope_paths.values()
    ):
        return None
    for path in sorted(paths):
        old_text = old_parts.get(path)
        new_text = new_parts.get(path)
        if _comparison_text(old_text) == _comparison_text(new_text):
            continue
        matches = [scope for scope, (target, body, last) in scope_paths.items() if _covers(target, path, body, last)]
        matches.sort(key=lambda scope: (scopes[scope] is not None, -len(scope)))
        if (
            len({scopes[scope] for scope in matches if scopes[scope] is not None}) > 1
            and scopes[matches[0]] is not None
        ):
            return None
        scope = matches[0] if matches else None
        groups.setdefault(scope, []).append((_label(path), old_text, new_text))
    return groups
