"""변경 이벤트 저장. event_id는 내용 기반이라 같은 변경을 여러 번 저장해도 파일이 하나다."""

import hashlib
import json
import os
from pathlib import Path


def make_event_id(source: str, item_id: str, version_key: str) -> str:
    return hashlib.sha1(f'{source}|{item_id}|{version_key}'.encode()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


class EventStore:
    """events/YYYY-MM-DD/<id>.json (+ <id>.diff). dated=False면 base 폴더에 바로 (replay)."""

    def __init__(self, home: Path, base: Path, *, dated: bool = True):
        self.home, self.base, self.dated = home, base, dated

    def folder(self, event: dict) -> Path:
        return self.base / event['detected_at'][:10] if self.dated else self.base

    def write(self, event: dict, diff_text: str | None = None) -> Path:
        folder = self.folder(event)
        if diff_text:
            diff_path = folder / f'{event["event_id"]}.diff'
            _write_atomic(diff_path, diff_text if diff_text.endswith('\n') else diff_text + '\n')
            event['diff']['path'] = diff_path.relative_to(self.home).as_posix()
        path = folder / f'{event["event_id"]}.json'
        _write_atomic(path, json.dumps(event, ensure_ascii=False, indent=2) + '\n')
        return path
