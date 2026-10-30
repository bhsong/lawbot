# src/lawbot/app.py  →  uv run lawbot-app
import html
import gradio as gr
from lawbot import envfile, manifest
from lawbot.answer_format import answer_citations, render
from lawbot.service import ask, load_app
from lawbot.security import get_local_launch_config, instance_name

# v4 Fast로 검색·답변을 확인한 질문 (근로기준법 제26조, 최저임금법 제6조, 남녀고용평등법 제19조)
EXAMPLES = ['해고 예고 없이 갑자기 해고되면 어떻게 하나요?',
            '최저임금보다 적게 주기로 한 근로계약은 효력이 있나요?',
            '육아휴직을 썼다고 불이익을 줘도 되나요?']
EMPTY = '답변을 생성하지 못했습니다. 다시 질문해 주세요.'
SEARCHING = '⏳ 조문을 찾는 중…'         # '⏳'로 시작하는 문구는 bench_ui가 첫 글자로 세지 않음
WRITING = '⏳ 답변을 쓰는 중…'

def source_title(h):
    return f"{h['law'] + ' ' if 'law' in h else ''}{h['article_id']} {h['title']}"

def source_scores(h):
    return (f"유사도 {h['score']:.3f}"
            + (f" · 리랭커 {h['rerank_score']:.3f} (임베딩 {h['emb_rank']}위)" if 'rerank_score' in h else ''))

def source_card(h):
    return f"**{source_title(h)}** · {source_scores(h)}\n\n{h['doc'].split(chr(10), 1)[1]}"

def folded_card(h):
    """접힌 조문 카드 (펼치면 본문). 본문은 빈 줄 뒤에 두어 Markdown으로 그린다"""
    return (f"<details><summary><b>{html.escape(source_title(h))}</b> · {source_scores(h)}</summary>\n\n"
            f"{h['doc'].split(chr(10), 1)[1]}\n\n</details>")

def fmt_sources(hits, cited=None, refused=False):
    """오른쪽 조문 카드. cited(답변에서 인용한 검색 결과 조문)를 주면 인용한 조문은 펼쳐서 먼저, 나머지는 접어서 뒤에
    생성 중이거나(cited=None) 인용을 찾지 못한 답변은 세 개를 모두 펼친다 (사용자가 직접 확인할 수 있게)"""
    if not refused and not cited:
        return '\n\n---\n\n'.join(source_card(h) for h in hits)
    used = [h for h in hits if h in (cited or [])]
    rest = [h for h in hits if h not in used]
    parts = []
    if used:
        parts.append('**✅ 답변에서 인용한 조문**\n\n' + '\n\n---\n\n'.join(source_card(h) for h in used))
    if rest:
        parts.append(('**검색됐지만 답변에 쓰지 않은 조문**' if refused else '**검색만 된 조문**') + ' (눌러서 펼치기)\n\n'
                     + '\n\n'.join(folded_card(h) for h in rest))
    return '\n\n---\n\n'.join(parts)

def data_notice(app):
    """제목 아래 한 줄: 법령 기준일 (웹앱 레포의 매니페스트). 원격 모드에서 모델 서버의 데이터 버전이 다르면 경고"""
    m = manifest.load(app.profile.articles)
    line = (f"법령 기준일 {m['reference_date']} (데이터 {m['dataset_version']})" if m else
            '법령 기준일: 확인되지 않음 (데이터 매니페스트 없음)')
    client = getattr(app.retriever, 'client', None)
    if client is not None:
        from lawbot.remote import dataset_warnings
        try:
            warn = dataset_warnings(client.get_json('/info'), app.profile)
        except Exception:                # 화면은 그대로 띄운다 (서버 문제는 질문할 때 안내)
            warn = []
        if warn:
            line += ' · ⚠ 모델 서버의 법령 데이터가 이 웹앱과 다릅니다 (' + '; '.join(w.split(' (')[0] for w in warn) + ')'
    return line

def searching_stats(app):
    """질문 직후 처리 단계 (검색 → 리랭킹 → 생성)"""
    return '⏳ 조문 검색' + (' → 리랭킹' if app.reranker else '') + ' 중…'

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
    else:
        parts.append('✍️ 답변 생성 중…')
    return ' · '.join(parts)

