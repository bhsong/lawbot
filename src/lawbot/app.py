# src/lawbot/app.py  →  uv run lawbot-app
import gradio as gr
from lawbot.answer_format import render
from lawbot.service import ask, load_app
from lawbot.security import get_local_launch_config

# v4 Fast로 검색·답변을 확인한 질문 (근로기준법 제26조, 최저임금법 제6조, 남녀고용평등법 제19조)
EXAMPLES = ['해고 예고 없이 갑자기 해고되면 어떻게 하나요?',
            '최저임금보다 적게 주기로 한 근로계약은 효력이 있나요?',
            '육아휴직을 썼다고 불이익을 줘도 되나요?']
EMPTY = '답변을 생성하지 못했습니다. 다시 질문해 주세요.'

def fmt_sources(hits):
    return '\n\n---\n\n'.join(
        f"**{h['law'] + ' ' if 'law' in h else ''}{h['article_id']} {h['title']}** · 유사도 {h['score']:.3f}"
        + (f" · 리랭커 {h['rerank_score']:.3f} (임베딩 {h['emb_rank']}위)" if 'rerank_score' in h else '')
        + f"\n\n{h['doc'].split(chr(10), 1)[1]}"
        for h in hits)

def fmt_stats(label, stage, st):
    """측정 칸: 서버 기준 시간. 브라우저 표시 지연은 lawbot.bench_ui로 따로 잰다"""
    if stage == 'load':
        return f'⏳ {label} 불러오는 중… (모델 전환 대기, 검색 {st["search"]:.2f}초)'
    parts = [label, f"검색 {st['search']:.2f}초"]
    if 'rerank' in st:
        parts.append(f"리랭킹 {st['rerank']:.2f}초")
    if st['load'] is not None:
        parts.append(f"모델 전환 {st['load']:.1f}초")
    if 'ttft' in st:
        parts.append(f"첫 토큰 {st['ttft']:.1f}초")
    if 'first_text' in st:
        parts.append(f"첫 글자 {st['first_text']:.1f}초")
    if stage == 'done':
        if st.get('tps'):
            parts.append(f"{st['tps']:.1f} tok/s")
        parts.append(f"완료 {st['total']:.1f}초")
        if 'early_stop_at' in st:
            parts.append('간결형 조기 종료')
        if st.get('finish') == 'length':
            parts.append('⚠ 최대 길이에서 잘림')
    return ' · '.join(parts)

def make_chat(app):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    # API 입력은 [질문, 모델 key, 형식] 세 개 (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)
    def chat(message, history, model_key, fmt):      # 단일 턴: history는 쓰지 않음
        if not (message or '').strip():
            yield '질문을 입력해 주세요.', '', ''
            return
        spec = app.models.specs.get(model_key)
        if spec is None or fmt not in ('concise', 'full'):
            raise gr.Error(f'알 수 없는 선택: {model_key!r}, {fmt!r}')
        early = fmt == 'concise' and app.early_stop
        try:
            for stage, hits, raw, st in ask(app, message, model_key, early_stop=early):
                src = fmt_sources(hits)          # 오른쪽 조문 3개는 생성 내내 같음
                if stage == 'load':
                    yield f'⏳ {spec.label} 불러오는 중…', src, fmt_stats(spec.label, stage, st)
                    continue
                text = render(raw, hits, fmt, done=stage == 'done')
                if stage == 'done' and not raw.strip():
                    text = EMPTY
                yield text, src, fmt_stats(spec.label, stage, st)
        except (OSError, ValueError) as e:       # 모델 폴더 없음·형식 오류 (mlx_lm.load)
            if app.models.is_loaded(model_key):
                raise                            # 생성 중 오류는 그대로 올림 (Gradio가 오류로 표시)
            raise gr.Error(f'{spec.label} 모델을 불러오지 못했습니다: {spec.path} ({type(e).__name__}). '
                           'uv run python -m lawbot.check_deploy로 확인하세요.') from e
    return chat

def model_choices(app):
    return [(f"{s.label} — {s.info}" + ('' if s.path.is_dir() else ' (모델 폴더 없음)'), s.key)
            for s in app.models.specs.values()]

def default_fmt(app):
    """모델을 바꾸면 형식 라디오를 그 모델의 기본 형식으로 (Fast 4항목 / Pro 간결). 사용자가 다시 바꿀 수 있음"""
    return lambda model_key: app.models.specs[model_key].fmt

def build_demo(app):                 # 화면만 만든다: 인증 검사·모델 로딩은 main에서
    p = app.profile
    with gr.Blocks(title=p.title) as demo:
        gr.Markdown(f'# {p.title}\n질문과 관련된 조문 3개를 찾아 그 조문을 근거로 답합니다. '
                    '법률 자문이 아닌 참고용 답변입니다.')
        sources = gr.Markdown(render=False)
        stats = gr.Markdown(render=False)
        with gr.Row():
            with gr.Column(scale=3):
                with gr.Row():
                    model = gr.Radio(model_choices(app), value=p.models[0].key, label='답변 모델',
                                     info='Fast는 4B, Pro는 14B 모델입니다. 같은 조문을 참고하며, Pro는 답변이 더 느립니다.')
                    fmt = gr.Radio([('간결', 'concise'), ('전체 (4항목)', 'full')], value=p.models[0].fmt,
                                   label='답변 형식')
                model.change(default_fmt(app), model, fmt, queue=False, api_visibility='private')
                gr.ChatInterface(
                    fn=make_chat(app),
                    additional_inputs=[model, fmt],
                    additional_outputs=[sources, stats],
                    examples=[[q, None, None] for q in EXAMPLES],   # None: 예시를 눌러도 현재 모델·형식 선택 유지
                    concurrency_limit=1)                            # 생성은 한 번에 하나 (두 모델 동시 생성 금지)
                stats.render()
            with gr.Column(scale=2):
                gr.Markdown('### 검색된 조문 (상위 3개)')
                sources.render()
    return demo

def main():
    config = get_local_launch_config()   # 인증 정보가 없으면 모델 로딩 전에 실행 거부
    demo = build_demo(load_app())
    demo.launch(**config)            # 127.0.0.1:7860, 로그인 필수, 공유 링크 없음

if __name__ == '__main__':
    main()
