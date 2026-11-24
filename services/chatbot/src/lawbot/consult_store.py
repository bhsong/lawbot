"""Redact a completed trace and persist it atomically."""

import base64
import hashlib
import json
import re
import time
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lawbot import feedback
from lawbot.consult_trace import POLICY, Call, Consultation, stamp
from lawbot.db.connection import connect, transaction

_COOKIE = rb'(?<![A-Za-z0-9_+/-])[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048](?![A-Za-z0-9_+/=-])'
_ENCODINGS = {'gzip', 'deflate', 'identity'}
if find_spec('brotli') or find_spec('brotlicffi'):
    _ENCODINGS.add('br')
if find_spec('zstandard'):
    _ENCODINGS.add('zstd')


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _marker(reason: str) -> bytes:
    return f'[REDACTED:{reason}]'.encode()


class Redactor:
    """Match bytes before encoding for storage, including across transport chunks."""

    def __init__(
        self, model_token: str, llm_key: str, model_url: str, llm_url: str, port: int | None, cookie: str | None = None
    ):
        values = (
            (model_token, 'model_token'),
            (llm_key, 'generation_api_key'),
            (urlsplit(model_url).netloc, 'model_server_address'),
            (urlsplit(llm_url).netloc, 'generation_server_address'),
            (cookie or '', 'session_cookie'),
        )
        self.port = port
        patterns: dict[bytes, str] = {}
        for value, reason in sorted(values, key=lambda item: len(item[0]), reverse=True):
            if not value:
                continue
            for form in (value, _json(value)[1:-1], json.dumps(value)[1:-1]):
                literal = re.escape(form.encode())
                pattern = b'(?i:' + literal + b')' if reason.endswith('_address') else literal
                patterns.setdefault(pattern, reason)
        if port is not None:
            patterns[rb'(?<![0-9])' + str(port).encode() + rb'(?![0-9])'] = 'web_port'
        patterns[_COOKIE] = 'session_cookie'
        self.reasons = list(patterns.values())
        self.pattern = re.compile(b'|'.join(b'(' + term + b')' for term in patterns))

    def _replacement(self, match: re.Match[bytes], counts: Counter[str]) -> bytes:
        index = match.lastindex
        assert index is not None
        reason = self.reasons[index - 1]
        counts[reason] += 1
        return _marker(reason)

    def body(self, value: bytes, counts: Counter[str]) -> bytes:
        return self.pattern.sub(lambda match: self._replacement(match, counts), value)

    def data(self, value, counts: Counter[str]):
        if isinstance(value, str):
            return self.body(value.encode(), counts).decode()
        if type(value) is int and value == self.port:
            counts['web_port'] += 1
            return '[REDACTED:web_port]'
        if isinstance(value, (tuple, list)):
            return [self.data(item, counts) for item in value]
        if isinstance(value, dict):
            return {self.data(key, counts): self.data(item, counts) for key, item in value.items()}
        return value

    def chunks(self, joined: bytes, chunks: list[bytes], counts: Counter[str]) -> list[bytes]:
        # Keep each receive boundary. A cross-boundary replacement belongs to the chunk where it starts.
        matches = self.pattern.finditer(joined)
        first = next(matches, None)
        if first is None:
            return chunks
        starts = [0]
        for chunk in chunks:
            starts.append(starts[-1] + len(chunk))
        output: list[bytes] = []
        match = first
        consumed = 0
        for index, chunk in enumerate(chunks):
            at = max(0, consumed - starts[index])
            parts = bytearray()
            while match is not None and bisect_right(starts, match.start()) - 1 == index:
                parts += chunk[at : match.start() - starts[index]]
                parts += self._replacement(match, counts)
                consumed = match.end()
                at = consumed - starts[index]
                match = next(matches, None)
            parts += chunk[at:]
            output.append(bytes(parts))
        return output


