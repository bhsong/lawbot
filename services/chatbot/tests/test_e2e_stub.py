# Browser E2E: the real web app, a protected generation HTTP fake, and fake embedding/rerank models.
# These tests never load real models and are not evidence of real-model E2E success.
# 화면 요소는 역할·라벨·테스트 id로 찾는다 (e2e_support). 접속은 127.0.0.1만
import base64
import csv
import hashlib
import json
import time

import httpx
import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from admin_test_data import add_feedback, seed_qa
from conftest import ADMIN_PW, ADMIN_USER, free_port
from lawbot import manifest
from lawbot.db import cli
from lawbot.db.connection import connect
from lawbot.deploy import article_paths
from lawbot.view import V5_EXAMPLES
from test_migrate_csv_to_sqlite import run as migrate

pytestmark = pytest.mark.e2e_stub

QUESTION = '해고 예고 없이 갑자기 해고되면 어떻게 하나요?'
FAKE_ANSWER = '가짜 모델 답변입니다.'
READ_TIMEOUT_S = 2  # 웹앱 LAWBOT_MODEL_READ_TIMEOUT (slow 모드의 토큰 간격 5초보다 짧게)
PHONE = {'viewport': {'width': 390, 'height': 844}, 'is_mobile': True, 'has_touch': True}


@pytest.fixture(scope='module')
def feedback_db(tmp_path_factory):
    path = tmp_path_factory.mktemp('e2e_feedback') / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(path)])
    return path


@pytest.fixture(scope='module')
def stack(tmp_path_factory, feedback_db):
    s = e2e.Stack(
        tmp_path_factory.mktemp('e2e_stub'),
        read_timeout=READ_TIMEOUT_S,
        LAWBOT_FEEDBACK='1',
        LAWBOT_DB=str(feedback_db),
    )
    yield s
    s.stop()


def alert(page, text, timeout=e2e.ANSWER_TIMEOUT):
    e2e.answer(page).get_by_role('alert').get_by_text(text).wait_for(timeout=timeout)


@pytest.fixture
def ui(stack, page, request):
    """정상 모드 모델 서버 + 로그인한 화면. 실패하면 진단 산출물을 results/e2e_stub/에 남긴다"""
    stack.ensure('normal')
    e2e.login(page, stack.url)
    e2e.textbox(page).wait_for()
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


@pytest.fixture
def fresh(stack, page, request):
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


def test_generation_server_has_separate_protected_model_contract(stack):
    stack.ensure('normal')
    assert stack.chat_url != stack.ms_url
    for path in ('/v1/models', '/v1/chat/completions'):
        assert httpx.get(stack.chat_url + path, trust_env=False).status_code == 401
        assert (
            httpx.get(
                stack.chat_url + path,
                headers={'Authorization': f'Bearer {stack.token}'},
                trust_env=False,
            ).status_code
            == 401
        )
        assert (
            httpx.get(
                stack.chat_url + path,
                params={'api_key': stack.chat_token},
                trust_env=False,
            ).status_code
            == 401
        )
        assert (
            httpx.get(
                stack.chat_url + path,
                headers={'Authorization': f'Basic {stack.chat_token}'},
                trust_env=False,
            ).status_code
            == 401
        )
    response = httpx.get(
        stack.chat_url + '/v1/models',
        headers={'Authorization': f'Bearer {stack.chat_token}'},
        trust_env=False,
    )
    assert response.status_code == 200
    assert response.json() == {
        'object': 'list',
        'data': [
            {
                'id': stack.chat_model,
                'object': 'model',
                'revision': e2e.STUB_MODEL_REVISION,
                'mlx_lm_version': '0.31.2',
                'max_concurrent': 1,
            }
        ],
    }
    models = httpx.get(
        stack.ms_url + '/v1/models',
        headers={'Authorization': f'Bearer {stack.token}'},
        trust_env=False,
    ).json()['data']
    assert {model['type'] for model in models} == {'embedding', 'rerank'}
    assert all(model['id'] != stack.chat_model for model in models)


