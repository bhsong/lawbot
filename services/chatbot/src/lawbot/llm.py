# lawbot/llm.py  →  답변 생성: [참고 조문] 프롬프트를 만들어 OpenAI 호환 chat completions로 스트리밍 (openai SDK)
# Generation uses a dedicated OpenAI-compatible endpoint, local or explicitly allowed external HTTPS.
# (text·generation_tps·prompt_tokens·prompt_tps·generation_tokens·peak_memory·finish_reason: service.ask가 읽음)
import json
import re
import types
from functools import cached_property

import httpx
import openai

from lawbot.api_client import (
    ModelLoadError,
    ModelServerError,
    ModelServerTimeout,
    RouterTimeout,
    error_from_body,
    request_headers,
)
from lawbot.consult_trace import call_purpose, capture_call
from lawbot.hits import Clarification, ClarifyAttempt, Hit
from lawbot.inference import failure_fields, observe_inference
from lawbot.paths import DATA

CLARIFY_SYSTEM_FILE = DATA / 'v5' / 'system_clarify.txt'
CLARIFY_MAX_TOKENS = 256


def _clean(s):
    """조문 본문의 개정·신설 표시(' <개정 2020. 5. 26.>' 등)를 뺀다"""
    return re.sub(r' <(?:개정|신설)[^<>]*>', '', s)


def build_prompt(hits: list[Hit], question: str) -> str:
    """[참고 조문] 프롬프트: 검색 결과의 법령명·조문 번호·제목·본문 (7개 법률은 조문 번호가 겹쳐 법령명을 붙임)
    미확인: v4 학습 노트북의 [참고 조문] 형식과 글자 단위로 대조해야 함"""
    blocks = [
        f'[{i}] {h["law"]} {h["article_id"]}({h["title"]})\n{_clean(h["doc"].split(chr(10), 1)[1])}'
        for i, h in enumerate(hits, 1)
    ]
    return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'


GEN_FIELDS = (
    'text',
    'reasoning',
    'generation_tps',
    'prompt_tokens',
    'prompt_tps',
    'generation_tokens',
    'peak_memory',
    'finish_reason',
)
TEMPERATURE = 0  # Greedy generation for evaluation and the web app.


def response(text='', **kw):
    return types.SimpleNamespace(**(dict.fromkeys(GEN_FIELDS) | {'text': text} | kw))


def convert_error(e):
    """openai SDK 예외 → 웹앱이 안내하는 예외 (시간 초과는 ModelServerTimeout, 연결 실패는 ConnectionError,
    모델 불러오기 실패는 ModelLoadError)"""
    if isinstance(e, openai.APITimeoutError):
        if isinstance(e.__cause__, httpx.ConnectTimeout):
            return ConnectionError(f'생성 API에 연결할 수 없습니다 ({type(e.__cause__).__name__})')
        return ModelServerTimeout(f'생성 API가 응답하지 않았습니다 ({type(e).__name__})')
    if isinstance(e, openai.APIConnectionError):
        return ConnectionError(f'생성 API에 연결할 수 없습니다 ({type(e).__name__})')
    if isinstance(e, openai.APIStatusError):
        if e.status_code == 401:
            return ModelServerError('생성 API의 LAWBOT_LLM_API_KEY가 맞지 않습니다.', 401)
        body = e.body if isinstance(e.body, dict) else {}
        error = body.get('error', body)
        if error.get('type') == 'model_load_error':
            return ModelLoadError(f'생성 API 오류: {error.get("message", "model_load_error")}')
        return error_from_body(e.status_code, body if 'error' in body else {'error': body})
    if isinstance(e, openai.APIError):  # 스트림 중 error 이벤트
        return RuntimeError(f'생성 오류: {e.message}')
    return e


def responses(chunks):
    """Yield token responses immediately, then final metrics; require an explicit finish reason."""
    stats = {}
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
        if choice.delta is not None:
            text = choice.delta.content
            reasoning = getattr(choice.delta, 'reasoning_content', None) or getattr(choice.delta, 'reasoning', None)
            if text is not None or reasoning is not None:
                yield response(text or '', reasoning=reasoning)
    if not stats.get('finish_reason'):
        raise RuntimeError('생성 스트림이 finish_reason 없이 종료되었습니다.')
    yield response('', **stats)


