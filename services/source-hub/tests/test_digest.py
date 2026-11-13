import base64
import json
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from lawbot_common.erp_daily import replace_file, seal
from source_hub import digest, gmail
from source_hub.collector import file_lock
from source_hub.store import KST

NOW = datetime(2026, 11, 13, 8, tzinfo=KST)
URL = 'https://www.moel.go.kr/notice/123'
PAGE = 'https://calendar.example/#erp'
NEWS = {
    'id': 'news:one',
    'kind': 'news',
    'title': '<공식 새 글>',
    'url': URL,
    'publishedOn': '2026-11-12',
    'change': 'new',
}


def published(path, changes=None, *, cutoff=NOW):
    value = seal(
        {
            'schemaVersion': 1,
            'dayKst': '2026-11-13',
            'cutoffAt': cutoff,
            'runId': 'daily-test',
            'configVersion': 1,
            'configHash': '0' * 64,
            'status': 'partial',
            'sources': [{'sourceId': 'bad', 'outcome': 'blocked', 'stale': True}],
            'feeds': {'erp': {'items': []}, 'news': {'moel': {'items': [{'title': '과거 게시물'}]}}},
            'changes': [NEWS] if changes is None else changes,
        }
    )
    replace_file(path, value)
    return value


def test_render_utf8_hash_public_only_and_metadata_fallback(tmp_path):
    snapshot = published(tmp_path / 'public.json')
    mail = digest.render(snapshot, PAGE, limit=10)
    assert '<공식 새 글>' in mail.text and '&lt;공식 새 글&gt;' in mail.html
    assert '내용 미확인' in mail.text and '2026-11-12' in mail.text and URL in mail.text
    assert '실패 1' in mail.text and '과거 게시물' not in mail.text
    parsed = BytesParser(policy=policy.default).parsebytes(
        gmail.message(mail, 'sender@example.test', 'first@example.test', snapshot['snapshotHash'])
    )
    assert parsed['X-ERP-Snapshot'] == snapshot['snapshotHash']
    assert parsed.get_body(preferencelist=('plain',)).get_content().splitlines() == mail.text.splitlines()
    assert parsed.get_body(preferencelist=('html',)).get_content().splitlines() == mail.html.splitlines()


def test_gmail_official_oauth_and_mime_contract(tmp_path):
    snapshot = published(tmp_path / 'public.json')
    mime = gmail.message(
        digest.render(snapshot, PAGE, limit=10), 'sender@example.test', 'first@example.test', snapshot['snapshotHash']
    )
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == 'oauth2.googleapis.com':
            assert b'grant_type=refresh_token' in request.content
            return httpx.Response(200, json={'access_token': 'short-lived'})
        assert request.url.path == '/gmail/v1/users/me/messages/send'
        assert request.headers['Authorization'] == 'Bearer short-lived'
        assert base64.urlsafe_b64decode(json.loads(request.content)['raw']) == mime
        return httpx.Response(200, json={'id': 'provider-acceptance'})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        sender = gmail.Gmail(http, gmail.Credentials('client', 'private', 'refresh'))
        assert sender.send(mime) == gmail.Result('accepted', 'provider-acceptance')
    assert calls == ['oauth2.googleapis.com', 'gmail.googleapis.com']


def test_outbox_retries_definite_failure_and_keeps_uncertain_delivery(tmp_path):
    path = tmp_path / 'public.json'
    snapshot = published(path)
    settings = digest.Settings(
        PAGE, 'sender@example.test', {'first': 'first@example.test', 'second': 'second@example.test'}
    )
    calls = []

    def send(raw):
        recipient = BytesParser(policy=policy.default).parsebytes(raw)['To']
        calls.append(str(recipient))
        if recipient == 'second@example.test':
            return gmail.Result('unknown')
        return gmail.Result('retryable' if calls.count('first@example.test') == 1 else 'accepted')

    args = (path, tmp_path / 'private.db', tmp_path / 'publish.lock', settings)
    first = digest.deliver(*args, now=NOW, send=send)
    assert first['snapshot_hash'] == snapshot['snapshotHash']
    assert first['deliveries'] == {'first': 'retryable', 'second': 'unknown'}
    second = digest.deliver(*args, now=NOW + timedelta(minutes=10), send=send)
    assert second['deliveries'] == {'first': 'accepted', 'second': 'unknown'}
    assert digest.deliver(*args, now=NOW + timedelta(minutes=20), send=send) == second
    assert calls == ['first@example.test', 'second@example.test', 'first@example.test']


def test_new_hash_halts_pending_old_message_without_resending_accepted(tmp_path):
    path = tmp_path / 'public.json'
    published(path)
    settings = digest.Settings(
        PAGE, 'sender@example.test', {'first': 'first@example.test', 'second': 'second@example.test'}
    )
    calls = []

    def send(raw):
        calls.append(raw)
        return gmail.Result('accepted' if len(calls) == 1 else 'retryable')

    args = (path, tmp_path / 'private.db', tmp_path / 'publish.lock', settings)
    digest.deliver(*args, now=NOW, send=send)
    updated = published(path, [NEWS | {'title': '정정된 글'}], cutoff=NOW + timedelta(minutes=3))
    result = digest.deliver(*args, now=NOW + timedelta(minutes=10), send=send)
    assert result['snapshot_hash'] == updated['snapshotHash']
    assert result['deliveries'] == {'first': 'already_sent', 'second': 'awaiting_review'}
    assert len(calls) == 2


