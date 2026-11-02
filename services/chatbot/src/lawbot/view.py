# lawbot/view.py  →  화면 문구와 조문 카드·측정 줄 조립 (Gradio 화면 app.py와 새 화면 API web.py가 함께 씀)
# 이 모듈은 gradio를 import하지 않는다 (tests/test_web.py가 검사)
import html
import os

from lawbot import manifest
from lawbot.decree import is_decree
from lawbot.security import instance_name
from lawbot.service import uses_decrees

# v4 Fast로 검색·답변을 확인한 질문 (근로기준법 제26조, 최저임금법 제6조, 남녀고용평등법 제19조)
EXAMPLES = [
    '해고 예고 없이 갑자기 해고되면 어떻게 하나요?',
    '최저임금보다 적게 주기로 한 근로계약은 효력이 있나요?',
    '육아휴직을 썼다고 불이익을 줘도 되나요?',
]
EMPTY = '답변을 생성하지 못했습니다. 다시 질문해 주세요.'
SEARCHING = '⏳ 조문을 찾는 중…'  # '⏳'로 시작하는 문구는 첫 글자 측정에서 제외 (화면 지연 측정 기준)
WRITING = '⏳ 답변을 쓰는 중…'
DISCLAIMER = '법률 자문이 아닌 참고용 답변입니다.'
DISABLED_MODEL = '이 기기에서는 사용하지 않는 모델입니다. 다른 답변 모델을 고르세요.'
FORMATS = [('간결', 'concise'), ('전체 (4항목)', 'full')]
CITED_LABEL = '✅ 답변에서 인용한 조문'
# 검색·리랭킹·생성 단계 오류의 화면 안내 (요청 로그 error_type → 문구). 원래 예외를 그대로 올리면 Gradio가 안내 없이
# 'Error' 표시만 남기고 말풍선은 '답변을 쓰는 중…'에 멈춘다 (2026-11-02 e2e_stub에서 확인)
STAGE_ERRORS = {
    'search': '조문을 검색하지 못했습니다',
    'rerank': '조문 순위를 매기지 못했습니다',
    'generation': '답변을 생성하지 못했습니다',
}


def source_title(h):
    """하위법령은 앞에 〔시행령〕·〔시행규칙〕 표시 (법률 카드와 구분)"""
    tag = f'〔{h["kind"]}〕 ' if is_decree(h) else ''
    return f'{tag}{h["law"] + " " if "law" in h else ""}{h["article_id"]} {h["title"]}'


def source_scores(h):
    where = '하위법령 임베딩' if is_decree(h) else '임베딩'
    return f'유사도 {h["score"]:.3f}' + (
        f' · 리랭커 {h["rerank_score"]:.3f} ({where} {h["emb_rank"]}위)' if 'rerank_score' in h else ''
    )


def source_body(h):
    return h['doc'].split('\n', 1)[1]


def source_card(h):
    return f'**{source_title(h)}** · {source_scores(h)}\n\n{source_body(h)}'


def folded_card(h):
    """접힌 조문 카드 (펼치면 본문). 본문은 빈 줄 뒤에 두어 Markdown으로 그린다"""
    return (
        f'<details><summary><b>{html.escape(source_title(h))}</b> · {source_scores(h)}</summary>\n\n'
        f'{source_body(h)}\n\n</details>'
    )


def source_groups(hits, cited=None, refused=False):
    """오른쪽 조문 카드 묶음 [(제목 또는 None, 펼침 여부, 조문 목록)]. cited(답변에서 인용한 검색 결과 조문)를 주면
    인용한 조문은 펼쳐서 먼저, 나머지는 접어서 뒤에. 생성 중이거나(cited=None) 인용을 찾지 못한 답변은 모두 펼친다
    (사용자가 직접 확인할 수 있게). hits 순서(법률 → 첨부한 하위법령)를 묶음 안에서도 지킨다"""
    if not refused and not cited:
        return [(None, True, list(hits))]
    used = [h for h in hits if h in (cited or [])]
    rest = [h for h in hits if h not in used]
    groups = []
    if used:
        groups.append((CITED_LABEL, True, used))
    if rest:
        groups.append(('검색됐지만 답변에 쓰지 않은 조문' if refused else '검색만 된 조문', False, rest))
    return groups


