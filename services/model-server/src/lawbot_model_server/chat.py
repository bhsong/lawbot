# lawbot_model_server/chat.py  →  MLX 채팅 모델: messages → chat template → 생성 (mlx_lm)
# 챗봇의 이전 llm.LLM.stream과 같은 계산 (템플릿 인자·생각 블록 처리·탐욕적 생성): 답변이 글자 단위로 같아야 함
# 생성 엔진 BatchEngine: mlx_lm BatchGenerator로 여러 요청을 한 배치에서 생성 (continuous batching)
#   scheduler.py가 GPU 스레드에서 add·step·remove를 부른다. 엔진 하나 = 모델 하나
#   요청 1건이면 이전 stream_generate와 같은 답 (2026-11-01 val 200문항 확인, docs/evals/2026-11-01-batch-generation.md)
import copy
import time
from dataclasses import dataclass
from pathlib import Path

import mlx.core as mx
from mlx_lm import load
from mlx_lm.generate import BatchGenerator
from mlx_lm.sample_utils import make_sampler

THINK = ('<think>', '</think>')  # Qwen3 계열 생각 블록 표시


def think_markers(tok):
    """토크나이저에 Qwen식 생각 토큰이 있으면 (시작, 끝), 없으면 None (mlx_lm TokenizerWrapper가 어휘에서 찾은 값)"""
    pair = (getattr(tok, 'think_start', None), getattr(tok, 'think_end', None))
    return pair if pair == THINK else None


def close_thinking(prompt, markers):
    """enable_thinking=False를 무시하는 chat template 대비: 프롬프트 끝에 빈 생각 블록이 없으면 직접 붙인다
    (Qwen3 템플릿이 생각을 끌 때 넣는 '<think>\\n\\n</think>\\n\\n'과 같은 문자열). 이미 닫혀 있으면 그대로"""
    if markers is None:
        return prompt
    start, end = markers
    tail = prompt.rstrip()
    if tail.endswith(end):
        return prompt
    if tail.endswith(start):  # 템플릿이 생각을 강제로 연 경우: 비운 채 닫는다
        return tail + f'\n\n{end}\n\n'
    return prompt + f'{start}\n\n{end}\n\n'


def _with_text(r, text):
    r = copy.copy(r)
    r.text = text
    return r


class ThinkFilter:
    """모델이 생각 블록으로 시작하면 끝 표시까지 text를 비운다. 요청마다 하나 (토큰마다 응답은 그대로: 측정값 유지)
    생각 블록이 없으면 이어 붙인 출력은 원문과 글자 단위로 같다"""

    def __init__(self, markers):
        self.start, self.end = markers
        self.buf, self.state = '', 'head'  # head: 시작 표시인지 판단 전, think: 생각 중, body: 본문

    def feed(self, r):
        if self.state == 'body':
            return r
        self.buf += r.text
        if self.state == 'head':
            head = self.buf.lstrip()
            if not head or (self.start.startswith(head) and head != self.start):  # 공백 또는 시작 표시를 쓰는 중
                return _with_text(r, '')
            if not head.startswith(self.start):  # 생각 블록 없음: 모아 둔 글을 그대로 내보냄
                self.state = 'body'
                return _with_text(r, self.buf)
            self.state = 'think'
        i = self.buf.find(self.end)
        if i < 0:
            return _with_text(r, '')
        self.state = 'body'
        return _with_text(r, self.buf[i + len(self.end) :].lstrip())


class ChatModel:
    def __init__(self, path):
        path = Path(path)
        if not path.is_dir():  # mlx_lm.load는 없는 경로를 Hugging Face 저장소 이름으로 보고 내려받으려 함
            raise FileNotFoundError(f'모델 폴더 없음: {path}')
        self.path = path
        self.model, self.tok = load(str(path))[:2]  # (모델, 토크나이저). return_config를 쓰지 않음
        self.think = think_markers(self.tok)

    def prompt(self, messages, enable_thinking=False):
        text = self.tok.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, enable_thinking=enable_thinking
        )
        return text if enable_thinking else close_thinking(text, self.think)

    def engine(self, max_concurrent):
        """생성 엔진. 상한 1이면 한 번에 하나만 배치에 넣는다"""
        return BatchEngine(self, max_concurrent)


@dataclass
class Chunk:
    """배치 생성의 토큰 하나 (mlx_lm GenerationResponse에서 api.py가 쓰는 속성만)"""

    text: str
    prompt_tokens: int
    generation_tokens: int
    prompt_tps: float
    generation_tps: float
    peak_memory: float
    finish_reason: str | None