def test_generation_stream_exposes_usage_and_separate_reasoning(stack):
    stack.ensure('normal')
    body = {'model': stack.chat_model, 'messages': [{'role': 'user', 'content': QUESTION}], 'stream': True}
    assert httpx.post(stack.chat_url + '/v1/chat/completions', json=body, trust_env=False).status_code == 401
    assert (
        httpx.post(
            stack.chat_url + '/v1/chat/completions',
            json=body,
            headers={'Authorization': f'Bearer {stack.token}'},
            trust_env=False,
        ).status_code
        == 401
    )
    with httpx.stream(
        'POST',
        stack.chat_url + '/v1/chat/completions',
        json=body | {'stream_options': {'include_usage': True}},
        headers={'Authorization': f'Bearer {stack.chat_token}'},
        trust_env=False,
    ) as response:
        assert response.status_code == 200
        events = [line.removeprefix('data: ') for line in response.iter_lines() if line.startswith('data: ')]
    assert events[-1] == '[DONE]'
    chunks = [json.loads(event) for event in events[:-1]]
    deltas = [choice['delta'] for chunk in chunks for choice in chunk['choices']]
    assert any(delta.get('reasoning') == 'private synthetic reasoning' for delta in deltas)
    assert 'private synthetic reasoning' not in ''.join(delta.get('content', '') for delta in deltas)
    assert chunks[-1]['usage'] == {'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17}


def test_generation_nonstream_exposes_usage_without_mixing_reasoning(stack):
    stack.ensure('normal')
    response = httpx.post(
        stack.chat_url + '/v1/chat/completions',
        json={'model': stack.chat_model, 'messages': [{'role': 'user', 'content': QUESTION}]},
        headers={'Authorization': f'Bearer {stack.chat_token}'},
        trust_env=False,
    )
    assert response.status_code == 200
    body = response.json()
    message = body['choices'][0]['message']
    assert FAKE_ANSWER in message['content']
    assert message['reasoning'] == 'private synthetic reasoning'
    assert message['reasoning'] not in message['content']
    assert body['usage'] == {'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17}


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '127.0.0.1:1', 'localhost:1', 'example.com:80'])
@pytest.mark.parametrize('method,path', [('GET', '/v1/models'), ('POST', '/v1/chat/completions')])
def test_generation_rejects_foreign_host_and_port(stack, host, method, path):
    stack.ensure('normal')
    response = httpx.request(
        method,
        stack.chat_url + path,
        json={'model': stack.chat_model, 'messages': [{'role': 'user', 'content': QUESTION}]},
        headers={'Authorization': f'Bearer {stack.chat_token}', 'Host': host},
        trust_env=False,
    )
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'invalid_host'


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost'])
def test_generation_accepts_only_configured_loopback_port(stack, host):
    stack.ensure('normal')
    response = httpx.get(
        stack.chat_url + '/v1/models',
        headers={'Authorization': f'Bearer {stack.chat_token}', 'Host': f'{host}:{stack.chat_port}'},
        trust_env=False,
    )
    assert response.status_code == 200


@pytest.mark.parametrize('field', ['adapters', 'draft_model'])
@pytest.mark.parametrize('stream', [False, True])
def test_generation_rejects_model_override_fields(stack, field, stream):
    stack.ensure('normal')
    response = httpx.post(
        stack.chat_url + '/v1/chat/completions',
        json={
            'model': stack.chat_model,
            'messages': [{'role': 'user', 'content': QUESTION}],
            'stream': stream,
            field: 'synthetic-override',
        },
        headers={'Authorization': f'Bearer {stack.chat_token}'},
        trust_env=False,
    )
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'unsupported_parameter'


@pytest.mark.parametrize('stream', [False, True])
def test_generation_rejects_other_model_ids(stack, stream):
    stack.ensure('normal')
    response = httpx.post(
        stack.chat_url + '/v1/chat/completions',
        json={'model': 'other-model', 'messages': [{'role': 'user', 'content': QUESTION}], 'stream': stream},
        headers={'Authorization': f'Bearer {stack.chat_token}'},
        trust_env=False,
    )
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'model_not_found'


def test_generation_has_no_private_health_endpoint(stack):
    stack.ensure('normal')
    response = httpx.get(
        stack.chat_url + '/health',
        headers={'Authorization': f'Bearer {stack.chat_token}'},
        trust_env=False,
    )
    assert response.status_code == 404


def test_reasoning_is_not_shown_and_usage_is_logged(stack, ui):
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    expect(e2e.answer(ui)).to_contain_text(FAKE_ANSWER)
    expect(e2e.answer(ui)).not_to_contain_text('private synthetic reasoning')
    row = e2e.wait_request_log(ui, stack.request_log, before)
    assert row['prompt_tokens'] == 12 and row['gen_tokens'] == 5


def test_feedback_button_saves_turn(stack, ui, feedback_db):
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    expect(e2e.answer(ui)).to_contain_text(FAKE_ANSWER)
    like = ui.get_by_role('button', name='좋아요')
    read_feedback = e2e.capture_response_body(ui, '/api/feedback')
    with ui.expect_response(f'{stack.url}/api/feedback', timeout=e2e.ANSWER_TIMEOUT) as response:
        like.click()
    assert response.value.ok and json.loads(read_feedback()) == {'saved': True}
    expect(like).to_have_attribute('aria-pressed', 'true')

    with connect(feedback_db) as conn:
        rows = conn.execute('SELECT record, origin FROM feedbacks').fetchall()
    assert len(rows) == 1
    raw, origin = rows[0]
    record = json.loads(raw)
    assert origin == 'web' and record['question'] == QUESTION and record['label'] == 'like'
    assert FAKE_ANSWER in record['answer_shown']
    rid = e2e.wait_request_log(ui, stack.request_log, before)['request_id']
    assert record['request_id'] == rid
    with connect(feedback_db) as conn:
        assert conn.execute('SELECT feedback_turn_id FROM consults WHERE request_id = ?', (rid,)).fetchone() == (
            record['turn_id'],
        )