def fmt_sources(hits, cited=None, refused=False):
    """source_groups를 Markdown 한 덩어리로 (Gradio 화면)"""
    parts = []
    for label, opened, group in source_groups(hits, cited, refused):
        if label is None:
            parts.append('\n\n---\n\n'.join(source_card(h) for h in group))
        elif opened:
            parts.append(f'**{label}**\n\n' + '\n\n---\n\n'.join(source_card(h) for h in group))
        else:
            parts.append(f'**{label}** (눌러서 펼치기)\n\n' + '\n\n'.join(folded_card(h) for h in group))
    return '\n\n---\n\n'.join(parts)


def data_notice(app):
    """제목 아래 한 줄: 법령 기준일 (웹앱 레포의 매니페스트. 조문 데이터는 웹앱에만 있음)"""
    m = manifest.load(app.profile.articles)
    kinds = manifest.kinds_text(m)  # 하위법령을 켜면 '법률 415 · 시행령 273 · 시행규칙 124'
    return (
        f'법령 기준일 {m["reference_date"]} (데이터 {m["dataset_version"]}{" · " + kinds if kinds else ""})'
        if m
        else '법령 기준일: 확인되지 않음 (데이터 매니페스트 없음)'
    )


def searching_stats(app, model_key=None):
    """질문 직후 처리 단계 (검색 → 리랭킹 → 하위법령 → 생성)"""
    return (
        '⏳ 조문 검색'
        + (' → 리랭킹' if app.reranker else '')
        + (' → 시행령·시행규칙' if uses_decrees(app, model_key) else '')
        + ' 중…'
    )


def loading_text(spec):
    return f'⏳ {spec.label} 불러오는 중…'


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


def error_message(kind, cause, spec):
    """ask 오류 종류(요청 로그 error_type) → 화면 안내. 설정 오류(config 등 안내가 없는 종류)는 None"""
    name = type(cause).__name__
    if kind == 'model_server_timeout':
        return f'모델 서버 응답이 없습니다 ({cause}). 잠시 뒤 다시 질문하세요.'
    if kind == 'model_server_busy':
        return '모델 서버가 바쁩니다 (대기 중인 질문이 많음). 잠시 뒤 다시 질문하세요.'
    if kind == 'model_server':  # 모델 서버(생성 API) 연결 실패·토큰 불일치·로딩 중
        return (
            f'모델 서버가 준비되지 않았습니다 ({name}). 잠시 뒤 다시 질문하고, 계속되면 '
            'uv run python -m lawbot.check_deploy로 모델 서버를 확인하세요.'
        )
    if kind == 'model_load':  # 모델 서버의 모델 폴더 없음·형식 오류, 웹앱의 system 파일 없음
        return (
            f'{spec.label} 모델을 불러오지 못했습니다: {spec.model} ({name}). '
            'uv run python -m lawbot_model_server.check와 uv run python -m lawbot.check_deploy로 확인하세요.'
        )
    stage_msg = STAGE_ERRORS.get(kind)
    return None if stage_msg is None else f'{stage_msg} ({name}). 다시 질문해 주세요.'


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


def sources_title(app):
    if any(uses_decrees(app, k) for k in app.models.specs):
        return f'검색된 조문 (법률 3개 + 시행령·시행규칙 최대 {app.decree.k}개)'
    return '검색된 조문 (상위 3개)'


CHAT_CONCURRENCY_LIMIT = 16


def chat_concurrency(env=None):
    """LAWBOT_CHAT_CONCURRENCY: 웹앱이 동시에 처리하는 질문 수 (기본 1). 모델 서버의 LAWBOT_MAX_CONCURRENT와 따로 둔다
    (서버를 다른 앱과 같이 쓰므로). 서버 상한보다 크면 넘는 질문은 서버에서 기다린다"""
    env = os.environ if env is None else env
    raw = (env.get('LAWBOT_CHAT_CONCURRENCY') or '1').strip()
    if not raw.isdigit() or not 1 <= int(raw) <= CHAT_CONCURRENCY_LIMIT:
        raise ValueError(f'LAWBOT_CHAT_CONCURRENCY는 1–{CHAT_CONCURRENCY_LIMIT} 정수: {raw!r}')
    return int(raw)
