# tests/test_common_api.py  →  웹앱 ↔ 모델 서버 계약 (lawbot_common.api)
import pytest
from pydantic import ValidationError

from lawbot_common import api


def test_error_body_is_openai_shape():
    assert api.error_body('대기열', api.SERVER_BUSY) == {
        'error': {'message': '대기열', 'type': 'server_busy', 'param': None, 'code': None}
    }


def test_requests_reject_unknown_fields_and_types():
    with pytest.raises(ValidationError):
        api.EmbeddingRequest.model_validate({'model': 'e', 'input': ['a'], 'dimensions': 8})
    with pytest.raises(ValidationError):
        api.RerankRequest.model_validate({'model': 'r', 'query': 'q', 'documents': 'a'})  # 목록이어야 함


def test_responses_allow_extra_fields():
    # 받는 쪽(웹앱)은 서버가 필드를 더 보내도 받는다 (OpenAI 응답과 같이)
    m = api.ModelsResponse.model_validate({'data': [{'id': 'x', 'type': 'chat', 'loaded': True, 'new': 1}]})
    assert m.data[0].loaded and m.data[0].model_dump()['new'] == 1
