# src/lawbot/app.py  →  uv run lawbot-app
import os
import gradio as gr
from lawbot.paths import LLM_4BIT
from lawbot.retriever import Retriever
from lawbot.llm import LLM
from lawbot.security import get_local_launch_config

def fmt_sources(hits):
    return '\n\n---\n\n'.join(
        f"**{h['article_id']} {h['title']}** · 유사도 {h['score']:.3f}\n\n{h['doc'].split(chr(10), 1)[1]}"
        for h in hits)

def make_chat(retriever, llm):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    def chat(message, history):      # 단일 턴: history는 쓰지 않음
        hits = retriever.search(message, k=3)
        src, text = fmt_sources(hits), ''
        for r in llm.stream(message, hits):
            text += r.text
            yield text, src          # (답변, 근거 조문 칸)
    return chat

def build_demo(retriever, llm):      # 화면만 만든다: 인증 검사·모델 로딩은 main에서
    with gr.Blocks(title='근로기준법 챗봇') as demo:
        gr.Markdown('# 근로기준법 챗봇\n질문과 관련된 조문을 찾아 그 조문을 근거로 답합니다. '
                    '법률 자문이 아닌 참고용 답변입니다.')
        sources = gr.Markdown(render=False)
        with gr.Row():
            with gr.Column(scale=3):
                gr.ChatInterface(
                    fn=make_chat(retriever, llm),
                    additional_outputs=[sources],
                    examples=['수습 기간에도 해고 예고를 해야 하나요?',
                              '주휴수당은 어떤 조건에서 받을 수 있나요?',
                              '회사가 연차를 못 쓰게 하면 어떻게 되나요?'])
            with gr.Column(scale=2):
                gr.Markdown('### 근거 조문')
                sources.render()
    return demo

def main():
    config = get_local_launch_config()   # 인증 정보가 없으면 모델 로딩 전에 실행 거부
    demo = build_demo(Retriever(), LLM(os.environ.get('LLM_PATH', LLM_4BIT)))
    demo.launch(**config)            # 127.0.0.1:7860, 로그인 필수, 공유 링크 없음

if __name__ == '__main__':
    main()
