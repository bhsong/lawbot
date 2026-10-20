# src/lawbot/app.py  →  uv run lawbot-app
import gradio as gr
from lawbot.service import answer_stream, load_models, source_notes, trace_citations
from lawbot.security import get_local_launch_config

def fmt_provenance(snapshot):
    """근거 패널 첫 줄: 조문 데이터의 출처. 기록되지 않은 값은 미기록으로 표시"""
    if not snapshot.verified:
        return f'출처: `{snapshot.data_file}` · 출처·기준일 확인 불가'
    return (f"출처: {snapshot.law_name or '법령명 미기록'} 조문 데이터 `{snapshot.data_file}` · "
            f"기준일 {snapshot.as_of or '미기록'} · {snapshot.source or '원 출처 미기록'}")

def fmt_sources(hits, snapshot=None, notes=()):
    body = '\n\n---\n\n'.join(
        f"**{h['article_id']} {h['title']}** · 유사도 {h['score']:.3f}\n\n{h['doc'].split(chr(10), 1)[1]}"
        for h in hits)
    head = [fmt_provenance(snapshot)] if snapshot else []
    if notes:                        # 시점·최신성을 확인할 수 없는 경우에만 (lawdata.time_notes)
        head.append('\n'.join(f'> - {n}' for n in notes))
    return '\n\n'.join(head) + '\n\n---\n\n' + body if head else body

def fmt_trace(cited, outside):
    """답변이 인용한 조문과 검색 결과의 대조. 인용이 없으면(거절 등) 빈 문자열"""
    lines = []
    if cited:
        lines.append(f"답변이 인용한 조문: {', '.join(cited)} (아래 검색 결과)")
    if outside:
        lines.append(f"⚠ 검색 결과에 없는 조문 인용: {', '.join(outside)}. 이 데이터로 근거를 확인하지 않은 인용입니다.")
    return '\n\n'.join(lines) + '\n\n---\n\n' if lines else ''

def make_chat(retriever, llm):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    def chat(message, history):      # 단일 턴: history는 쓰지 않음
        hits, chunks = answer_stream(retriever, llm, message)
        notes = source_notes(retriever, message, hits)
        src, text = fmt_sources(hits, retriever.snapshot, notes), ''
        for chunk in chunks:
            text += chunk
            yield text, src          # (답변, 근거 조문 칸)
        if trace := fmt_trace(*trace_citations(text, hits)):
            yield text, trace + src  # 생성이 끝난 뒤 인용 조문을 검색 결과와 대조해 맨 위에 표시
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
    demo = build_demo(*load_models())
    demo.launch(**config)            # 127.0.0.1:7860, 로그인 필수, 공유 링크 없음

if __name__ == '__main__':
    main()
