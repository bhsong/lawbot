"""Fence deterministic calculations against changes to their legal text."""

import csv
from collections.abc import Iterable
from io import StringIO
from pathlib import Path

from lawbot.calculators import ARTICLE_HASHES
from lawbot.corpus_store import active_article_bodies
from lawbot.exact_cache import digest

_REQUIRED_IDS = frozenset(key for dependencies in ARTICLE_HASHES.values() for key in dependencies)


def _article_hashes(rows: Iterable[tuple[str, str, str | None]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    for key, deleted, text in rows:
        if key in seen:
            hashes.pop(key, None)
            continue
        seen.add(key)
        if deleted == '0' and isinstance(text, str):
            hashes[key] = digest(text)
    return hashes


def _compatibility(found: dict[str, str]) -> dict[str, tuple[str, ...]]:
    return {
        calculator: tuple(key for key, expected in dependencies.items() if found.get(key) != expected)
        for calculator, dependencies in ARTICLE_HASHES.items()
    }


def calculator_compatibility(source: Path | bytes | None) -> dict[str, tuple[str, ...]]:
    """Return each calculator's mismatching or missing IDs. Empty tuples are compatible."""
    if source is None:
        return _compatibility({})
    try:
        raw = source.read_bytes() if isinstance(source, Path) else source
        rows = csv.DictReader(StringIO(raw.decode('utf-8-sig'), newline=''))
        found = _article_hashes(
            (row['article_id'], row['deleted'], row['text']) for row in rows if row['article_id'] in _REQUIRED_IDS
        )
    except OSError, UnicodeError, KeyError, csv.Error:
        found = {}
    return _compatibility(found)


def database_calculator_compatibility(db: Path | None, profile: str) -> dict[str, tuple[str, ...]]:
    """Check required bodies from the active release in one read-only SQLite snapshot."""
    if db is None:
        return _compatibility({})
    try:
        found = _article_hashes(active_article_bodies(db, profile, _REQUIRED_IDS))
    except OSError, RuntimeError:
        found = {}
    return _compatibility(found)


def calculator_report(source: Path | bytes | None) -> list[tuple[str, str]]:
    return [
        ('WARN', f'계산기 {calculator} 꺼짐 (본문 변경·누락 조문 {", ".join(ids)})')
        if ids
        else ('OK', f'계산기 {calculator} 켜짐 (근거 조문 본문 일치)')
        for calculator, ids in calculator_compatibility(source).items()
    ]
