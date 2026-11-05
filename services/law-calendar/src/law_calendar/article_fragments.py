"""Partition changed saved law text by exact paragraph and item scope."""

import re
from collections.abc import Collection

Path = tuple[int, int]
Fragment = tuple[str, str | None, str | None]

CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿'
HEADER = re.compile(r'^#####[ \t]*(제[1-9]\d*조(?:의[1-9]\d*)*)(?=[ \t(\r\n]|$)')
PARAGRAPH = re.compile(rf'^\*\*([{CIRCLED}])\*\*(?=[ \t\r\n]|$)')
ITEM = re.compile(r'^ {2}([1-9]\d*)\\\.[ \t]+')
OTHER_MARKER = re.compile(rf'^[ \t]*(?:\*{{0,2}}[{CIRCLED}]|\d+(?:의\d+)*\\?\.[ \t]+)')
SCOPE = re.compile(r'(?:제([1-9]\d*)항)?(?:제([1-9]\d*)호)?')
DATE = r'\d{4}\.[ \t]*\d{1,2}\.[ \t]*\d{1,2}\.?'
ANNOTATION = re.compile(
    rf'[ \t]+<(?:개정|신설|전문개정|제목개정)[ \t]+{DATE}(?:,[ \t]*{DATE})*>[ \t]*(?=\r?$)', re.MULTILINE
)


def _parts(text: str) -> tuple[str, dict[Path, str]] | None:
    header = HEADER.match(text)
    if not header:
        return None
    parts = {}
    path = (0, 0)
    start = offset = 0
    for line in text.splitlines(keepends=True):
        if match := PARAGRAPH.match(line):
            next_path = (CIRCLED.index(match[1]) + 1, 0)
        elif match := ITEM.match(line):
            next_path = (path[0], int(match[1]))
        else:
            if OTHER_MARKER.match(line) or (path[1] and line.strip() and not line.startswith('    ')):
                return None
            offset += len(line)
            continue
        if next_path <= path:
            return None
        parts[path] = text[start:offset]
        path, start = next_path, offset
        offset += len(line)
    parts[path] = text[start:]
    return header[1], parts


def _label(path: Path) -> str:
    paragraph, item = path
    return (f'제{paragraph}항' if paragraph else '') + (f'제{item}호' if item else '')


def _comparison_text(text: str | None) -> str | None:
    return ANNOTATION.sub('', text).strip() if text is not None else None


def partition_changes(before: str, after: str, scopes: Collection[str]) -> dict[str | None, list[Fragment]] | None:
    """Return exact changed spans grouped by the proviso scope or the default (None)."""
    old = _parts(before)
    new = _parts(after)
    if old is None or new is None or old[0] != new[0]:
        return None

    scope_paths = {}
    for scope in scopes:
        match = SCOPE.fullmatch(scope)
        if not match or not scope:
            return None
        path = (int(match[1] or 0), int(match[2] or 0))
        if path in scope_paths:
            return None
        scope_paths[path] = scope
    if any(item and (paragraph, 0) in scope_paths for paragraph, item in scope_paths):
        return None

    old_parts, new_parts = old[1], new[1]
    groups: dict[str | None, list[Fragment]] = {}
    for path in sorted(old_parts.keys() | new_parts.keys()):
        old_text = old_parts.get(path)
        new_text = new_parts.get(path)
        if _comparison_text(old_text) == _comparison_text(new_text):
            continue
        scope = scope_paths.get(path) or scope_paths.get((path[0], 0))
        groups.setdefault(scope, []).append((_label(path), old_text, new_text))
    return groups if all(scope in groups for scope in scope_paths.values()) else None
