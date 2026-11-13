"""Evaluate robots.txt for the collector's fixed ASCII request paths (RFC 9309)."""

import re
from dataclasses import dataclass, field

PRODUCT = 'LawbotSourceHub'
UNRESERVED = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'


@dataclass
class Group:
    agents: set[str] = field(default_factory=set)
    rules: list[tuple[str, str]] = field(default_factory=list)


def normalize(pattern: str) -> str:
    def unescape(match: re.Match[str]) -> str:
        character = chr(int(match[1], 16))
        return character if character in UNRESERVED else match[0].upper()

    return re.sub(r'%([0-9a-fA-F]{2})', unescape, pattern)


def matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith('$')
    if anchored:
        pattern = pattern[:-1]
    prefix, *parts = pattern.split('*')
    if not parts:
        return path == prefix if anchored else path.startswith(prefix)
    if not path.startswith(prefix):
        return False
    remainder = path[len(prefix) :]
    if anchored and (suffix := parts.pop()):
        if not remainder.endswith(suffix):
            return False
        remainder = remainder[: -len(suffix)]
    for part in parts:
        index = remainder.find(part)
        if index == -1:
            return False
        remainder = remainder[index + len(part) :]
    return True


def access_failure(lines: list[str], path: str) -> str | None:
    groups: list[Group] = []
    group = Group()
    for raw in lines:
        key, _, value = raw.partition('#')[0].partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'user-agent':
            if not re.fullmatch(r'[A-Za-z_-]+|\*', value):
                return 'robots_invalid'
            if group.rules:
                groups.append(group)
                group = Group()
            group.agents.add(value.lower())
        elif key in {'allow', 'disallow', 'crawl-delay', 'request-rate'} and group.agents:
            group.rules.append((key, value))
    if group.agents:
        groups.append(group)
    if not groups:
        return 'robots_invalid'
    selected = [group for group in groups if PRODUCT.lower() in group.agents]
    selected = selected or [group for group in groups if '*' in group.agents]
    applicable = []
    for group in selected:
        for key, value in group.rules:
            if key in {'crawl-delay', 'request-rate'}:
                return 'robots_rate_review'
            if not value:
                continue
            if not value.startswith('/') or any(character.isspace() for character in value):
                return 'robots_invalid'
            pattern = normalize(value)
            if matches(pattern, path):
                applicable.append((len(pattern.encode('utf-8')), key == 'allow'))
    return 'robots_denied' if applicable and not max(applicable)[1] else None
