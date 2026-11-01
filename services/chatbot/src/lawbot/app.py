# src/lawbot/app.py  →  uv run lawbot-app
import html
import os

import gradio as gr

from lawbot import envfile, feedback, feedback_ui, manifest, settings
from lawbot.answer_format import answer_citations, render
from lawbot.decree import is_decree
from lawbot.security import SERVER_NAME, instance_name
from lawbot.service import ask, load_app, uses_decrees

# v4 Fast로 검색·답변을 확인한 질문 (근로기준법 제26조, 최저임금법 제6조, 남녀고용평등법 제19조)
EXAMPLES = [
    '해고 예고 없이 갑자기 해고되면 어떻게 하나요?',
    '최저임금보다 적게 주기로 한 근로계약은 효력이 있나요?',
    '육아휴직을 썼다고 불이익을 줘도 되나요?',
]
EMPTY = '답변을 생성하지 못했습니다. 다시 질문해 주세요.'
SEARCHING = '⏳ 조문을 찾는 중…'  # '⏳'로 시작하는 문구는 첫 글자 측정에서 제외 (화면 지연 측정 기준)
WRITING = '⏳ 답변을 쓰는 중…'


def source_title(h):
    """하위법령은 앞에 〔시행령〕·〔시행규칙〕 표시 (법률 카드와 구분)"""
    tag = f'〔{h["kind"]}〕 ' if is_decree(h) else ''
    return f'{tag}{h["law"] + " " if "law" in h else ""}{h["article_id"]} {h["title"]}'


def source_scores(h):
    where = '하위법령 임베딩' if is_decree(h) else '임베딩'
    return f'유사도 {h["score"]:.3f}' + (
        f' · 리랭커 {h["rerank_score"]:.3f} ({where} {h["emb_rank"]}위)' if 'rerank_score' in h else ''
    )


def source_card(h):
    return f'**{source_title(h)}** · {source_scores(h)}\n\n{h["doc"].split(chr(10), 1)[1]}'


def folded_card(h):
    """접힌 조문 카드 (펼치면 본문). 본문은 빈 줄 뒤에 두어 Markdown으로 그린다"""
    return (
        f'<details><summary><b>{html.escape(source_title(h))}</b> · {source_scores(h)}</summary>\n\n'
        f'{h["doc"].split(chr(10), 1)[1]}\n\n</details>'
    )


def fmt_sources(hits, cited=None, refused=False):
    """오른쪽 조문 카드. cited(답변에서 인용한 검색 결과 조문)를 주면 인용한 조문은 펼쳐서 먼저, 나머지는 접어서 뒤에
    생성 중이거나(cited=None) 인용을 찾지 못한 답변은 모두 펼친다 (사용자가 직접 확인할 수 있게)
    hits 순서(법률 → 첨부한 하위법령)를 그룹 안에서도 지킨다: 하위법령 카드는 항상 법률 카드 뒤"""
    if not refused and not cited:
        return '\n\n---\n\n'.join(source_card(h) for h in hits)
    used = [h for h in hits if h in (cited or [])]
    rest = [h for h in hits if h not in used]
    parts = []
    if used:
        parts.append('**✅ 답변에서 인용한 조문**\n\n' + '\n\n---\n\n'.join(source_card(h) for h in used))
    if rest:
        parts.append(
            ('**검색됐지만 답변에 쓰지 않은 조문**' if refused else '**검색만 된 조문**')
            + ' (눌러서 펼치기)\n\n'
            + '\n\n'.join(folded_card(h) for h in rest)
        )
    return '\n\n---\n\n'.join(parts)


def data_notice(app):
    """제목 아래 한 줄: 법령 기준일 (웹앱 레포의 매니페스트. 조문 데이터는 웹앱에만 있음)"""
    m = manifest.load(app.profile.articles)
    kinds = manifest.kinds_text(m)  # 하위법령을 켜면 '법률 415 · 시행령 273 · 시행규칙 124'
    line = (
        f'법령 기준일 {m["reference_date"]} (데이터 {m["dataset_version"]}{" · " + kinds if kinds else ""})'
        if m
        else '법령 기준일: 확인되지 않음 (데이터 매니페스트 없음)'
    )
    return line


