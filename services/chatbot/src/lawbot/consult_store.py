"""Redact a completed trace and persist it atomically.

Configured model credentials, addresses and the web port are not supplied as model inputs;
generated SSE tokens are not reassembled for redaction.
"""

import codecs
import hashlib
import json
import re
import time
import zlib
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from lawbot import feedback
from lawbot.consult_trace import POLICY, Call, Consultation, stamp
from lawbot.db.connection import connect, transaction

_COOKIE = rb'(?<![A-Za-z0-9_+/-])[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048](?![A-Za-z0-9_+/=-])'
_JSON_STRING = re.compile(rb'"(?:[^"\\\x00-\x1f]|\\.)*"')
_LINE_END = re.compile(rb'\r\n|\r|\n')
_HOST_END = re.compile(
    rb'(?i:(?<![a-z0-9_.-])(?:[a-z][a-z0-9+.-]*://[a-z0-9-]+|localhost|[a-z0-9-]+(?:\.[a-z0-9-]+)+\.?'
    rb'|\[[a-f0-9:.]+(?:%[a-z0-9_.-]+)?\]))\Z'
)
_WIDE_BOMS = {
    'utf-16': (('utf-16-be', codecs.BOM_UTF16_BE), ('utf-16-le', codecs.BOM_UTF16_LE)),
    'utf-32': (('utf-32-be', codecs.BOM_UTF32_BE), ('utf-32-le', codecs.BOM_UTF32_LE)),
}


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
        self.port = (
            re.compile(rb'(?<=:)' + str(port).encode() + rb'(?=$|[\s/?#"\'<>),;\]}\\])') if port is not None else None
        )
        patterns: dict[bytes, str] = {}
        for value, reason in sorted(values, key=lambda item: len(item[0]), reverse=True):
            if not value:
                continue
            literal = re.escape(value.encode())
            pattern = b'(?i:' + literal + b')' if reason.endswith('_address') else literal
            patterns.setdefault(pattern, reason)
        patterns[_COOKIE] = 'session_cookie'
        self.reasons = list(patterns.values())
        self.pattern = re.compile(b'|'.join(b'(' + term + b')' for term in patterns))

    def _matches(self, value: bytes) -> Iterator[tuple[int, int, str]]:
        for match in self.pattern.finditer(value):
            assert match.lastindex is not None
            yield match.start(), match.end(), self.reasons[match.lastindex - 1]
        if self.port is not None:
            for match in self.port.finditer(value):
                if _HOST_END.search(value, 0, match.start() - 1):
                    yield match.start(), match.end(), 'web_port'

    def _plain_edits(self, value: bytes, counts: Counter[str], offset=0) -> Iterator[tuple[int, int, bytes]]:
        end = 0
        for start, stop, reason in sorted(self._matches(value)):
            if start >= end:
                counts[reason] += 1
                yield offset + start, offset + stop, _marker(reason)
                end = stop

    def body(self, value: bytes, counts: Counter[str]) -> bytes:
        return _apply_edits([value], self._plain_edits(value, counts))[0]

    def wire(self, chunks: list[bytes], counts: Counter[str]) -> list[bytes]:
        """Inspect JSON string tokens without rewriting unchanged bytes or joining generated deltas."""
        joined = b''.join(chunks)
        encoding = json.detect_encoding(joined)
        if encoding not in ('utf-8', 'utf-8-sig'):
            # Wide JSON is legal in the decoder; translate edit positions without re-encoding untouched bytes.
            try:
                text = joined.decode(encoding).encode('utf-8')
            except UnicodeError:
                return _apply_edits(chunks, self._plain_edits(joined, counts))
            codec, bom = next(
                ((codec, bom) for codec, bom in _WIDE_BOMS.get(encoding, ()) if joined.startswith(bom)),
                (encoding, b''),
            )

            def wide_edits():
                at, offset = 0, len(bom)
                for start, end, replacement in self._wire_edits(text, counts):
                    offset += len(text[at:start].decode().encode(codec))
                    stop = offset + len(text[start:end].decode().encode(codec))
                    yield offset, stop, replacement.decode().encode(codec)
                    at, offset = end, stop

            return _apply_edits(chunks, wide_edits())
        return _apply_edits(chunks, self._wire_edits(joined, counts))

    def _wire_edits(self, value: bytes, counts: Counter[str]) -> Iterator[tuple[int, int, bytes]]:
        at = 0
        for match in _JSON_STRING.finditer(value):
            if b'\\' not in (inside := match[0][1:-1]):
                yield from self._plain_edits(value[at : match.start()], counts, at)
                yield from self._plain_edits(inside, counts, match.start() + 1)
                at = match.end()
                continue
            try:
                decoded = json.loads(match[0]).encode('utf-8', errors='surrogatepass')
            except ValueError, UnicodeError:
                continue
            yield from self._plain_edits(value[at : match.start()], counts, at)
            redacted = self.body(decoded, counts)
            if redacted != decoded:
                payload = json.dumps(redacted.decode('utf-8', errors='surrogatepass')).encode()
                yield match.start(), match.end(), payload
            at = match.end()
        yield from self._plain_edits(value[at:], counts, at)

    def data(self, value, counts: Counter[str]):
        if isinstance(value, str):
            return self.body(value.encode(), counts).decode()
        if isinstance(value, (tuple, list)):
            return [self.data(item, counts) for item in value]
        if isinstance(value, dict):
            return {self.data(key, counts): self.data(item, counts) for key, item in value.items()}
        return value