# ---- 로그인 ----
def test_login_required(stack, fresh):
    fresh.goto(stack.url)
    fresh.get_by_label('비밀번호').wait_for()
    assert e2e.textbox(fresh).count() == 0  # 로그인 전에는 질문 입력창이 없다
    e2e.login(fresh, stack.url, pw='wrong-pw')
    fresh.get_by_role('alert').wait_for()
    assert e2e.textbox(fresh).count() == 0
    e2e.login(fresh, stack.url)
    e2e.textbox(fresh).wait_for()


@pytest.fixture
def admin_stack(tmp_path, page, request, admin_hash):
    db = tmp_path / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(db)])
    seed_qa(db)
    s = e2e.Stack(
        tmp_path,
        read_timeout=READ_TIMEOUT_S,
        capture_wire=True,
        LAWBOT_DB=str(db),
        LAWBOT_ADMIN_USER=ADMIN_USER,
        LAWBOT_ADMIN_PW_HASH=admin_hash,
    )
    try:
        yield s, db
        e2e.keep_on_failure(request, 'e2e_stub', s.logs(), page)
    finally:
        s.stop()


def test_admin_can_log_in_on_existing_screen(admin_stack, page):
    s, db = admin_stack
    e2e.login(page, s.url, user=ADMIN_USER, pw=ADMIN_PW)
    e2e.textbox(page).wait_for(timeout=e2e.ANSWER_TIMEOUT)
    assert page.request.get(f'{s.url}/api/me', timeout=e2e.ANSWER_TIMEOUT).json() == {
        'user': ADMIN_USER,
        'role': 'admin',
    }
    with connect(db) as conn:
        assert conn.execute('SELECT actor, role, action FROM audit_events').fetchall() == [
            (ADMIN_USER, 'admin', 'login_ok')
        ]


@pytest.mark.parametrize('width', [390, 1280])
def test_admin_reviews_and_promotes_a_like(admin_stack, page, width):
    s, db = admin_stack
    add_feedback(db, 'admin-like')
    page.set_viewport_size({'width': width, 'height': 844})
    e2e.login(page, s.url, user=ADMIN_USER, pw=ADMIN_PW)
    page.get_by_role('radio', name='피드백 & 법령 관리').click(timeout=3000)
    calendar = page.get_by_role('region', name='법령 캘린더')
    likes = page.get_by_role('region', name='좋아요')
    flags = page.get_by_role('region', name='문제 신고')
    expect(calendar.get_by_text('캘린더 연결 안 됨')).to_be_visible(timeout=e2e.ANSWER_TIMEOUT)
    expect(likes.get_by_role('button', name='해고 예고가 필요한가요?')).to_be_visible()
    top, left, right = (panel.bounding_box() for panel in (calendar, likes, flags))
    assert top and left and right and top['y'] < left['y']
    if width == 390:
        assert left['y'] < right['y']
    else:
        assert left['y'] == right['y'] and left['x'] < right['x']
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')

    likes.get_by_role('button', name='해고 예고가 필요한가요?').click()
    dialog = page.get_by_role('dialog', name='좋아요 검수')
    expect(dialog.get_by_label('교정 질문')).to_be_focused()
    dialog.get_by_role('radio', name='정답', exact=True).check()
    dialog.get_by_label('근거 조문 검색').fill('근로기준법 제26조')
    dialog.get_by_role('button', name='근로기준법 제26조', exact=True).click()
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    review_path = '/api/admin/feedbacks/admin-like/review'
    read_review = e2e.capture_response_body(page, review_path)
    with page.expect_response(f'{s.url}{review_path}', timeout=e2e.ANSWER_TIMEOUT) as review:
        dialog.get_by_role('button', name='검수 저장', exact=True).click()
    assert review.value.status == 201 and json.loads(read_review())['status'] == 'reviewed'
    likes.get_by_label('상태').select_option('reviewed')
    likes.get_by_role('checkbox', name='해고 예고가 필요한가요? 선택').check()
    promotion_path = '/api/admin/feedbacks/promote-up'
    read_promotion = e2e.capture_response_body(page, promotion_path)
    with page.expect_response(f'{s.url}{promotion_path}', timeout=e2e.ANSWER_TIMEOUT) as promotion:
        likes.get_by_role('button', name='선택 승격 (1)').click()
    assert promotion.value.ok and json.loads(read_promotion())['results'][0]['ok']
    likes.get_by_label('상태').select_option('promoted')
    expect(likes.get_by_role('list', name='피드백 목록').get_by_text('승격됨', exact=True)).to_be_visible()

    page.context.clear_cookies()
    e2e.login(page, s.url)
    expect(e2e.textbox(page)).to_be_visible()
    expect(page.get_by_role('radio', name='피드백 & 법령 관리')).to_have_count(0)
    denied = page.request.get(f'{s.url}/api/admin/feedbacks', timeout=e2e.ANSWER_TIMEOUT)
    assert denied.status == 403 and denied.json()['code'] == 'admin_required'


