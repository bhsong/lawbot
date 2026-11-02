# src/lawbot/app.py  →  uv run lawbot-app
import gradio as gr

from lawbot import envfile, feedback, feedback_ui, settings
from lawbot.answer_format import answer_citations, render
from lawbot.security import SERVER_NAME
from lawbot.service import AskError, ask, load_app
from lawbot.view import (  # noqa: F401  (EXAMPLES·CHAT_CONCURRENCY_LIMIT: 테스트가 app에서 읽음, PR 3에서 정리)
    CHAT_CONCURRENCY_LIMIT,
    DISABLED_MODEL,
    DISCLAIMER,
    EMPTY,
    EXAMPLES,
    FORMATS,
    SEARCHING,
    WRITING,
    chat_concurrency,
    data_notice,
    error_message,
    fmt_sources,
    fmt_stats,
    intro_text,
    page_title,
    searching_stats,
    sources_title,
)
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')


def session_of(request):
    return getattr(request, 'session_hash', None) if request is not None else None


def make_chat(app, fb=None):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    # API 입력은 [질문, 모델 key, 형식] 세 개 (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)
    # request(gr.Request)는 Gradio가 넣는 특수 인자: API 입력 수는 그대로. fb(feedback.Feedback)가 있으면 끝난 턴을 기억한다
    def chat(message, history, model_key, fmt, request: gr.Request | None = None):  # 단일 턴: history는 순번에만 씀
        if not (message or '').strip():
            yield '질문을 입력해 주세요.', '', ''
            return
        if any(s.key == model_key for s in app.profile.disabled):
            raise gr.Error(DISABLED_MODEL)
        spec = app.models.specs.get(model_key)
        if spec is None or fmt not in ('concise', 'full'):
            raise gr.Error(f'알 수 없는 선택: {model_key!r}, {fmt!r}')
        yield SEARCHING, '', searching_stats(app, model_key)
        try:
            for stage, hits, raw, st in ask(app, message, model_key, fmt=fmt):
                src = fmt_sources(hits)  # 생성 중에는 조문을 모두 펼쳐 둔다
                if stage == 'load':
                    yield f'⏳ {spec.label} 불러오는 중…', src, fmt_stats(spec.label, stage, st)
                    continue
                done = stage == 'done'
                text = render(raw, hits, fmt, done=done)
                if done:  # 끝나면 인용한 조문과 검색만 된 조문을 나눈다
                    used, _, no = answer_citations(raw, hits)
                    src = fmt_sources(hits, used, no)
                    if not raw.strip():
                        text = EMPTY
                    if fb is not None:  # 이 답에 버튼을 누르면 저장할 입력 전체 (history의 assistant 수 = 이 답의 순번)
                        try:
                            fb.remember(
                                session_of(request),
                                sum(feedback.role(m) == 'assistant' for m in history or []),
                                feedback.turn_record(app, message, hits, raw, text, model_key, fmt, st),
                            )
                        except Exception as e:  # 피드백 기록 실패는 답변을 막지 않는다
                            log.warning(f'경고: 피드백용 턴 기록 실패 ({type(e).__name__})')
                elif not text.strip():  # 표시 기호를 쓰는 중 (간결형)
                    text = WRITING
                yield text, src, fmt_stats(spec.label, stage, st)
        except AskError as err:  # 유형(kind)은 요청 로그의 error_type과 같음. 원래 예외는 __cause__
            msg = error_message(err.kind, err.__cause__, spec)
            if msg is None:  # config(없는 모델 key·id): 설정 오류는 원래 예외 그대로
                raise (err.__cause__ or err) from None
            raise gr.Error(msg) from err.__cause__

    return chat


def model_choices(app):
    """쓰는 모델 + LAWBOT_MODELS에서 뺀 모델(비활성 표시, 고르면 되돌림)"""
    return [(f'{s.label} — {s.info}', s.key) for s in app.models.specs.values()] + [
        (f'{s.label} — 이 기기에서 사용 안 함', s.key) for s in app.profile.disabled
    ]


def default_fmt(app):
    """모델을 바꾸면 형식 라디오를 그 모델의 기본 형식으로 (Fast 4항목 / Pro 간결). 사용자가 다시 바꿀 수 있음
    비활성 모델을 고르면 안내하고 기본 모델로 되돌린다 → (모델, 형식)"""
    first = app.profile.models[0]

    def follow(model_key):
        spec = app.models.specs.get(model_key)
        if spec is None:
            gr.Warning('이 기기에서는 사용하지 않는 모델입니다.')
            return first.key, first.fmt
        return model_key, spec.fmt

    return follow


