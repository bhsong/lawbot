# src/lawbot/envfile.py  →  레포 최상위 .env를 환경 변수로 읽는다 (app·check_deploy·eval_v4 등의 main이 맨 먼저 호출)
# 규칙은 lawbot_common.envfile (모델 서버·monitor와 같음): LAWBOT_*만, 셸에 있는 변수 우선, LAWBOT_ENV_FILE로 파일 지정
import re
from pathlib import Path

from lawbot.paths import ROOT
from lawbot_common import envfile as common
from lawbot_common.logs import logger

DEFAULT = ROOT / '.env'
SECRETS = ('LAWBOT_PW', 'LAWBOT_MODEL_TOKEN', 'LAWBOT_ADMIN_PW_HASH', 'LAWBOT_CALENDAR_ADMIN_TOKEN')
MODEL_HOURS_LINE = re.compile(r'^\s*(?:export\s+)?LAWBOT_MODEL_HOURS(?=\s|=|$)')


def parse(text, name='.env'):
    return common.parse(text, name, common.lawbot_keys)


def source_path() -> Path | None:
    return common.source_path(DEFAULT, ROOT)


def read_model_hours(path: Path) -> str | None:
    """Parse only model-hours assignments; never interpret other .env settings."""
    try:
        with path.open(encoding='utf-8') as file:
            lines = ''.join(line for line in file if MODEL_HOURS_LINE.match(line))
    except FileNotFoundError:
        return None
    return parse(lines, path.name).get('LAWBOT_MODEL_HOURS')


def load():
    return common.load(DEFAULT, ROOT, SECRETS, logger('lawbot', 'lawbot'))
