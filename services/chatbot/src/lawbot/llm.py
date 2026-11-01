# lawbot/llm.py  →  답변 생성: [참고 조문] 프롬프트를 만들어 OpenAI 호환 chat completions로 스트리밍 (openai SDK)
# 기본은 모델 서버(/v1), LAWBOT_LLM_BASE_URL로 클라우드 모델도 쓸 수 있다. 응답은 이전 MLX 생성과 같은 속성으로 바꿔 낸다
# (text·generation_tps·prompt_tokens·prompt_tps·generation_tokens·peak_memory·finish_reason: service.ask가 읽음)
import re
import types

import openai
import pandas as pd

from lawbot.api_client import ModelServerError, error_from_body
from lawbot.paths import ARTICLES
from lawbot.prompts import SYSTEM  # v1 학습 system (여기서 다시 export)

_law = pd.read_csv(ARTICLES, encoding='utf-8-sig')
_clean = lambda s: re.sub(r' <(?:개정|신설)[^<>]*>', '', s)
ART = {a: (t, _clean(x)) for a, t, x in zip(_law['article_id'], _law['title'], _law['text']) if pd.notna(t)}


def build_prompt(ids, question):
    blocks = [f'[{i}] {a}({ART[a][0]})\n{ART[a][1]}' for i, a in enumerate(ids, 1)]
    return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'


def build_prompt_v4(hits, question):
    """v4: 검색 결과의 법령명·조문을 그대로 쓴다 (7개 법률은 조문 번호가 겹쳐 ART로 찾을 수 없음)
    미확인: v4 학습 노트북의 [참고 조문] 형식과 글자 단위로 대조해야 함"""
    blocks = [
        f'[{i}] {h["law"]} {h["article_id"]}({h["title"]})\n{_clean(h["doc"].split(chr(10), 1)[1])}'
        for i, h in enumerate(hits, 1)
    ]
    return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'


GEN_FIELDS = (
    'text',
    'generation_tps',
    'prompt_tokens',
    'prompt_tps',
    'generation_tokens',
    'peak_memory',
    'finish_reason',
)
TEMPERATURE = 0  # 탐욕적 생성 (평가·웹앱 공통, 모델 서버 기본값과 같음)


def response(text='', **kw):
    return types.SimpleNamespace(**(dict.fromkeys(GEN_FIELDS) | {'text': text} | kw))


def convert_error(e):
    """openai SDK 예외 → 웹앱이 안내하는 예외 (연결 실패는 ConnectionError, 모델 불러오기 실패는 ModelLoadError)"""
    if isinstance(e, openai.APIConnectionError):
        return ConnectionError(f'생성 API에 연결할 수 없습니다 ({type(e).__name__})')
    if isinstance(e, openai.APIStatusError):
        body = e.body if isinstance(e.body, dict) else {}
        return error_from_body(e.status_code, body if 'error' in body else {'error': body})
    if isinstance(e, openai.APIError):  # 스트림 중 error 이벤트
        return RuntimeError(f'생성 오류: {e.message}')
    return e


def responses(chunks):
    """SSE 청크 → 토큰마다 응답 (받는 즉시: 첫 토큰 시간이 그대로). 끝 청크의 측정값(finish_reason·timings·usage)은
    마지막에 text가 빈 응답 하나로 붙인다 (service.ask는 마지막 응답의 측정값을 읽음)"""
    stats, n = {}, 0
    for ch in chunks:
        extra = ch.model_extra or {}
        if ch.usage is not None:
            stats |= {'prompt_tokens': ch.usage.prompt_tokens, 'generation_tokens': ch.usage.completion_tokens}
        if extra.get('timings'):
            t = extra['timings']
            stats |= {
                'prompt_tps': t.get('prompt_tps'),
                'generation_tps': t.get('generation_tps'),
                'peak_memory': t.get('peak_memory_gb'),
            }
        if not ch.choices:
            continue
        choice = ch.choices[0]
        if choice.finish_reason:
            stats['finish_reason'] = choice.finish_reason
        if choice.delta is not None and choice.delta.content is not None:
            n += 1
            yield response(choice.delta.content)
    if stats or not n:
        yield response('', **stats)


class LLM:
    """model: API 모델 id, client: openai.OpenAI. stream()은 이전 MLX LLM.stream과 같은 인자·응답 속성"""

    def __init__(self, model, client, system=SYSTEM, prompt_style='v1'):
        self.model, self.client = model, client
        self.system, self.prompt_style = system, prompt_style  # v4: deploy 프로필의 system 파일

    def build_messages(self, question, hits, system=None):
        user = (
            build_prompt_v4(hits, question)
            if self.prompt_style == 'v4'
            else build_prompt([h['article_id'] for h in hits], question)
        )
        return [{'role': 'system', 'content': system or self.system}, {'role': 'user', 'content': user}]

    def stream(self, question, hits, max_tokens=1024, system=None):
        """system: 이번 요청만 다른 system으로 (None이면 모델의 기본 system). 생성기를 닫으면 연결을 끊는다 (서버가 생성을 멈춤)"""
        try:
            s = self.client.chat.completions.create(
                model=self.model,
                messages=self.build_messages(question, hits, system),
                max_tokens=max_tokens,
                temperature=TEMPERATURE,
                stream=True,
                stream_options={'include_usage': True},
            )
        except openai.OpenAIError as e:
            raise convert_error(e) from e
        try:
            yield from responses(s)
        except openai.OpenAIError as e:
            raise convert_error(e) from e
        finally:
            s.close()


__all__ = ['LLM', 'SYSTEM', 'ModelServerError', 'build_prompt', 'build_prompt_v4']
