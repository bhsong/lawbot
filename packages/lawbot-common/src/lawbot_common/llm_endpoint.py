"""Validate the dedicated OpenAI-compatible generation endpoint."""

import os
from urllib.parse import urlsplit


def endpoint(env=None):
    """Return the required generation URL, dedicated key, and external flag."""
    env = os.environ if env is None else env
    value = (env.get('LAWBOT_LLM_BASE_URL') or '').strip().rstrip('/')
    if not value:
        raise RuntimeError('LAWBOT_LLM_BASE_URL이 없거나 비어 있어 실행을 거부합니다')
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError('LAWBOT_LLM_BASE_URL 형식 오류') from None
    if host is None or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('LAWBOT_LLM_BASE_URL 형식 오류')
    external = host != '127.0.0.1'
    if not external:
        if parsed.scheme != 'http' or port is None:
            raise ValueError('LAWBOT_LLM_BASE_URL이 이 맥이면 http://127.0.0.1:<포트>/v1 형식')
    else:
        if (env.get('LAWBOT_LLM_ALLOW_EXTERNAL') or '').strip() != '1':
            raise ValueError('외부 LAWBOT_LLM_BASE_URL에는 LAWBOT_LLM_ALLOW_EXTERNAL=1이 필요합니다')
        if parsed.scheme != 'https':
            raise ValueError('외부 LAWBOT_LLM_BASE_URL은 https만 허용합니다')
    key = (env.get('LAWBOT_LLM_API_KEY') or '').strip()
    if not key:
        raise RuntimeError('LAWBOT_LLM_API_KEY가 없거나 비어 있어 실행을 거부합니다')
    if len(key) < 16:
        raise RuntimeError('LAWBOT_LLM_API_KEY가 너무 짧아 실행을 거부합니다 (16자 이상)')
    return value, key, external


def generation_model(models, model_id):
    """Select a generation model; reject missing or invalid concurrency metadata."""
    model = next((row for row in models if row['id'] == model_id), None)
    if model is None:
        raise ValueError(f'생성 모델 없음: {model_id}')
    concurrent = model.get('max_concurrent')
    if type(concurrent) is not int or concurrent < 1:
        raise ValueError(f'생성 모델 max_concurrent는 양의 정수여야 합니다: {model_id}')
    return model
