"""Shared, content-addressed daily ERP publication contract."""

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

LIMIT = 16 * 1024 * 1024
AGENCIES = ('lawgo', 'moel', 'mofe', 'nts', 'hometax')


class SourceStatus(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sourceId: str
    outcome: Literal['ok', 'partial', 'blocked', 'error', 'disabled', 'unverified', 'not_due', 'busy']
    runId: str | None = None
    newCount: int | None = Field(default=None, ge=0)
    modifiedCount: int | None = Field(default=None, ge=0)
    lastSuccessAt: AwareDatetime | None = None
    lastAttemptAt: AwareDatetime | None = None
    stale: bool = False


class DailySnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schemaVersion: Literal[1]
    dayKst: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    cutoffAt: AwareDatetime
    runId: str
    configVersion: int = Field(ge=0)
    configHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['ok', 'partial']
    sources: list[SourceStatus]
    feeds: dict
    changes: list[dict]
    snapshotHash: str = Field(pattern=r'^[a-f0-9]{64}$')


def encode(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def snapshot_hash(data: dict) -> str:
    return hashlib.sha256(encode(data)).hexdigest()


def seal(data: dict) -> dict:
    raw = DailySnapshot.model_validate(data | {'snapshotHash': '0' * 64}).model_dump(
        mode='json', exclude={'snapshotHash'}
    )
    raw['snapshotHash'] = snapshot_hash(raw)
    return raw


def read_snapshot(path: Path) -> dict:
    with path.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('ERP daily snapshot exceeds the size limit')
    parsed = DailySnapshot.model_validate_json(data)
    if snapshot_hash(parsed.model_dump(mode='json', exclude={'snapshotHash'})) != parsed.snapshotHash:
        raise ValueError('ERP daily snapshot hash mismatch')
    return parsed.model_dump(mode='json')


def replace_file(path: Path, data: dict, *, limit: int = LIMIT) -> None:
    encoded = encode(data)
    if len(encoded) > limit:
        raise ValueError('ERP daily file exceeds the size limit')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(encoded)
            stream.close()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
