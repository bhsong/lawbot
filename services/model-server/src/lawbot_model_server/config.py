# lawbot_model_server/config.py  →  모델 서버 설정 (.env의 LAWBOT_* 중 서버가 읽는 값). 모델 라이브러리를 import하지 않음
# 주소는 127.0.0.1 고정, 포트는 LAWBOT_MODEL_SERVER_PORT (코드 기본값 없음), 토큰은 LAWBOT_MODEL_TOKEN (16자 이상)
# Served embedding/rerank models use comma-separated id=path entries relative to the repository root.
import os
import re
from dataclasses import dataclass
from pathlib import Path

from lawbot_common.ports import parse_port
from lawbot_model_server.envfile import ROOT

HOST = '127.0.0.1'  # 고정값: 설정으로 바꾸지 않음 (질문이 맥 밖으로 나가지 않게)
TOKEN_MIN = 16
KINDS = {'embedding': 'LAWBOT_SERVE_EMBEDDING', 'rerank': 'LAWBOT_SERVE_RERANK'}
MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')


@dataclass(frozen=True)
class ModelEntry:
    id: str  # API의 model 값
    kind: str  # 'embedding' / 'rerank'
    path: Path  # 로컬 모델 폴더 (절대 경로)


@dataclass(frozen=True)
class Config:
    port: int
    token: str
    models: tuple  # ModelEntry


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


def load():
    """포트·토큰을 먼저 확인한다 (모델을 올리기 전에 실행 거부)"""
    p, t = port(), token()
    return Config(p, t, models())