def searching_stats(app, model_key=None):
    """질문 직후 처리 단계 (검색 → 리랭킹 → 하위법령 → 생성)"""
    return (
        '⏳ 조문 검색'
        + (' → 리랭킹' if app.reranker else '')
        + (' → 시행령·시행규칙' if uses_decrees(app, model_key) else '')
        + ' 중…'
    )


def fmt_stats(label, stage, st):
    """측정 칸: 서버 기준 시간 (브라우저 표시 지연은 포함하지 않음)"""
    if stage == 'load':
        return f'⏳ {label} 불러오는 중… (모델 전환 대기, 검색 {st["search"]:.2f}초)'
    parts = [label, f'검색 {st["search"]:.2f}초']
    if 'rerank' in st:
        parts.append(f'리랭킹 {st["rerank"]:.2f}초')
    if 'decree_rerank' in st:
        parts.append(f'시행령·시행규칙 {st["decree_search"] + st["decree_rerank"]:.2f}초 ({st["n_decree"]}개 첨부)')
    if st['load'] is not None:
        parts.append(f'모델 전환 {st["load"]:.1f}초')
    if 'ttft' in st:
        parts.append(f'첫 토큰 {st["ttft"]:.1f}초')
    if 'first_text' in st:
        parts.append(f'첫 글자 {st["first_text"]:.1f}초')
    if stage == 'done':
        if st.get('tps'):
            parts.append(f'{st["tps"]:.1f} tok/s')
        parts.append(f'완료 {st["total"]:.1f}초')
        if st.get('finish') == 'length':
            parts.append('⚠ 최대 길이에서 잘림')
    else:
        parts.append('✍️ 답변 생성 중…')
    return ' · '.join(parts)


def session_of(request):
    return getattr(request, 'session_hash', None) if request is not None else None


def make_chat(app, fb=None):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    # API 입력은 [질문, 모델 key, 형식] 세 개 (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)
    # request(gr.Request)는 Gradio가 넣는 특수 인자: API 입력 수는 그대로. fb(feedback.Feedback)가 있으면 끝난 턴을 기억한다
    def chat(message, history, model_key, fmt, request: gr.Request = None):  # 단일 턴: history는 순번에만 씀
        if not (message or '').strip():
            yield '질문을 입력해 주세요.', '', ''
            return
        if any(s.key == model_key for s in app.profile.disabled):
            raise gr.Error('이 기기에서는 사용하지 않는 모델입니다. 다른 답변 모델을 고르세요.')
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
                            print(f'[lawbot] 경고: 피드백용 턴 기록 실패 ({type(e).__name__})')
                elif not text.strip():  # 표시 기호를 쓰는 중 (간결형)
                    text = WRITING
                yield text, src, fmt_stats(spec.label, stage, st)
        except Exception as e:  # 유형은 service.ask가 붙임 (요청 로그의 error_type과 같음)
            kind = getattr(e, 'lawbot_error', None)
            if kind == 'model_server_timeout':
                raise gr.Error(f'모델 서버 응답이 없습니다 ({e}). 잠시 뒤 다시 질문하세요.') from e
            if kind == 'model_server_busy':
                raise gr.Error('모델 서버가 바쁩니다 (대기 중인 질문이 많음). 잠시 뒤 다시 질문하세요.') from e
            if kind == 'model_server':  # 모델 서버(생성 API) 연결 실패·토큰 불일치·로딩 중
                raise gr.Error(
                    f'모델 서버가 준비되지 않았습니다 ({type(e).__name__}). 잠시 뒤 다시 질문하고, 계속되면 '
                    'uv run python -m lawbot.check_deploy로 모델 서버를 확인하세요.'
                ) from e
            if kind == 'model_load':  # 모델 서버의 모델 폴더 없음·형식 오류, 웹앱의 system 파일 없음
                raise gr.Error(
                    f'{spec.label} 모델을 불러오지 못했습니다: {spec.model} ({type(e).__name__}). '
                    'uv run python -m lawbot_model_server.check와 uv run python -m lawbot.check_deploy로 확인하세요.'
                ) from e
            raise  # 리랭킹·생성 중 오류는 그대로 올림 (Gradio가 오류로 표시)

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