def _wire(stack, request_id):
    return sorted(
        (
            row
            for path in (stack.model_wire, stack.chat_wire)
            for row in e2e.request_log(path)
            if row['request_id'] == request_id
        ),
        key=lambda row: row['ordinal'],
    )


def _assert_recorded_wire(db, stack, rid, purposes):
    with connect(db) as conn:
        calls = conn.execute(
            'SELECT purpose, method, path, model_id, model_revision, request_body, status_code, '
            'response_body, response_streaming, first_byte_at, usage, model_calls.redaction_count, '
            'send_status FROM model_calls '
            'JOIN consults ON consults.id = model_calls.consult_id WHERE request_id = ? ORDER BY seq',
            (rid,),
        ).fetchall()
        chunks = conn.execute(
            'SELECT body FROM model_call_chunks JOIN consults ON consults.id = consult_id '
            'WHERE request_id = ? ORDER BY call_seq, seq',
            (rid,),
        ).fetchall()
    assert [row[0] for row in calls] == purposes
    wire = _wire(stack, rid)
    for call, actual in zip(calls, wire, strict=True):
        (
            purpose,
            method,
            path,
            model_id,
            revision,
            sent,
            status,
            body,
            streaming,
            first,
            usage,
            redactions,
            send_status,
        ) = call
        assert (method, path, status) == (actual['method'], actual['path'], actual['status'])
        assert sent == base64.b64decode(actual['request'])
        assert send_status == 'sent'
        assert model_id == json.loads(sent)['model'] and revision == e2e.STUB_MODEL_REVISION
        assert first and redactions == 0
        if purpose == 'writer':
            assert streaming and b''.join(piece for (piece,) in chunks) == b''.join(
                base64.b64decode(piece) for piece in actual['chunks']
            )
            assert body is None and json.loads(usage)['total_tokens'] == 17
        else:
            assert not streaming and body == b''.join(base64.b64decode(piece) for piece in actual['chunks'])
    return [row['request'] for row in wire]