class _Replay(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.index = 0

    def __iter__(self) -> Iterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            self.index = index
            yield chunk


def _decoded(encoding: str, chunks: list[bytes]) -> list[bytes]:
    replay = _Replay(chunks)
    decoded = [b''] * len(chunks)
    response = httpx.Response(200, headers={'content-encoding': encoding}, stream=replay)
    for chunk in response.iter_bytes():
        decoded[replay.index] += chunk
    return decoded


def _usage(body: bytes, streaming: bool) -> dict[str, int] | None:
    documents = (
        (line[5:].lstrip() for line in body.splitlines() if line.startswith(b'data:') and b'"usage"' in line)
        if streaming
        else (body,)
    )
    result = None
    for document in documents:
        try:
            data = json.loads(document)
        except ValueError, UnicodeError:
            continue
        usage = data.get('usage') if isinstance(data, dict) else None
        if isinstance(usage, dict):
            result = {
                key: usage[key]
                for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                if type(usage.get(key)) is int
            }
    return result


def _response(call: Call, redactor: Redactor, counts: Counter[str]):
    raw = [body for _, body in call.chunks]
    decoded = False
    usage = None
    encoding = (call.encoding or 'identity').strip().lower()
    if encoding != 'identity' and raw:
        # Raw compressed bytes are retained only if their decoded contents are safe to persist.
        try:
            if not set(encoding.replace(' ', '').split(',')) <= _ENCODINGS:
                raise httpx.DecodingError('Unsupported content encoding')
            plain = _decoded(encoding, raw)
        except httpx.DecodingError:
            counts['unreadable_encoding'] += 1
            raw = [_marker('unreadable_encoding'), *([b''] * (len(raw) - 1))]
            decoded = True
        else:
            joined = b''.join(plain)
            usage = _usage(joined, call.streaming)
            if redactor.pattern.search(joined):
                raw = redactor.chunks(joined, plain, counts)
                decoded = True
    else:
        joined = b''.join(raw)
        usage = _usage(joined, call.streaming)
        raw = redactor.chunks(joined, raw, counts) if call.streaming else [redactor.body(joined, counts)]
    if call.status_code is None:
        return None, None, decoded, usage
    if not call.streaming:
        return b''.join(raw), None, decoded, usage
    chunks = [
        [offset, base64.b64encode(body).decode('ascii')] for (offset, _), body in zip(call.chunks, raw, strict=True)
    ]
    return None, _json(chunks), decoded, usage


def save(db: Path, trace: Consultation, redactor: Redactor) -> None:
    total: Counter[str] = Counter()
    question = redactor.data(trace.question, total)
    answer_shown = redactor.data(trace.answer_shown, total)
    app_commit = feedback.commit()
    end = trace.end if trace.end is not None else time.monotonic_ns()
    system = trace.timing.get('_system')
    steps = []
    for seq, step in enumerate(trace.stages, 1):
        counts: Counter[str] = Counter()
        result = redactor.data(step.result, counts)
        steps.append(
            (
                trace.id,
                seq,
                step.stage,
                trace.timestamp(step.start),
                trace.timestamp(step.end or end),
                _json(result),
                counts.total(),
                _json(counts),
            )
        )
        total.update(counts)
    calls = []
    for seq, call in enumerate(trace.calls, 1):
        counts = Counter()
        path = redactor.data(call.path, counts)
        model = redactor.data(call.model_id, counts)
        revision = redactor.data(call.model_revision, counts)
        request = redactor.body(call.request_body, counts)
        response, chunks, decoded, usage = _response(call, redactor, counts)
        first_byte = trace.timestamp(call.start + call.chunks[0][0]) if call.chunks else None
        calls.append(
            (
                trace.id,
                seq,
                call.purpose,
                call.method,
                path,
                model,
                revision,
                request,
                call.status_code,
                response,
                chunks,
                int(decoded),
                trace.timestamp(call.start),
                first_byte,
                trace.timestamp(call.end or end),
                _json(usage) if usage is not None else None,
                call.error_type,
                counts.total(),
                _json(counts),
            )
        )
        total.update(counts)
    with connect(db, write=True) as conn, transaction(conn):
        conn.execute(
            'INSERT INTO consults VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                trace.id,
                trace.request_id,
                stamp(trace.created),
                trace.timestamp(end),
                trace.user_hash,
                trace.role,
                trace.session_id,
                question,
                answer_shown,
                trace.answer_verdict,
                trace.status,
                trace.error_type,
                POLICY,
                app_commit,
                trace.corpus_release_id,
                hashlib.sha256(system.encode()).hexdigest() if system is not None else None,
                trace.feedback_turn_id,
                total.total(),
                _json(total),
            ),
        )
        conn.executemany('INSERT INTO consult_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)', steps)
        conn.executemany(
            'INSERT INTO model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', calls
        )
