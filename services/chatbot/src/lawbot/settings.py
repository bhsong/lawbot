# lawbot/settings.py  →  웹앱 실행 설정: 시작할 때 한 번 읽어 모두 검증하고, 틀린 값은 한꺼번에 알린다
# 각 값의 규칙(기본값·범위·오류 문구)은 원래 모듈(security·deploy·service·app·feedback)의 함수에 그대로 있고,
# 여기서는 그 함수들에 같은 환경 변수 묶음(env)을 넘겨 모은다. 실행 경로(app.main·load_app·평가 명령)는 이 객체를 쓴다
import os
from dataclasses import dataclass
from pathlib import Path

from lawbot.deploy import DecreeConfig, Profile, RerankerConfig


class SettingsError(ValueError, RuntimeError):
    """설정 오류 모음. 값은 넣지 않는다 (비밀번호·토큰). 기존 호출부의 except ValueError·RuntimeError와 맞도록 둘 다"""


@dataclass(frozen=True)
class Settings:
    profile: Profile  # LAWBOT_MODELS로 고른 답변 모델 포함
    reranker: RerankerConfig | None
    decree: DecreeConfig | None
    model_server: str  # http://127.0.0.1:<포트>
    model_token: str
    llm_url: str  # 생성 API base_url (모델 서버 /v1 또는 외부)
    llm_key: str
    llm_external: bool
    server_wait: float  # 시작할 때 모델 서버 준비 대기 (초)
    read_timeout: float  # 질문 처리 중 응답 조각 사이 최대 대기 (초)
    request_log: Path | None  # 요청 로그 파일 (None이면 기록 안 함)
    # 웹 화면만 (web=True일 때만 읽음. 평가·점검 명령은 None)
    auth: tuple | None = None  # (사용자, 비밀번호)
    port: int | None = None
    instance: str | None = None
    chat_concurrency: int | None = None
    feedback: object = None  # feedback.Feedback 또는 None (LAWBOT_FEEDBACK=1이면 저장 폴더를 확인한 뒤)


MISSING_SERVER = (
    '모델 서버 주소가 없습니다: .env에 LAWBOT_MODEL_SERVER=http://127.0.0.1:<모델 서버 포트>와 '
    'LAWBOT_MODEL_TOKEN을 적고, uv run lawbot-model-server로 모델 서버를 띄우세요.'
)


def load(env=None, web=False):
    """env(기본 os.environ)에서 설정을 읽는다. web=True면 로그인·포트·동시 처리 수·피드백까지 (웹앱 시작)
    틀린 값이 하나라도 있으면 SettingsError에 모두 적어 올린다 (모델 서버에 접속하기 전에)"""
    from lawbot import deploy, feedback, security, service

    env = dict(os.environ if env is None else env)
    errors, got = [], {}

    def take(key, fn):
        try:
            got[key] = fn()
        except (ValueError, RuntimeError) as e:
            errors.append(str(e))
            got[key] = None
        return got[key]

    profile = take('profile', lambda: deploy.get_profile(env))
    if profile is not None:
        rr = take('reranker', lambda: deploy.reranker(profile, env))
        if rr is not None and rr.candidates < service.TOP_K:
            errors.append(f'LAWBOT_RERANKER_CANDIDATES는 {service.TOP_K} 이상: {rr.candidates}')
        take('decree', lambda: deploy.decree(profile, env))
    url = take('model_server', lambda: security.model_server_url(env))
    if url is None and not any('LAWBOT_MODEL_SERVER' in e for e in errors):
        errors.append(MISSING_SERVER)
    take('model_token', lambda: security.model_token(env=env))
    if url:
        llm = take('llm', lambda: security.llm_endpoint(url, env))
        got['llm_url'], got['llm_key'], got['llm_external'] = llm or (None, None, None)
    take('server_wait', lambda: service.server_wait(env))
    take('read_timeout', lambda: service.read_timeout(env))
    take('request_log', lambda: service.request_log_path(env))
    if web:
        from lawbot import app  # gradio를 import하므로 웹 화면일 때만

        take('auth', lambda: security.auth(env))
        take('port', lambda: security.server_port(env))
        take('instance', lambda: security.instance_name(env))
        take('chat_concurrency', lambda: app.chat_concurrency(env))
        take('feedback', lambda: feedback.from_env(env))
    if errors:
        raise SettingsError(f'설정 오류 {len(errors)}개:\n' + '\n'.join(f'- {e}' for e in errors))
    fields = Settings.__dataclass_fields__
    return Settings(**{k: v for k, v in got.items() if k in fields})