def test_consult_trace_wire_and_admin_browser(admin_stack, page, tmp_path):
    stack, db = admin_stack
    off_dir = tmp_path / 'disabled'
    off_dir.mkdir()
    off_log = off_dir / 'requests.jsonl'
    port = free_port()
    off_env = stack.web_env | {'LAWBOT_CONSULT_TRACE': '0', 'LAWBOT_REQUEST_LOG': str(off_log)}
    off = e2e.start_web(off_dir, port, **off_env)
    try:
        e2e.login(page, f'http://127.0.0.1:{port}')
        expect(e2e.textbox(page)).to_be_visible()
        expect(page.get_by_test_id('consult-retention')).to_have_count(0)
        e2e.ask(page, QUESTION)
        e2e.wait_done(page)
        expect(e2e.stats(page)).to_be_visible()
        original = e2e.answer_body(page).inner_text()
        original_cards = e2e.sources(page).get_by_test_id('source-card').all_inner_texts()
        baseline = _wire(stack, e2e.wait_request_log(page, off_log, 0)['request_id'])
        with connect(db) as conn:
            assert conn.execute('SELECT count(*) FROM consults').fetchone() == (0,)
    finally:
        off.stop()
        page.context.clear_cookies()

    start = len(e2e.request_log(stack.request_log))
    e2e.login(page, stack.url)
    expect(page.get_by_test_id('consult-retention')).to_have_text(
        '상담 내용은 디버깅·평가·학습을 위해 기한 없이 보관되며, 관리자에게 삭제를 요청할 수 있습니다.'
    )
    e2e.ask(page, QUESTION)
    e2e.wait_done(page)
    expect(e2e.stats(page)).to_be_visible()
    assert e2e.answer_body(page).inner_text() == original
    assert e2e.sources(page).get_by_test_id('source-card').all_inner_texts() == original_cards
    row = e2e.wait_request_log(page, stack.request_log, start)
    assert row['trace_status'] == 'saved'
    wire = _assert_recorded_wire(db, stack, row['request_id'], ['embedding', 'rerank', 'embedding', 'rerank', 'writer'])
    assert wire == [item['request'] for item in baseline]
    model_startup_paths = {x['path'] for x in e2e.request_log(stack.model_wire) if x['request_id'] is None}
    assert {'/health', '/v1/models', '/v1/embeddings'} <= model_startup_paths
    assert any(x['path'] == '/v1/models' and x['request_id'] is None for x in e2e.request_log(stack.chat_wire))
    assert QUESTION not in stack.request_log.read_text() and FAKE_ANSWER not in stack.request_log.read_text()

    page.get_by_role('button', name='로그아웃').click()
    e2e.login(page, stack.url, user=ADMIN_USER, pw=ADMIN_PW)
    page.get_by_role('radio', name='상담 기록').click()
    page.get_by_role('list', name='상담 목록').get_by_role('button', name=QUESTION).click()
    detail = page.get_by_role('region', name='상담 상세')
    expect(detail.get_by_test_id('consult-shown')).to_contain_text(FAKE_ANSWER)
    step_list = detail.get_by_role('list', name='처리 단계')
    assert step_list.get_by_test_id('consult-step').all_inner_texts() == [
        'search',
        'rerank',
        'decree_search',
        'decree_rerank',
        'definitions',
        'writer',
        'citation',
        'display',
    ]
    writer = detail.get_by_test_id('consult-call').last
    writer.get_by_text(f'writer · {stack.chat_model} · 200', exact=True).click()
    sent = writer.get_by_role('region', name='송신 본문')
    request_body = sent.get_by_test_id('consult-request-body')
    expect(request_body).to_contain_text('"messages"')
    sent_bytes = base64.b64decode(wire[-1])
    pages = [request_body.text_content() or '']
    loaded = len(pages[0].encode())
    while loaded < len(sent_bytes):
        sent.get_by_role('button', name='다음 본문').click()
        expect(request_body).not_to_have_text(pages[-1])
        text = request_body.text_content()
        assert text
        pages.append(text)
        loaded += len(text.encode())
    request_text = ''.join(pages)
    assert request_text.encode() == sent_bytes
    assert QUESTION in request_text
    response_chunks = writer.get_by_role('region', name='수신 원문').get_by_test_id('consult-response-chunk')
    expect(response_chunks.first).to_be_visible()
    assert 'private synthetic reasoning' in ''.join(response_chunks.all_text_contents())

    delete = page.get_by_role('region', name='상담 삭제')
    delete.get_by_label('사용자 해시').fill(hashlib.sha256(e2e.USER.encode()).hexdigest()[:16])
    delete.get_by_role('button', name='삭제 건수 미리 보기').click()
    expect(delete.get_by_role('status')).to_have_text('삭제 대상: 상담 1건 · 단계 8건 · 호출 5건')
    delete.get_by_role('button', name='확인 후 삭제').click()
    expect(delete.get_by_test_id('consult-delete-result')).to_have_text(
        '상담 1건 · 단계 8건 · 호출 5건을 삭제했습니다.'
    )
    expect(page.get_by_role('status').get_by_text('상담 기록이 없습니다.')).to_be_visible()
    with connect(db) as conn:
        assert conn.execute('SELECT consult_count, step_count, call_count FROM consult_trace_deletions').fetchall() == [
            (1, 8, 5),
        ]


def test_consult_trace_router_http_purposes(admin_stack, page):
    stack, db = admin_stack
    stack.ensure('calculation')
    e2e.login(page, stack.url)
    e2e.ask(page, '월급 300만원으로 3년 일했습니다. 퇴직금은 얼마인가요?')
    e2e.wait_done(page)
    row = e2e.wait_request_log(page, stack.request_log, 0)
    _assert_recorded_wire(
        db,
        stack,
        row['request_id'],
        ['router_select', 'router_label', 'embedding', 'rerank', 'embedding', 'rerank', 'writer'],
    )


# ---- 정상 질문 ----
def test_question_streams_answer(stack, ui):
    stack.ensure('stream')  # 토큰 사이 지연: 답이 조각으로 늘어나는지 보이게
    e2e.ask(ui, QUESTION)
    seen = []
    body = e2e.answer_body(ui)
    deadline = time.monotonic() + e2e.ANSWER_TIMEOUT / 1000
    while not ui.get_by_test_id('lawbot-stats').get_by_text('완료').count():
        assert time.monotonic() < deadline, f'답변이 끝나지 않음 (본 화면: {seen})'
        text = body.inner_text() if body.count() else ''
        if text and (not seen or seen[-1] != text):
            seen.append(text)
        ui.wait_for_timeout(50)
    assert FAKE_ANSWER in body.inner_text()
    assert any(FAKE_ANSWER not in t and t.strip() for t in seen)  # 끝나기 전 중간 상태가 화면에 보였음


