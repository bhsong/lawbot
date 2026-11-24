"""Collect one consultation in memory without consuming the caller's HTTP streams."""

import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from lawbot.hits import Timing
from lawbot.inference import REQUEST_ID
from lawbot_common import api

POLICY = 'consult-trace-v1'
_RETRIEVAL = {api.EMBEDDINGS_PATH: 'embedding', api.RERANK_PATH: 'rerank'}


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='microseconds')


def account_hash(user: str) -> str:
    return hashlib.sha256(user.encode()).hexdigest()[:16]


@dataclass
class Step:
    stage: str
    start: int
    end: int | None = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class Call:
    purpose: str
    method: str
    path: str
    model_id: str
    model_revision: str | None
    request_body: bytes
    streaming: bool
    start: int = field(default_factory=time.monotonic_ns)
    end: int | None = None
    status_code: int | None = None
    encoding: str | None = None
    chunks: list[tuple[int, bytes]] = field(default_factory=list)
    error_type: str | None = None


@dataclass
class Consultation:
    request_id: str
    question: str
    user_hash: str
    session_id: str
    role: str
    timing: Timing
    corpus_release_id: str | None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: datetime = field(default_factory=lambda: datetime.now(UTC))
    start: int = field(default_factory=time.monotonic_ns)
    end: int | None = None
    stages: list[Step] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    status: str = 'cancelled'
    error_type: str | None = None
    answer_shown: str | None = None
    answer_verdict: str | None = None
    feedback_turn_id: str | None = None

    def timestamp(self, at: int) -> str:
        return stamp(self.created + timedelta(microseconds=(at - self.start) // 1000))

    @contextmanager
    def step(self, name: str, **result: Any) -> Iterator[dict[str, Any]]:
        step = Step(name, time.monotonic_ns(), result=result)
        self.stages.append(step)
        first_call = len(self.calls)
        try:
            yield result
        except BaseException as exc:
            result['error_type'] = type(exc).__name__
            if (
                name in ('writer', 'search', 'rerank', 'decree_search', 'decree_rerank')
                and len(self.calls) > first_call
            ):
                call = self.calls[-1]
                if call.error_type is None:
                    call.error_type = type(exc).__name__
            raise
        finally:
            step.end = time.monotonic_ns()


ACTIVE: ContextVar[Consultation | None] = ContextVar('consultation', default=None)
_PURPOSE: ContextVar[str | None] = ContextVar('consultation_call_purpose', default=None)


def current() -> Consultation | None:
    trace = ACTIVE.get()
    return trace if trace is not None and trace.request_id == REQUEST_ID.get() else None


@contextmanager
def stage(name: str, **result: Any) -> Iterator[dict[str, Any] | None]:
    if (trace := current()) is None:
        yield None
    else:
        with trace.step(name, **result) as captured:
            yield captured


@contextmanager
def call_purpose(purpose: str) -> Iterator[None]:
    token = _PURPOSE.set(purpose)
    try:
        yield
    finally:
        _PURPOSE.reset(token)


class _RecordedStream(httpx.SyncByteStream):
    def __init__(self, upstream: httpx.SyncByteStream, call: Call):
        self.upstream, self.call = upstream, call

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self.upstream:
                self.call.chunks.append((time.monotonic_ns() - self.call.start, chunk))
                yield chunk
        except httpx.TransportError as exc:
            self.call.error_type = type(exc).__name__
            raise
        finally:
            self.call.end = time.monotonic_ns()

    def close(self) -> None:
        try:
            self.upstream.close()
        finally:
            if self.call.end is None:
                self.call.end = time.monotonic_ns()


class _RecordingTransport(httpx.BaseTransport):
    def __init__(self, upstream: httpx.BaseTransport, trace: Consultation, revisions: dict[str, str | None]):
        self.upstream, self.trace, self.revisions = upstream, trace, revisions

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        purpose = _RETRIEVAL.get(path)
        chat = path.endswith('/chat/completions')
        if request.method != 'POST' or not (purpose or chat):
            return self.upstream.handle_request(request)
        # All inference clients send buffered JSON; inspect without re-encoding or reading a stream.
        body = request.content
        payload = json.loads(body)
        model = payload['model']
        streaming = chat and payload.get('stream') is True
        purpose = purpose or ('writer' if streaming else _PURPOSE.get())
        if purpose is None:
            return self.upstream.handle_request(request)
        call = Call(purpose, request.method, path, model, self.revisions.get(model), body, streaming)
        self.trace.calls.append(call)
        try:
            response = self.upstream.handle_request(request)
        except httpx.TransportError as exc:
            call.error_type = type(exc).__name__
            call.end = time.monotonic_ns()
            raise
        call.status_code = response.status_code
        call.streaming = streaming and response.is_success
        call.encoding = response.headers.get('content-encoding')
        if response.is_error:
            call.error_type = 'HTTPStatusError'
        assert isinstance(response.stream, httpx.SyncByteStream)
        response.stream = _RecordedStream(response.stream, call)
        return response


class TraceClient(httpx.Client):
    """Wrap the selected transport so proxy mounts and client defaults stay intact."""

    revisions: dict[str, str | None]

    def __init__(self, **options: Any):
        super().__init__(**options)
        self.revisions = {}

    def _transport_for_url(self, url: httpx.URL) -> httpx.BaseTransport:
        upstream = super()._transport_for_url(url)
        trace = current()
        return upstream if trace is None else _RecordingTransport(upstream, trace, self.revisions)
