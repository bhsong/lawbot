# lawbot_model_server/config.py  →  모델 서버 설정 (.env의 LAWBOT_* 중 서버가 읽는 값). 모델 라이브러리를 import하지 않음
# 주소는 127.0.0.1 고정, 포트는 LAWBOT_MODEL_SERVER_PORT (코드 기본값 없음), 토큰은 LAWBOT_MODEL_TOKEN (16자 이상)
# 제공할 모델은 'id=경로'를 쉼표로: LAWBOT_SERVE_CHAT·LAWBOT_SERVE_EMBEDDING·LAWBOT_SERVE_RERANK. 상대 경로는 레포 최상위 기준
import os
import re
from dataclasses import dataclass
from pathlib import Path

from lawbot_common.ports import parse_port
from lawbot_model_server.envfile import ROOT

HOST = '127.0.0.1'  # 고정값: 설정으로 바꾸지 않음 (질문이 맥 밖으로 나가지 않게)
TOKEN_MIN = 16
KINDS = {'chat': 'LAWBOT_SERVE_CHAT', 'embedding': 'LAWBOT_SERVE_EMBEDDING', 'rerank': 'LAWBOT_SERVE_RERANK'}
MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')


@dataclass(frozen=True)
class ModelEntry:
    id: str  # API의 model 값
    kind: str  # 'chat' / 'embedding' / 'rerank'
    path: Path  # 로컬 모델 폴더 (절대 경로)


@dataclass(frozen=True)
class Config:
    port: int
    token: str
    models: tuple  # ModelEntry
    preload: tuple  # 시작할 때 올릴 chat 모델 id (첫 번째는 실패하면 시작 중단, 나머지는 경고만)
    max_resident: int  # 동시에 올려 두는 chat 모델 수 (LRU). embedding·rerank는 항상 상주
    max_concurrent: int = 1  # 동시에 생성하는 chat 요청 수 (배치 크기). 1이면 PR 2와 같은 답 (2026-11-01 val 확인)
    max_queued: int = 32  # 생성을 기다리는 chat 요청 상한. 넘으면 503 server_busy


def port():
    value = (os.environ.get('LAWBOT_MODEL_SERVER_PORT') or '').strip()
    if not value:
        raise RuntimeError('모델 서버 포트가 없어 실행을 거부합니다: .env에 LAWBOT_MODEL_SERVER_PORT를 적으세요.')
    return parse_port('LAWBOT_MODEL_SERVER_PORT', value)


def token():
    """값은 출력하지 않는다"""
    value = (os.environ.get('LAWBOT_MODEL_TOKEN') or '').strip()
    if len(value) < TOKEN_MIN:
        raise RuntimeError(
            f'LAWBOT_MODEL_TOKEN이 없거나 {TOKEN_MIN}자 미만이라 실행을 거부합니다 (openssl rand -hex 32로 만들어 .env에 적기)'
        )
    return value


def _path(value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


def parse_models(kind, value):
    """'id=경로,id=경로' → ModelEntry 목록"""
    name, out = KINDS[kind], []
    for item in (value or '').split(','):
        item = item.strip()
        if not item:
            continue
        mid, sep, path = (x.strip() for x in item.partition('='))
        if not sep or not path or not MODEL_ID.fullmatch(mid):
            raise ValueError(f"{name}는 'id=경로'를 쉼표로 (id는 영문·숫자·._:-): {item!r}")
        out.append(ModelEntry(mid, kind, _path(path)))
    return out


def models():
    out = [e for kind, name in KINDS.items() for e in parse_models(kind, os.environ.get(name))]
    ids = [e.id for e in out]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f'모델 id가 겹칩니다: {dup}')
    if not out:
        raise RuntimeError(f'제공할 모델이 없습니다: .env에 {", ".join(KINDS.values())} 중 하나 이상을 적으세요.')
    return tuple(out)


def max_resident():
    raw = os.environ.get('LAWBOT_MAX_RESIDENT') or '2'
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f'LAWBOT_MAX_RESIDENT는 1 이상의 정수: {raw!r}')
    return int(raw)


MAX_CONCURRENT_LIMIT = 16


def max_concurrent():
    raw = (os.environ.get('LAWBOT_MAX_CONCURRENT') or '1').strip()
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_CONCURRENT_LIMIT:
        raise ValueError(f'LAWBOT_MAX_CONCURRENT는 1–{MAX_CONCURRENT_LIMIT} 정수: {raw!r}')
    return int(raw)


MAX_QUEUED_LIMIT = 1024


def max_queued():
    raw = (os.environ.get('LAWBOT_MAX_QUEUED') or '32').strip()
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_QUEUED_LIMIT:
        raise ValueError(f'LAWBOT_MAX_QUEUED는 1–{MAX_QUEUED_LIMIT} 정수: {raw!r}')
    return int(raw)


def preload(entries, resident):
    """LAWBOT_SERVE_PRELOAD (chat id 쉼표). 없으면 chat 모델 앞에서부터 상주 수만큼"""
    chat = [e.id for e in entries if e.kind == 'chat']
    value = os.environ.get('LAWBOT_SERVE_PRELOAD')
    if value is None:
        return tuple(chat[:resident])
    ids = tuple(i.strip() for i in value.split(',') if i.strip())
    bad = [i for i in ids if i not in chat]
    if bad:
        raise ValueError(f'LAWBOT_SERVE_PRELOAD에 chat 모델이 아닌 id: {bad} (chat 모델 {chat})')
    if len(ids) > resident:
        raise ValueError(f'LAWBOT_SERVE_PRELOAD({len(ids)}개)가 LAWBOT_MAX_RESIDENT({resident})보다 많습니다')
    return ids


def load():
    """포트·토큰을 먼저 확인한다 (모델을 올리기 전에 실행 거부)"""
    p, t = port(), token()
    entries, resident = models(), max_resident()
    return Config(p, t, entries, preload(entries, resident), resident, max_concurrent(), max_queued())