def make_on_feedback(fb):
    """Chatbot .like 이벤트 → feedback.Feedback.on_like (알림 팝업 없음)"""

    def on_feedback(history, d: gr.LikeData, request: gr.Request):
        fb.on_like(session_of(request), history, d.index, d.value, d.liked)

    return on_feedback


def web_settings(app):
    """app.main이 읽은 웹 설정이 있으면 True (load_app만 한 테스트·평가 앱은 지금 환경 변수로)"""
    cfg = getattr(app, 'settings', None)
    return cfg is not None and cfg.chat_concurrency is not None


def build_demo(
    app, fb=None
):  # 화면만 만든다: 인증 검사·모델 로딩은 main에서. fb: feedback.from_env() (None이면 버튼 없음)
    p, title = app.profile, page_title(app)
    with gr.Blocks(title=title) as demo:
        gr.Markdown(f'# {title}\n{intro_text(app)}{DISCLAIMER}\n\n{data_notice(app)}')
        sources = gr.Markdown(
            render=False, elem_id='lawbot-sources'
        )  # elem_id: 브라우저 E2E가 찾는 자리 (화면 변화 없음)
        stats = gr.Markdown(render=False, elem_id='lawbot-stats')
        with gr.Row():
            with gr.Column(scale=3):
                with gr.Row():
                    model = gr.Radio(
                        model_choices(app),
                        value=p.models[0].key,
                        label='답변 모델',
                        info='모델마다 같은 조문을 참고합니다.'
                        if len(p.models) > 1
                        else ('비활성 모델은 이 기기에서 쓰지 않습니다.' if p.disabled else None),
                    )
                    fmt = gr.Radio(FORMATS, value=p.models[0].fmt, label='답변 형식')
                model.change(default_fmt(app), model, [model, fmt], queue=False, api_visibility='private')
                # 메시지 복사·재시도는 기본 제공. 공유(Hugging Face 업로드) 버튼은 상담 내용이 나가지 않게 뺀다
                # 피드백: 👍('Like')과 깃발(이유 4종). 'Dislike'는 넣지 않는다
                chatbot = gr.Chatbot(
                    buttons=['copy', 'copy_all'],
                    height=480,
                    feedback_options=feedback.OPTIONS if fb is not None else [],
                )
                if fb is not None:
                    chatbot.like(make_on_feedback(fb), chatbot, None, queue=False, api_visibility='private')
                gr.ChatInterface(
                    fn=make_chat(app, fb),
                    chatbot=chatbot,
                    # 'never' 명시: 환경 변수 GRADIO_CHAT_FLAGGING_MODE로 기본 기능(.gradio/flagged에 화면 대화 저장)이 켜지지 않게
                    flagging_mode='never',
                    additional_inputs=[model, fmt],
                    additional_outputs=[sources, stats],
                    examples=[[q, None, None] for q in EXAMPLES],  # None: 예시를 눌러도 현재 모델·형식 선택 유지
                    concurrency_limit=(app.settings.chat_concurrency if web_settings(app) else chat_concurrency()),
                )  # 동시에 처리하는 질문 수 (기본 1). 모델 교체·동시 생성 순서는 모델 서버가 정한다
                stats.render()
            with gr.Column(scale=2):
                gr.Markdown(f'### {sources_title(app)}')
                sources.render()
    return demo


def main():
    envfile.load()  # .env (셸에 있는 변수 우선). 설정을 읽기 전에
    cfg = settings.load(
        web=True
    )  # 인증·포트·모델·피드백 폴더를 모두 검사: 틀리면 모델 서버에 접속하기 전에 한꺼번에 거부
    a = load_app(cfg)
    fb = cfg.feedback  # LAWBOT_FEEDBACK=1이면 저장 폴더를 확인한 Feedback
    demo = build_demo(a, fb) if fb is not None else build_demo(a)  # 꺼져 있으면 기존과 같은 호출
    demo.launch(
        server_name=SERVER_NAME, server_port=cfg.port, auth=cfg.auth, share=False, **feedback_ui.launch_options(fb)
    )  # 127.0.0.1:<LAWBOT_PORT>, 로그인 필수, 공유 링크 없음


if __name__ == '__main__':
    main()