def test_morning_only_reuses_lines_confirmed_for_the_entire_current_revision(tmp_path):
    law = NEWS | {
        'id': 'law:one',
        'kind': 'law',
        'eventId': 'monitor-id',
        'revision': 'rev',
        'expected': 1,
        'publicSummaries': ['공개 확인된 변경'],
        'dates': ['2026-11-20'],
    }
    entry = {
        'events': [{'eventId': 'monitor-id', 'revision': 'rev'}],
        'text': '- 공개 확인된 변경\n- 승인되지 않은 적용일 2027-01-01',
    }
    snapshot = published(tmp_path / 'public.json', [law])
    assert digest.verified_morning(snapshot, [entry]) == {'law:one': '공개 확인된 변경'}
    assert digest.verified_morning(snapshot, [entry | {'events': [*entry['events'], {'eventId': 'form'}]}]) == {}
    assert digest.verified_morning(snapshot, [entry | {'events': [{'eventId': 'monitor-id', 'revision': 'old'}]}]) == {}
    assert digest.verified_morning(published(tmp_path / 'public.json', [law | {'expected': 2}]), [entry]) == {}


def test_model_only_receives_approved_facts_and_selects_exact_spans(tmp_path):
    from source_hub import digest_model

    erp = NEWS | {'id': 'erp:one', 'kind': 'erp', 'excerpt': '가상 변경입니다. 새 안내서가 공개되었습니다.'}
    snapshot = published(tmp_path / 'public.json', [NEWS, erp])
    posted = []

    def handle(request):
        data = json.loads(request.content)
        posted.append(data)
        assert 'news:one' not in data['messages'][1]['content']
        assert '새 안내서가 공개되었습니다.' in data['messages'][1]['content']
        content = json.dumps({'erp:one': '새 안내서가 공개되었습니다.', 'news:one': '추정 내용'})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        model = digest_model.Model(http, 'http://127.0.0.1', 'fixture')
        assert model.summarize(snapshot) == {'erp:one': '새 안내서가 공개되었습니다.'}
        assert model.summarize(published(tmp_path / 'public.json')) == {}
    assert len(posted) == 1 and posted[0]['max_tokens'] == 800


def test_interrupted_send_is_unknown_and_shared_lock_prevents_overlap(tmp_path):
    path = tmp_path / 'public.json'
    published(path)
    settings = digest.Settings(PAGE, 'sender@example.test', {'first': 'first@example.test'})
    args = (path, tmp_path / 'private.db', tmp_path / 'publish.lock', settings)

    def stop(_):
        raise RuntimeError('crashed after submitting to provider')

    with file_lock(args[2]):
        assert digest.deliver(*args, now=NOW, send=stop) == {'status': 'busy'}
    with pytest.raises(RuntimeError, match='crashed'):
        digest.deliver(*args, now=NOW, send=stop)
    assert digest.deliver(*args, now=NOW, send=stop)['deliveries'] == {'first': 'unknown'}


@pytest.mark.parametrize(('code', 'expected'), [(429, 'retryable'), (403, 'failed'), (503, 'unknown')])
def test_gmail_classifies_rejection_and_ambiguous_provider_error(code, expected):
    def handle(request):
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token': 'short-lived'})
        return httpx.Response(code, json={'error': {'errors': [{'reason': 'denied'}]}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert gmail.Gmail(http, gmail.Credentials('client', 'private', 'refresh')).send(b'mime').status == expected


def test_same_snapshot_retry_preserves_original_bytes_and_model_is_called_once(tmp_path):
    path = tmp_path / 'public.json'
    published(path, [NEWS | {'id': 'erp:one', 'kind': 'erp', 'excerpt': '검토된 원문'}])
    settings = digest.Settings(PAGE, 'sender@example.test', {'first': 'first@example.test'})
    args = (path, tmp_path / 'private.db', tmp_path / 'publish.lock', settings)
    messages, model_calls = [], []

    def send(raw):
        messages.append(raw)
        return gmail.Result('retryable' if len(messages) == 1 else 'accepted')

    def enrich(snapshot):
        model_calls.append(snapshot['snapshotHash'])
        return {'erp:one': '검토된 원문'}

    digest.deliver(*args, now=NOW, send=send, enrich=enrich)
    digest.deliver(*args, now=NOW + timedelta(minutes=10), send=send, enrich=enrich)
    assert len(model_calls) == 1 and len(messages) == 2 and messages[0] == messages[1]


def test_preview_and_disabled_command_do_not_use_network_or_create_outbox(tmp_path, capsys):
    from source_hub.cli import main

    path = tmp_path / 'public.json'
    snapshot = published(path)
    outbox = tmp_path / 'outbox.db'
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'erp_page': PAGE}))
    settings.chmod(0o600)
    args = [
        '--database',
        str(tmp_path / 'collector.db'),
        'email-digest',
        '--snapshot',
        str(path),
        '--outbox',
        str(outbox),
        '--settings',
        str(settings),
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'disabled'}
    assert main([*args, '--preview']) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['status'] == 'preview' and preview['snapshot_hash'] == snapshot['snapshotHash']
    assert '내용 미확인' in preview['text'] and not outbox.exists()


def test_malformed_addresses_are_reported_as_configuration_errors():
    with pytest.raises(ValueError, match='Invalid or duplicate ERP email address'):
        digest.Settings(PAGE, 'sender@example.test', {'first': 'user@@example.test'})