def test_unverified_draft_is_replaced_at_completion(stack, ui):
    stack.ensure('stream')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, '합성 근거 오류 사례')
    answer = e2e.answer(ui)
    expect(answer.get_by_text('답변 초안', exact=True)).to_be_visible()
    e2e.wait_done(ui)
    expect(answer).to_contain_text(FAKE_ANSWER)
    expect(answer).to_contain_text('사회복지사업법 제2조제1호⚠확인되지 않은 인용')
    expect(answer.get_by_role('button', name='답변 복사')).to_be_visible()
    sources = e2e.sources(ui)
    expect(sources.get_by_text('✅ 답변에서 인용한 조문')).to_be_visible()
    expect(sources.get_by_test_id('source-card').filter(has_text='사회복지사업법')).to_have_count(0)
    assert e2e.wait_request_log(ui, stack.request_log, before)['answer_verdict'] == 'shown'


def test_unverified_conclusion_is_marked_only_at_completion(stack, ui):
    stack.ensure('stream')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, '합성 결론 오류 사례')
    answer = e2e.answer(ui)
    expect(answer.get_by_text('답변 초안', exact=True)).to_be_visible()
    e2e.wait_done(ui)
    expect(answer).to_contain_text('소득세법 제99999조 제2항⚠확인되지 않은 인용에 따릅니다.')
    expect(answer).to_contain_text('근거:')
    expect(e2e.sources(ui).get_by_test_id('source-card').filter(has_text='제99999조')).to_have_count(0)
    row = e2e.wait_request_log(ui, stack.request_log, before)
    assert row['answer_verdict'] == 'shown' and row['concl_cite_nonexistent'] == 1


def test_sources_and_stats_shown(stack, ui):
    for label in ('답변 모델', '답변 형식'):
        expect(ui.get_by_role('radiogroup', name=label)).to_have_count(0)
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    st = e2e.stats(ui).inner_text()
    for part in ('Lawbot Pro', '검색', '리랭킹', '첫 토큰', '완료'):
        assert part in st, (part, st)
    src = e2e.sources(ui)
    assert src.get_by_text('✅ 답변에서 인용한 조문').count() == 1  # 가짜 답은 첫 번째 조문을 인용
    assert src.get_by_text('검색만 된 조문').count() == 1
    cards = [c.inner_text() for c in src.get_by_test_id('source-card').all()]
    law_cards = e2e.law_cards(ui)
    assert len(law_cards) == 3, cards  # 법률 조문 3개 (하위법령 카드는 〔시행령〕·〔시행규칙〕 표시)
    assert len(cards) > 3, cards  # 하위법령 카드도 있는 상태에서 법률 카드만 셈 (e2e_live와 같은 헬퍼)
    assert all('리랭커' in c for c in law_cards)


@pytest.fixture
def db_corpus_stack(tmp_path, page, request):
    db = tmp_path / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(db)])
    release = 'e2e-v5-release'
    law, _ = article_paths('v5')
    assert migrate(db, 'corpus', '--dir', law.parent, '--profile', 'v5', '--release-id', release, '--activate') == 0
    stack = e2e.Stack(
        tmp_path,
        read_timeout=READ_TIMEOUT_S,
        LAWBOT_CORPUS='db',
        LAWBOT_DB=str(db),
        LAWBOT_V5_ARTICLES=str(tmp_path / 'absent-law.csv'),
        LAWBOT_V5_DECREE_ARTICLES=str(tmp_path / 'absent-combined.csv'),
    )
    try:
        yield stack, release
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
    finally:
        stack.stop()


def test_db_corpus_question_shows_three_law_cards(db_corpus_stack, page):
    stack, release = db_corpus_stack
    e2e.login(page, stack.url)
    e2e.ask(page, QUESTION)
    e2e.wait_done(page)
    expect(e2e.answer(page)).to_contain_text(FAKE_ANSWER)
    assert len(e2e.law_cards(page)) == 3
    assert e2e.wait_request_log(page, stack.request_log, 0)['corpus_release_id'] == release


@pytest.mark.parametrize('question', V5_EXAMPLES)
def test_example_click(stack, ui, question):
    ui.get_by_role('button', name=question, exact=True).click()
    e2e.wait_done(ui)
    expect(ui.get_by_test_id('question').last).to_have_text(question)
    assert FAKE_ANSWER in e2e.answer(ui).inner_text()