def page_title(app):
    """화면·탭 제목. LAWBOT_INSTANCE가 있으면 붙인다 (예: '노동법 챗봇 · 개발', 운영/개발 구분)"""
    cfg = getattr(app, 'settings', None)
    name = cfg.instance if cfg is not None and cfg.chat_concurrency is not None else instance_name()
    return f'{app.profile.title} · {name}' if name else app.profile.title


def intro_text(app):
    """제목 아래 안내: 하위법령 첨부 모델이 있으면 그 모델 이름과 함께 알린다"""
    names = [s.label for s in app.models.specs.values() if uses_decrees(app, s.key)]
    if not names:
        return '질문과 관련된 조문 3개를 찾아 그 조문을 근거로 답합니다. '
    return (
        f'질문과 관련된 법률 조문 3개를 찾아 그 조문을 근거로 답합니다. {", ".join(names)}는 관련 시행령·시행규칙을 '
        f'최대 {app.decree.k}개 더 붙입니다. '
    )


def sources_heading(app):
    if any(uses_decrees(app, k) for k in app.models.specs):
        return f'### 검색된 조문 (법률 3개 + 시행령·시행규칙 최대 {app.decree.k}개)'
    return '### 검색된 조문 (상위 3개)'


def make_on_feedback(fb):
    """Chatbot .like 이벤트 → feedback.Feedback.on_like (알림 팝업 없음)"""

    def on_feedback(history, d: gr.LikeData, request: gr.Request):
        fb.on_like(session_of(request), history, d.index, d.value, d.liked)

    return on_feedback


CHAT_CONCURRENCY_LIMIT = 16


def chat_concurrency(env=None):
    """LAWBOT_CHAT_CONCURRENCY: 웹앱이 동시에 처리하는 질문 수 (기본 1). 모델 서버의 LAWBOT_MAX_CONCURRENT와 따로 둔다
    (서버를 다른 앱과 같이 쓰므로). 서버 상한보다 크면 넘는 질문은 서버에서 기다린다"""
    env = os.environ if env is None else env
    raw = (env.get('LAWBOT_CHAT_CONCURRENCY') or '1').strip()
    if not raw.isdigit() or not 1 <= int(raw) <= CHAT_CONCURRENCY_LIMIT:
        raise ValueError(f'LAWBOT_CHAT_CONCURRENCY는 1–{CHAT_CONCURRENCY_LIMIT} 정수: {raw!r}')
    return int(raw)


def web_settings(app):
    """app.main이 읽은 웹 설정이 있으면 True (load_app만 한 테스트·평가 앱은 지금 환경 변수로)"""
    cfg = getattr(app, 'settings', None)
    return cfg is not None and cfg.chat_concurrency is not None


def build_demo(
    app, fb=None
):  # 화면만 만든다: 인증 검사·모델 로딩은 main에서. fb: feedback.from_env() (None이면 버튼 없음)
    p, title = app.profile, page_title(app)
    with gr.Blocks(title=title) as demo:
        gr.Markdown(f'# {title}\n{intro_text(app)}법률 자문이 아닌 참고용 답변입니다.\n\n{data_notice(app)}')
        sources = gr.Markdown(render=False)
        stats = gr.Markdown(render=False)
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
                    fmt = gr.Radio(
                        [('간결', 'concise'), ('전체 (4항목)', 'full')], value=p.models[0].fmt, label='답변 형식'
                    )
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
                gr.Markdown(sources_heading(app))
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
    launch = {'server_name': SERVER_NAME, 'server_port': cfg.port, 'auth': cfg.auth, 'share': False}
    demo.launch(**launch, **feedback_ui.launch_options(fb))  # 127.0.0.1:<LAWBOT_PORT>, 로그인 필수, 공유 링크 없음


if __name__ == '__main__':
    main()
