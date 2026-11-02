# src/lawbot/envfile.py  →  레포 최상위 .env를 환경 변수로 읽는다 (app·check_deploy·eval_v4 등의 main이 맨 먼저 호출)
# 규칙은 lawbot_common.envfile (모델 서버·monitor와 같음): LAWBOT_*만, 셸에 있는 변수 우선, LAWBOT_ENV_FILE로 파일 지정
from lawbot.paths import ROOT
from lawbot_common import envfile as common
from lawbot_common.logs import logger

DEFAULT = ROOT / '.env'
SECRETS = ('LAWBOT_PW', 'LAWBOT_MODEL_TOKEN')  # 파일 권한이 넓으면 경고


def parse(text, name='.env'):
    return common.parse(text, name, common.lawbot_keys)


def load():
    return common.load(DEFAULT, ROOT, SECRETS, logger('lawbot', 'lawbot'))