def test_request_log_api_mode(stack, ui):
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    row = e2e.wait_request_log(ui, stack.request_log, before)
    assert row['mode'] == 'api' and row['status'] == 'ok' and row['error_type'] is None
    assert QUESTION not in stack.request_log.read_text(encoding='utf-8')  # 질문 원문은 남기지 않음


@pytest.mark.parametrize('action', ['중지', '로그아웃'])
def test_leaving_a_running_answer_cancels_generation(stack, ui, action, feedback_db):
    stack.ensure('long')  # 결론 뒤 설명이 길다: 간결형은 그동안 화면 글이 바뀌지 않음
    before = len(e2e.request_log(stack.request_log))
    ended_before = sum(line.startswith('e2e chat end') for line in stack.chat.output().splitlines())
    e2e.ask(ui, QUESTION)
    e2e.answer(ui).get_by_text('가짜 모델').wait_for(timeout=e2e.ANSWER_TIMEOUT)
    ui.get_by_role('button', name=action).click()
    if action == '중지':
        e2e.answer(ui).get_by_text('답변을 중지했습니다').wait_for()
        expect(e2e.answer(ui).get_by_text('답변 초안', exact=True)).to_be_visible()
        expect(e2e.answer(ui).get_by_role('button', name='답변 복사')).to_have_count(0)
    else:
        expect(ui.get_by_role('button', name='로그인')).to_be_visible()
        assert ui.request.get(f'{stack.url}/api/me', timeout=e2e.ANSWER_TIMEOUT).status == 401
    row = e2e.wait_request_log(ui, stack.request_log, before)
    assert row['status'] == 'cancelled' and row['trace_status'] == 'saved'
    with connect(feedback_db) as conn:
        stored = conn.execute(
            'SELECT consults.status, answer_shown, delivery_status, consults.id, seq, model_calls.error_type FROM consults '
            'JOIN model_calls ON consult_id = consults.id WHERE request_id = ? AND purpose = ?',
            (row['request_id'], 'writer'),
        ).fetchone()
        assert stored[:3] == ('cancelled', None, 'not_prepared') and stored[-1] is None
        chunks = b''.join(
            piece
            for (piece,) in conn.execute(
                'SELECT body FROM model_call_chunks WHERE consult_id = ? AND call_seq = ? ORDER BY seq',
                stored[3:5],
            )
        )
    assert b'data: [DONE]' not in chunks and '가짜 모델'.encode() in chunks
    deadline = time.monotonic() + 15
    while (
        len(ends := [line for line in stack.chat.output().splitlines() if line.startswith('e2e chat end')])
        <= ended_before
    ):
        assert time.monotonic() < deadline, f'생성 서버의 응답이 끝나지 않음\n{stack.chat.output()}'
        ui.wait_for_timeout(100)
    sent, total = map(int, ends[-1].split()[-1].split('/'))
    assert sent < total, ends


# ---- 휴대폰 ----
def test_phone_sources_sheet(stack, browser, request):
    stack.ensure('normal')
    ctx = browser.new_context(**PHONE)
    page = ctx.new_page()
    try:
        e2e.login(page, stack.url)
        e2e.ask(page, QUESTION)
        e2e.wait_done(page)
        assert not page.get_by_role('complementary', name='근거 조문').is_visible()  # 옆 패널은 숨김
        page.get_by_role('button', name='근거 조문').last.click()
        sheet = page.get_by_role('dialog', name='근거 조문')
        sheet.get_by_text('✅ 답변에서 인용한 조문').wait_for()
        sheet.get_by_role('button', name='닫기').click()
        sheet.wait_for(state='detached')
    finally:
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
        ctx.close()


# ---- 오류 안내 ----
@pytest.mark.parametrize('question', [QUESTION, '2026년 총급여 5천만 원, 의료비 300만 원이면 세액공제액은 얼마인가요?'])
def test_error_generation_busy(stack, ui, question, feedback_db):
    stack.ensure('busy')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, question)
    alert(ui, '모델 서버가 바쁩니다 (대기 중인 질문이 많음). 잠시 뒤 다시 질문하세요.')
    row = e2e.wait_request_log(ui, stack.request_log, before)
    assert (row['status'], row['error_type']) == ('error', 'model_server_busy')
    assert row['trace_status'] == 'saved'
    with connect(feedback_db) as conn:
        stored = conn.execute(
            'SELECT consults.status, consults.error_type, status_code, response_body, response_streaming '
            'FROM consults JOIN model_calls ON consult_id = consults.id WHERE request_id = ? ORDER BY seq DESC LIMIT 1',
            (row['request_id'],),
        ).fetchone()
    assert stored[:3] == ('error', 'model_server_busy', 503) and stored[-1] == 0
    assert json.loads(stored[3])['error']['type'] == 'server_busy'
    assert stack.chat.output().splitlines().count('e2e chat busy') == 1
    assert FAKE_ANSWER not in e2e.answer(ui).inner_text()
    expect(ui.get_by_role('button', name='좋아요', exact=True)).to_have_count(0)


