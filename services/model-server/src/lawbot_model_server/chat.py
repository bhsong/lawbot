# lawbot_model_server/chat.py  →  MLX 채팅 모델: messages → chat template → stream_generate (mlx_lm)
# 챗봇의 이전 llm.LLM.stream과 같은 계산 (템플릿 인자·생각 블록 처리·탐욕적 생성): 답변이 글자 단위로 같아야 함
import copy
from pathlib import Path

from mlx_lm import load, stream_generate
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


def hide_thinking(responses, markers):
    """그래도 모델이 생각 블록으로 시작하면 끝 표시까지 text를 비워서 낸다 (토큰마다 응답은 그대로: 측정값 유지)
    생각 블록이 없으면 이어 붙인 출력은 원문과 글자 단위로 같다"""
    if markers is None:
        yield from responses
        return
    start, end = markers
    buf, state = '', 'head'  # head: 시작 표시인지 판단 전, think: 생각 중, body: 본문
    for r in responses:
        if state == 'body':
            yield r
            continue
        buf += r.text
        if state == 'head':
            head = buf.lstrip()
            if not head or (start.startswith(head) and head != start):  # 공백 또는 시작 표시를 쓰는 중
                yield _with_text(r, '')
                continue
            if not head.startswith(start):  # 생각 블록 없음: 모아 둔 글을 그대로 내보냄
                state = 'body'
                yield _with_text(r, buf)
                continue
            state = 'think'
        i = buf.find(end)
        if i < 0:
            yield _with_text(r, '')
            continue
        state = 'body'
        yield _with_text(r, buf[i + len(end) :].lstrip())


class ChatModel:
    def __init__(self, path):
        path = Path(path)
        if not path.is_dir():  # mlx_lm.load는 없는 경로를 Hugging Face 저장소 이름으로 보고 내려받으려 함
            raise FileNotFoundError(f'모델 폴더 없음: {path}')
        self.path = path
        self.model, self.tok = load(str(path))
        self.think = think_markers(self.tok)

    def prompt(self, messages, enable_thinking=False):
        text = self.tok.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, enable_thinking=enable_thinking
        )
        return text if enable_thinking else close_thinking(text, self.think)

    def stream(self, messages, max_tokens=1024, temperature=0.0, top_p=None, enable_thinking=False):
        """mlx_lm GenerationResponse(text·prompt_tokens·…·finish_reason)를 토큰마다. temperature 0이면 탐욕적 생성
        생각을 끄면(기본) 템플릿이 무시해도 빈 생각 블록을 붙이고, 그래도 나온 생각 블록은 text에서 숨긴다"""
        sampler = make_sampler(temp=temperature, top_p=top_p if top_p is not None and top_p < 1.0 else 0.0)
        gen = stream_generate(
            self.model, self.tok, self.prompt(messages, enable_thinking), max_tokens=max_tokens, sampler=sampler
        )
        try:
            yield from (gen if enable_thinking else hide_thinking(gen, self.think))
        finally:
            gen.close()  # 중간에 멈추면 생성도 멈춤
