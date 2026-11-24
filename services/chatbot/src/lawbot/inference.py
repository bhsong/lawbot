"""Per-consultation observation at the inference send boundary."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

REQUEST_ID: ContextVar[str | None] = ContextVar('lawbot_request_id', default=None)
OBSERVER: ContextVar[Callable[[str, str, str | None, Exception | None], None] | None] = ContextVar(
    'lawbot_inference_observer', default=None
)


class InferenceControlError(RuntimeError):
    """Local inference control failed; do not treat it as a model error."""


class InferenceStopped(InferenceControlError):
    """The evaluator stopped before sending the next request."""


def failure_fields(stage: str, exc: BaseException, request_id: str | None = None) -> dict:
    """Allowlist metadata; never format exceptions, requests, or response bodies."""
    status = None
    root = exc
    while True:
        response = getattr(root, 'response', None)
        code = (
            getattr(root, 'status_code', None)
            or getattr(root, 'status', None)
            or getattr(response, 'status_code', None)
        )
        if type(code) is int and 100 <= code <= 599:
            status = code
        if root.__cause__ is None:
            break
        root = root.__cause__
    return {'stage': stage, 'request_id': request_id, 'exception': type(root).__name__, 'status_code': status}


@contextmanager
def observe_inference(stage: str):
    observer = OBSERVER.get()
    if observer is None:
        yield
        return
    rid = REQUEST_ID.get()
    observer('send', stage, rid, None)
    try:
        yield
    except Exception as exc:
        observer('failed', stage, rid, exc)
        raise
    else:
        observer('complete', stage, rid, None)