def make_chat(app):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    # API 입력은 [질문, 모델 key, 형식] 세 개 (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)
    def chat(message, history, model_key, fmt):      # 단일 턴: history는 쓰지 않음
        if not (message or '').strip():
            yield '질문을 입력해 주세요.', '', ''
            return
        if any(s.key == model_key for s in app.profile.disabled):
            raise gr.Error('이 기기에서는 사용하지 않는 모델입니다. 다른 답변 모델을 고르세요.')
        spec = app.models.specs.get(model_key)
        if spec is None or fmt not in ('concise', 'full'):
            raise gr.Error(f'알 수 없는 선택: {model_key!r}, {fmt!r}')
        early = fmt == 'concise' and app.early_stop
        yield SEARCHING, '', searching_stats(app)
        try:
            for stage, hits, raw, st in ask(app, message, model_key, early_stop=early, fmt=fmt):
                src = fmt_sources(hits)          # 생성 중에는 조문 3개를 모두 펼쳐 둔다
                if stage == 'load':
                    yield f'⏳ {spec.label} 불러오는 중…', src, fmt_stats(spec.label, stage, st)
                    continue
                done = stage == 'done'
                text = render(raw, hits, fmt, done=done)
                if done:                         # 끝나면 인용한 조문과 검색만 된 조문을 나눈다
                    used, _, no = answer_citations(raw, hits)
                    src = fmt_sources(hits, used, no)
                    if not raw.strip():
                        text = EMPTY
                elif not text.strip():           # 표시 기호를 쓰는 중 (간결형)
                    text = WRITING
                yield text, src, fmt_stats(spec.label, stage, st)
        except Exception as e:                   # 유형은 service.ask가 붙임 (요청 로그의 error_type과 같음)
            kind = getattr(e, 'lawbot_error', None)
            if kind == 'model_server':           # 연결 실패·토큰 불일치·로딩 중
                raise gr.Error(f'모델 서버가 준비되지 않았습니다 ({type(e).__name__}). 잠시 뒤 다시 질문하고, 계속되면 '
                               'uv run python -m lawbot.check_deploy로 모델 서버를 확인하세요.') from e
            if kind == 'model_load':             # 모델 폴더 없음·형식 오류 (mlx_lm.load)
                raise gr.Error(f'{spec.label} 모델을 불러오지 못했습니다: {spec.path} ({type(e).__name__}). '
                               'uv run python -m lawbot.check_deploy로 확인하세요.') from e
            raise                                # 리랭킹·생성 중 오류는 그대로 올림 (Gradio가 오류로 표시)
    return chat

def model_choices(app):
    """쓰는 모델 + LAWBOT_MODELS에서 뺀 모델(비활성 표시, 고르면 되돌림)"""
    return ([(f"{s.label} — {s.info}" + ('' if s.path.is_dir() else ' (모델 폴더 없음)'), s.key)
             for s in app.models.specs.values()] +
            [(f'{s.label} — 이 기기에서 사용 안 함', s.key) for s in app.profile.disabled])

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
    name = instance_name()
    return f'{app.profile.title} · {name}' if name else app.profile.title

def build_demo(app):                 # 화면만 만든다: 인증 검사·모델 로딩은 main에서
    p, title = app.profile, page_title(app)
    with gr.Blocks(title=title) as demo:
        gr.Markdown(f'# {title}\n질문과 관련된 조문 3개를 찾아 그 조문을 근거로 답합니다. '
                    f'법률 자문이 아닌 참고용 답변입니다.\n\n{data_notice(app)}')
        sources = gr.Markdown(render=False)
        stats = gr.Markdown(render=False)
        with gr.Row():
            with gr.Column(scale=3):
                with gr.Row():
                    model = gr.Radio(model_choices(app), value=p.models[0].key, label='답변 모델',
                                     info='모델마다 같은 조문을 참고합니다.' if len(p.models) > 1 else
                                          ('비활성 모델은 이 기기에서 쓰지 않습니다.' if p.disabled else None))
                    fmt = gr.Radio([('간결', 'concise'), ('전체 (4항목)', 'full')], value=p.models[0].fmt,
                                   label='답변 형식')
                model.change(default_fmt(app), model, [model, fmt], queue=False, api_visibility='private')
                gr.ChatInterface(
                    fn=make_chat(app),
                    # 메시지 복사·재시도는 기본 제공. 공유(Hugging Face 업로드) 버튼은 상담 내용이 나가지 않게 뺀다
                    chatbot=gr.Chatbot(buttons=['copy', 'copy_all'], height=480),
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
    envfile.load()                       # .env (셸에 있는 변수 우선). 인증·모델 설정을 읽기 전에
    config = get_local_launch_config()   # 인증 정보가 없으면 모델 로딩 전에 실행 거부
    demo = build_demo(load_app())
    demo.launch(**config)            # 127.0.0.1:<LAWBOT_PORT>, 로그인 필수, 공유 링크 없음

if __name__ == '__main__':
    main()