def test_error_timeout(stack, ui):
    stack.ensure('slow')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    alert(ui, '모델 서버 응답이 없습니다')
    assert e2e.wait_request_log(ui, stack.request_log, before)['error_type'] == 'model_server_timeout'


@pytest.mark.parametrize('mode', ['chat_error', 'truncated'])
def test_error_generation(stack, ui, mode):
    stack.ensure(mode)
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    alert(ui, '답변을 생성하지 못했습니다')
    row = e2e.wait_request_log(ui, stack.request_log, before)
    assert (row['status'], row['error_type']) == ('error', 'generation')
    assert FAKE_ANSWER not in e2e.answer(ui).inner_text()
    expect(ui.get_by_role('button', name='좋아요', exact=True)).to_have_count(0)


def test_error_server_down(stack, ui):
    stack.stop_chat_server()
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    alert(ui, '모델 서버가 준비되지 않았습니다')
    assert e2e.wait_request_log(ui, stack.request_log, before)['error_type'] == 'model_server'


def test_error_generation_authentication(stack, ui):
    stack.ensure('auth_error')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    alert(ui, '모델 서버가 준비되지 않았습니다')
    assert e2e.wait_request_log(ui, stack.request_log, before)['error_type'] == 'model_server'


@pytest.fixture
def v5_ui(tmp_path, page, request):
    stack = e2e.Stack(tmp_path, read_timeout=READ_TIMEOUT_S, example='.env.dev.example')
    try:
        e2e.login(page, stack.url)
        yield page
    finally:
        try:
            e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
        finally:
            stack.stop()


def test_v5_answers_in_the_single_concise_format(v5_ui):
    page = v5_ui
    expect(page.get_by_role('radiogroup', name='답변 형식')).to_have_count(0)
    e2e.ask(page, '연말정산에서 어떤 소득공제를 받을 수 있나요?')
    e2e.wait_done(page)
    answer = e2e.answer(page)
    expect(answer).to_contain_text('근로소득 세금, 4대보험')
    expect(answer).not_to_contain_text('가짜 모델의 전체 설명')
    assert len(e2e.law_cards(page)) == 3


@pytest.fixture
def definition_stack(tmp_path, page, request):
    path = tmp_path / 'law.csv'
    source, target = '근로자퇴직급여 보장법', '근로기준법'
    rows = [
        (source, '제2조', '정의', '4. "평균임금"이란 「근로기준법」 제2조제1항제6호에 따른 임금이다.'),
        (target, '제2조', '정의', '6. "평균임금"이란 지급된 임금의 평균이다.'),
        (source, '제8조', '퇴직금', '평균임금에 따른다. ' + '퇴직금 적용 기준을 정한다. ' * 20),
        (source, '제9조', '지급', '퇴직금 지급 기준을 정한다. ' * 20),
        (source, '제10조', '시효', '퇴직금 시효 기준을 정한다. ' * 20),
        *((source, f'제{i}조', '기타', '별도 규정이다.') for i in range(11, 66)),
    ]
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(('law', 'article_id', 'title', 'text', 'deleted'))
        writer.writerows((*row, 0) for row in rows)
    path.with_name('manifest.json').write_text(
        json.dumps(
            {
                'articles_file': path.name,
                'articles_sha256': manifest.sha256(path),
                'articles_total': len(rows),
                'dataset_version': 'synthetic',
                'reference_date': '2026-01-01',
            }
        ),
        encoding='utf-8',
    )
    stack = e2e.Stack(
        tmp_path,
        read_timeout=READ_TIMEOUT_S,
        LAWBOT_PROFILE='v5',
        LAWBOT_V5_ARTICLES=str(path),
        LAWBOT_RERANKER_ENABLED='1',
        LAWBOT_RERANKER_CANDIDATES='3',
        LAWBOT_DECREE_ENABLED='0',
    )
    try:
        yield stack
    finally:
        try:
            e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
        finally:
            stack.stop()


def test_definition_citation_has_no_source_card(definition_stack, page):
    e2e.login(page, definition_stack.url)
    e2e.ask(page, '합성 정의 인용 사례: 퇴직금은 어떻게 정하나요?')
    e2e.wait_done(page)
    expect(e2e.answer(page)).to_contain_text('근거: 근로기준법 제2조(정의)')
    cards = e2e.sources(page).get_by_test_id('source-card')
    expect(cards).to_have_count(3)
    expect(cards.filter(has_text='근로자퇴직급여 보장법 제8조')).to_have_count(1)
    expect(cards.filter(has_text='근로기준법 제2조')).to_have_count(0)