def _unique_keys(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError('Duplicate JSON key')
    return obj


def _parse_clarification(text: str | None) -> Clarification | None:
    try:
        value = json.loads(text or '', object_pairs_hook=_unique_keys)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.keys() != {'ask', 'missing', 'question'}:
        return None
    ask, missing, question = value['ask'], value['missing'], value['question']
    if type(ask) is not bool or not isinstance(question, str) or not isinstance(missing, list):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in missing):
        return None
    if (ask and (not missing or not question.strip())) or (not ask and (missing or question)):
        return None
    return Clarification(ask=ask, missing=missing, question=question.strip())


class LLM:
    """model: API 모델 id, client: openai.OpenAI, system: 이 모델의 기본 system (deploy 프로필·모델의 system 파일)"""

    def __init__(self, model, client, system):
        self.model, self.client, self.system = model, client, system

    def build_messages(self, question, hits, system=None):
        return [
            {'role': 'system', 'content': system or self.system},
            {'role': 'user', 'content': build_prompt(hits, question)},
        ]

    @cached_property
    def clarify_system(self):
        return CLARIFY_SYSTEM_FILE.read_text(encoding='utf-8').strip()

    def clarify(self, question: str, hits: list[Hit]):
        """Decide once, allowing one format correction; return diagnostics separately from log metrics."""
        attempts: list[ClarifyAttempt] = []
        metrics = {'clarify_prompt_tokens': 0, 'clarify_completion_tokens': 0}
        user = build_prompt(hits, question)
        decision = None
        failure = None
        for retry in (False, True):
            content = user + '\n\n형식 오류: 지정된 JSON 객체 하나만 출력하세요.' if retry else user
            messages = [{'role': 'system', 'content': self.clarify_system}, {'role': 'user', 'content': content}]
            attempt = ClarifyAttempt(messages=messages, raw=None, parsed=None, retry=retry, finish=None, error=None)
            attempts.append(attempt)
            try:
                with capture_call(openai.OpenAIError, ValueError, httpx.DecodingError) as capture:
                    result = self._plain_completion(
                        messages, CLARIFY_MAX_TOKENS, 'clarify_retry' if retry else 'clarify', 'clarify'
                    )
            except (openai.OpenAIError, httpx.TransportError) as exc:
                attempt['error'] = type(exc).__name__
                fields = failure_fields('clarify', exc)
                metrics['clarify_exception'] = fields['exception']
                metrics['clarify_status_code'] = fields['status_code']
                failure = 'call_error'
                break
            if result.usage is not None:
                metrics['clarify_prompt_tokens'] += result.usage.prompt_tokens
                metrics['clarify_completion_tokens'] += result.usage.completion_tokens
            if result.choices:
                choice = result.choices[0]
                attempt.update(raw=choice.message.content, finish=choice.finish_reason)
            if attempt['finish'] != 'stop':
                if attempt['finish'] is None:
                    capture.invalid('missing_finish_reason')
                finish = attempt['finish']
                failure = (
                    finish
                    if finish in ('length', 'content_filter', 'tool_calls', 'function_call')
                    else ('missing_finish' if finish is None else 'unexpected_finish')
                )
                break
            decision = _parse_clarification(attempt['raw'])
            attempt['parsed'] = decision
            failure = None if decision is not None else 'invalid_json'
            if decision is None:
                capture.invalid('invalid_clarification')
            if decision is not None:
                break
        status = 'failed_open' if decision is None else ('asked' if decision['ask'] else 'not_asked')
        return (
            decision,
            attempts,
            {
                **metrics,
                'clarify_status': status,
                'clarify_failure': failure,
                'clarify_calls': len(attempts),
                'clarify_retries': len(attempts) - 1,
            },
        )

    def route_calculation(self, question: str, *, metrics: dict[str, int] | None = None) -> str | None:
        """Select a calculator, then label the question's numbers for it.

        Returns the router JSON composed from both stages, or None when stage 1 is
        unusable or a stage hits its token limit. Each stage is one non-streaming
        request with the configured read timeout and no retry.
        """
        from lawbot.calc_extract import (
            SELECT_MAX_TOKENS,
            SELECT_SYSTEM,
            compose_route,
            label_max_tokens,
            label_system,
            marked_question,
            parse_selection,
        )

        with capture_call() as capture:
            raw = self._router_completion(SELECT_SYSTEM, question, SELECT_MAX_TOKENS, metrics, 'router_select')
            selection = parse_selection(raw)
            if raw is not None and selection is None:
                capture.invalid('invalid_router_selection')
        if selection is None or selection in ('lookup', 'unsupported'):
            return compose_route(question, selection)
        labels = self._router_completion(
            label_system(selection), marked_question(question), label_max_tokens(question), metrics, 'router_label'
        )
        return None if labels is None else compose_route(question, selection, labels)

    def _plain_completion(self, messages, max_tokens: int, stage: str, purpose: str):
        with call_purpose(purpose), observe_inference(stage):
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=TEMPERATURE,
                stream=False,
                extra_body={'chat_template_kwargs': {'enable_thinking': False}},
                **({'extra_headers': h} if (h := request_headers()) else {}),
            )

    def _router_completion(
        self, system: str, user: str, max_tokens: int, metrics: dict[str, int] | None, purpose: str
    ) -> str | None:
        if metrics is not None:
            metrics['router_calls'] = metrics.get('router_calls', 0) + 1
        try:
            with capture_call(openai.OpenAIError, ValueError, httpx.DecodingError) as capture:
                result = self._plain_completion(
                    [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    max_tokens,
                    'router',
                    purpose,
                )
        except openai.APITimeoutError as exc:
            raise RouterTimeout(exc.__cause__) from exc
        except openai.OpenAIError as exc:
            converted = convert_error(exc)
            if type(converted) is RuntimeError:
                converted = ModelServerError('계산 라우터 응답 오류')
            raise converted from exc
        if metrics is not None:
            if (usage := getattr(result, 'usage', None)) is not None:
                for key, value in (
                    ('router_prompt_tokens', usage.prompt_tokens),
                    ('router_completion_tokens', usage.completion_tokens),
                ):
                    metrics[key] = metrics.get(key, 0) + value
        if not result.choices:
            capture.invalid('empty_choices')
            return None
        if result.choices[0].finish_reason == 'length':
            return None
        if not result.choices[0].finish_reason:
            capture.invalid('missing_finish_reason')
            raise ModelServerError('계산 라우터 응답에 finish_reason이 없습니다.')
        return result.choices[0].message.content

    def stream(self, question, hits, max_tokens=1024, system=None, *, suffix='', capture_messages=None):
        """system: 이번 요청만 다른 system으로 (None이면 모델의 기본 system). 생성기를 닫으면 연결을 끊는다 (서버가 생성을 멈춤)"""
        messages = self.build_messages(question, hits, system)
        if suffix:
            messages[-1]['content'] += suffix
        if capture_messages is not None:
            capture_messages.extend(messages)
        with observe_inference('writer'):
            yield from self._stream(messages, max_tokens)

    def _stream(self, messages, max_tokens):
        try:
            with capture_call(openai.OpenAIError, ValueError, httpx.DecodingError) as capture:
                s = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=TEMPERATURE,
                    stream=True,
                    stream_options={'include_usage': True},
                    **({'extra_headers': h} if (h := request_headers()) else {}),
                )
        except openai.OpenAIError as e:
            raise convert_error(e) from e
        try:
            yield from responses(s)
        except openai.OpenAIError as e:
            capture.invalid(type(e).__name__)
            raise convert_error(e) from e
        except httpx.ConnectTimeout as e:
            raise ConnectionError(f'생성 API에 연결할 수 없습니다 ({type(e).__name__})') from e
        except httpx.TimeoutException as e:  # 스트림 중 응답 조각 사이가 상한을 넘음 (SDK가 감싸지 않음)
            raise ModelServerTimeout(f'생성 API가 응답 중에 멈췄습니다 ({type(e).__name__})') from e
        except httpx.TransportError as e:
            raise ConnectionError(f'생성 API와 연결이 끊겼습니다 ({type(e).__name__})') from e
        except (RuntimeError, ValueError, httpx.DecodingError) as e:
            capture.invalid(type(e).__name__)
            raise
        finally:
            s.close()


__all__ = ['LLM', 'ModelServerError', 'build_prompt']