def _apply_edits(chunks: list[bytes], edits: Iterator[tuple[int, int, bytes]]) -> list[bytes]:
    """Keep receive offsets and boundaries; place replacements where they start."""
    edit = next(edits, None)
    if edit is None:
        return chunks
    starts = [0]
    for chunk in chunks:
        starts.append(starts[-1] + len(chunk))
    output = []
    consumed = 0
    for index, chunk in enumerate(chunks):
        at = max(0, consumed - starts[index])
        parts = bytearray()
        while edit is not None and bisect_right(starts, edit[0]) - 1 == index:
            start, consumed, replacement = edit
            parts += chunk[at : start - starts[index]]
            parts += replacement
            at = consumed - starts[index]
            edit = next(edits, None)
        parts += chunk[at:]
        output.append(bytes(parts))
    return output


def _gzip_header(data: bytes, start: int, redactor: Redactor) -> None:
    # Extra fields may carry arbitrary encoded metadata, which is not safe to inspect as text.
    if len(data) - start < 10 or data[start : start + 3] != b'\x1f\x8b\x08':
        raise ValueError('Invalid gzip member')
    flags = data[start + 3]
    if flags & 0xE4:
        raise ValueError('Unverifiable gzip metadata')
    end = start + 10
    for flag in (8, 16):  # FNAME, FCOMMENT
        if flags & flag:
            terminator = data.index(b'\0', end)
            metadata = data[end:terminator]
            if redactor.wire([metadata], Counter())[0] != metadata:
                raise ValueError('Sensitive gzip metadata')
            end = terminator + 1


def _inflate(encoding: str, chunks: list[bytes], redactor: Redactor) -> list[bytes]:
    if encoding not in ('gzip', 'deflate'):
        raise ValueError('Unverifiable content encoding')
    raw = b''.join(chunks)
    gzip = encoding == 'gzip'
    if gzip:
        wbits = 31
    else:
        head = int.from_bytes(raw[:2], 'big')
        wbits = 15 if len(raw) >= 2 and head % 31 == 0 and head & 0x0F00 == 0x0800 else -15
    decoder = None
    end = 0
    plain = []
    for chunk in chunks:
        end += len(chunk)
        pending = chunk
        output = bytearray()
        while pending:
            if decoder is None or decoder.eof:
                if gzip:
                    _gzip_header(raw, end - len(pending), redactor)
                elif decoder is not None:
                    raise ValueError('Trailing deflate bytes')
                decoder = zlib.decompressobj(wbits)
            output.extend(decoder.decompress(pending))
            pending = decoder.unused_data
        plain.append(bytes(output))
    if decoder is None or not decoder.eof:
        raise ValueError('Incomplete compressed response')
    return plain


def _sse_data(body: bytes) -> Iterator[bytes]:
    at = len(codecs.BOM_UTF8) if body.startswith(codecs.BOM_UTF8) else 0
    data = []
    for delimiter in _LINE_END.finditer(body, at):
        line, at = body[at : delimiter.start()], delimiter.end()
        if not line and data:
            yield b'\n'.join(data)
            data.clear()
        elif line == b'data' or line.startswith(b'data:'):
            data.append(line[5:].removeprefix(b' '))


def _usage(body: bytes, streaming: bool) -> dict[str, int] | None:
    documents = _sse_data(body) if streaming else (body,)
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
            plain = raw
            for name in reversed(encoding.split(',')):
                if name.strip() != 'identity':
                    plain = _inflate(name.strip(), plain, redactor)
        except ValueError, zlib.error:
            counts['unreadable_encoding'] += 1
            raw = [_marker('unreadable_encoding'), *([b''] * (len(raw) - 1))]
            decoded = True
        else:
            joined = b''.join(plain)
            usage = _usage(joined, call.streaming)
            redacted = redactor.wire(plain, counts)
            if redacted != plain:
                raw, decoded = redacted, True
    else:
        joined = b''.join(raw)
        usage = _usage(joined, call.streaming)
        raw = redactor.wire(raw, counts)
    if call.status_code is None:
        return None, None, decoded, usage
    if not call.streaming:
        return b''.join(raw), None, decoded, usage
    return None, raw, decoded, usage


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
    received_chunks = []
    for seq, call in enumerate(trace.calls, 1):
        counts = Counter()
        path = redactor.data(call.path, counts)
        model = redactor.data(call.model_id, counts)
        revision = redactor.data(call.model_revision, counts)
        request = redactor.wire([call.request_body], counts)[0]
        response, chunks, decoded, usage = _response(call, redactor, counts)
        if chunks is not None:
            received_chunks.extend(
                (trace.id, seq, number, offset, body)
                for number, ((offset, _), body) in enumerate(zip(call.chunks, chunks, strict=True), 1)
            )
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
                call.send_status,
                call.status_code,
                response,
                int(chunks is not None),
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
            'INSERT INTO consults VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
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
                trace.delivery_status,
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
            'INSERT INTO model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', calls
        )
        conn.executemany('INSERT INTO model_call_chunks VALUES (?, ?, ?, ?, ?)', received_chunks)