@dataclass
class _Request:
    ticket: object
    detok: object
    think: ThinkFilter | None
    prompt_tokens: int
    t0: float
    t_first: float | None = None
    prompt_tps: float = 0.0
    n: int = 0


class BatchEngine:
    """mlx_lm BatchGenerator (continuous batching). 요청마다 detokenizer·생각 숨김·측정 시각을 따로 둔다
    끝내는 규칙은 mlx_lm stream_generate와 같다: eos는 stop(그 토큰 글자 없음), max_tokens는 length(마지막 토큰 포함)
    스텝 오류는 배치 전체가 실패하고 다음 요청 때 생성기를 새로 만든다"""

    def __init__(self, chat, max_concurrent, make_generator=None):
        self.chat, self.max_concurrent = chat, max_concurrent
        self.make_generator = make_generator or self._new_generator
        self.gen, self.items, self.samplers = None, {}, {}

    def _new_generator(self):
        return BatchGenerator(
            self.chat.model,
            completion_batch_size=self.max_concurrent,
            prefill_batch_size=self.max_concurrent,
            stop_tokens=[[t] for t in sorted(self.chat.tok.eos_token_ids)],
        )

    def _generator(self):
        if self.gen is None:
            self.gen = self.make_generator()
        return self.gen

    def _sampler(self, temperature, top_p):
        """같은 설정은 같은 객체: BatchGenerator가 배치 전체를 한 번에 샘플링한다"""
        key = (temperature, top_p if top_p is not None and top_p < 1.0 else 0.0)
        if key not in self.samplers:
            self.samplers[key] = make_sampler(temp=key[0], top_p=key[1])
        return self.samplers[key]

    def __len__(self):
        return len(self.items)

    def tickets(self):
        return [r.ticket for r in self.items.values()]

    def add(self, ticket, messages, kw):
        tok = self.chat.tok
        enable_thinking = kw.get('enable_thinking', False)
        prompt = self.chat.prompt(messages, enable_thinking)
        add_special = tok.bos_token is None or not prompt.startswith(tok.bos_token)  # stream_generate와 같은 판단
        tokens = tok.encode(prompt, add_special_tokens=add_special)
        sampler = self._sampler(kw.get('temperature', 0.0), kw.get('top_p'))
        (uid,) = self._generator().insert([tokens], max_tokens=[kw['max_tokens']], samplers=[sampler])
        think = None if enable_thinking or self.chat.think is None else ThinkFilter(self.chat.think)
        self.items[uid] = _Request(ticket, tok.detokenizer, think, len(tokens), time.perf_counter())

    def step(self):
        if not self.items:
            return []
        assert self.gen is not None  # items가 있으면 _generator()로 만든 뒤
        try:
            _, responses = self.gen.next()
        except Exception as e:
            events = [(r.ticket, 'error', e) for r in self.items.values()]
            self.items.clear()
            self._drop_generator()
            return events
        events, now = [], time.perf_counter()
        for resp in responses:
            r = self.items.get(resp.uid)
            if r is None:
                continue
            if r.t_first is None:
                r.t_first = now
                r.prompt_tps = r.prompt_tokens / max(now - r.t0, 1e-9)
            r.n += 1
            if resp.finish_reason != 'stop':  # eos 토큰은 글자로 내지 않음
                r.detok.add_token(resp.token)
            if resp.finish_reason is not None:
                r.detok.finalize()
            chunk = Chunk(
                text=r.detok.last_segment,
                prompt_tokens=r.prompt_tokens,
                generation_tokens=r.n,
                prompt_tps=r.prompt_tps,
                generation_tps=r.n / max(now - r.t_first, 1e-9),
                peak_memory=mx.get_peak_memory() / 1e9,
                finish_reason=resp.finish_reason,
            )
            events.append((r.ticket, 'item', r.think.feed(chunk) if r.think else chunk))
            if resp.finish_reason is not None:
                del self.items[resp.uid]
                events.append((r.ticket, 'end', None))
        return events

    def remove(self, ticket):
        for uid, r in list(self.items.items()):
            if r.ticket == ticket:
                del self.items[uid]
                assert self.gen is not None
                self.gen.remove([uid])

    def _drop_generator(self):
        if self.gen is not None:
            try:
                self.gen.close()
            finally:
                self.gen = None

    def close(self):
        self.items.clear()
        self._drop_generator()
