# src/lawbot/llm.py
import copy
import re
import pandas as pd
from mlx_lm import load, stream_generate
from mlx_lm.sample_utils import make_sampler
from lawbot.paths import ARTICLES, LLM_4BIT

# labor_sft_train.csv의 system 열과 동일 (전 문항 같은 값)
SYSTEM = ("당신은 대한민국 근로기준법 상담 도우미입니다. 사용자 메시지의 [참고 조문]만 근거로 답변하세요.\n"
          "답변은 반드시 다음 네 부분으로 작성합니다.\n"
          "【결론】 질문에 대한 핵심 답을 1~2문장으로\n"
          "【근거 조문】 '근로기준법 제N조(제목)' 형식으로 인용 (필요하면 항 번호 포함)\n"
          "【설명】 조문 내용을 근거로 한 설명을 '- '로 시작하는 항목으로\n"
          "【참고】 벌칙, 예외, 상담처 등 추가 안내\n"
          "제공된 조문에서 근거를 찾을 수 없으면 추측하지 말고 확인할 수 없다고 답하세요.")

_law = pd.read_csv(ARTICLES, encoding='utf-8-sig')
_clean = lambda s: re.sub(r' <(?:개정|신설)[^<>]*>', '', s)
ART = {a: (t, _clean(x)) for a, t, x in zip(_law['article_id'], _law['title'], _law['text'])
       if pd.notna(t)}

def build_prompt(ids, question):
    blocks = [f'[{i}] {a}({ART[a][0]})\n{ART[a][1]}' for i, a in enumerate(ids, 1)]
    return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'

def build_prompt_v4(hits, question):
    """v4: 검색 결과의 법령명·조문을 그대로 쓴다 (7개 법률은 조문 번호가 겹쳐 ART로 찾을 수 없음)
    미확인: v4 학습 노트북의 [참고 조문] 형식과 글자 단위로 대조해야 함"""
    blocks = [f"[{i}] {h['law']} {h['article_id']}({h['title']})\n{_clean(h['doc'].split(chr(10), 1)[1])}"
              for i, h in enumerate(hits, 1)]
    return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'

THINK = ('<think>', '</think>')     # Qwen3 계열 생각 블록 표시

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
    if tail.endswith(start):                 # 템플릿이 생각을 강제로 연 경우: 비운 채 닫는다
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
    buf, state = '', 'head'                  # head: 시작 표시인지 판단 전, think: 생각 중, body: 본문
    for r in responses:
        if state == 'body':
            yield r
            continue
        buf += r.text
        if state == 'head':
            head = buf.lstrip()
            if not head or (start.startswith(head) and head != start):   # 공백 또는 시작 표시를 쓰는 중
                yield _with_text(r, '')
                continue
            if not head.startswith(start):   # 생각 블록 없음: 모아 둔 글을 그대로 내보냄
                state = 'body'
                yield _with_text(r, buf)
                continue
            state = 'think'
        i = buf.find(end)
        if i < 0:
            yield _with_text(r, '')
            continue
        state = 'body'
        yield _with_text(r, buf[i + len(end):].lstrip())

class LLM:
    def __init__(self, path=LLM_4BIT, temp=0.0, system=SYSTEM, prompt_style='v1'):
        self.model, self.tok = load(str(path))
        self.sampler = make_sampler(temp=temp)
        self.system, self.prompt_style = system, prompt_style    # v4: deploy 프로필의 system 파일
        self.think = think_markers(self.tok)

    def build_messages(self, question, hits):
        user = (build_prompt_v4(hits, question) if self.prompt_style == 'v4'
                else build_prompt([h['article_id'] for h in hits], question))
        return [{'role': 'system', 'content': self.system}, {'role': 'user', 'content': user}]

    def stream(self, question, hits, max_tokens=1024):
        prompt = self.tok.apply_chat_template(
            self.build_messages(question, hits), add_generation_prompt=True,
            tokenize=False, enable_thinking=False)       # 학습 때와 같이 생각 단계 끄기
        prompt = close_thinking(prompt, self.think)        # 템플릿이 끄기를 지원하지 않는 모델(튜닝 전 27B 등) 대비
        yield from hide_thinking(stream_generate(self.model, self.tok, prompt,
                                                 max_tokens=max_tokens, sampler=self.sampler), self.think)
