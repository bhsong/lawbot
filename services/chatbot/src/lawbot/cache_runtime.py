"""Fail closed on cache errors and fence each hit against a corpus activation."""

import time
from dataclasses import dataclass
from pathlib import Path

from lawbot.cache_store import READ_TIMEOUT, CacheDatabaseError, Store, translate_sqlite_errors
from lawbot.calc_gate import is_calculation_question
from lawbot.corpus_store import active_token
from lawbot.exact_cache import FORMAT, MODEL, PROFILE, Article, Snapshot, normalize


@dataclass(frozen=True)
class Match:
    answer_id: str
    text: str
    articles: list[Article]


@dataclass(frozen=True)
class Decision:
    mode: str
    reason: str
    elapsed: float
    eligible: bool
    match: Match | None = None

    def metrics(self) -> dict:
        return {
            'cache_mode': self.mode,
            'cache_reason': self.reason,
            'cache_eligible': self.eligible,
            'would_hit': self.mode == 'shadow' and self.match is not None,
            'exact_lookup_ms': round(self.elapsed * 1000, 3),
        }


@dataclass(frozen=True)
class ExactCache:
    store: Store
    corpus_db: Path
    snapshot: Snapshot

    def deployment_status(self) -> tuple[str, str]:
        namespace = self.snapshot.namespace.id
        try:
            with translate_sqlite_errors():
                version = self.store.check_schema()
                control = self.store.control()
                detail = f'스키마 {version} · 모드 {control.mode} · namespace {namespace[:12]}'
                if control.namespace_id is not None:
                    if control.namespace_id != namespace:
                        return 'WARN', f'사용 안 함 (namespace 불일치 · {detail})'
                    expected = (self.snapshot.release_id, control.activation_event)
                    if active_token(self.corpus_db, PROFILE, timeout=READ_TIMEOUT) != expected:
                        return 'WARN', f'사용 안 함 (코퍼스 활성화 불일치 · {detail})'
        except FileNotFoundError:
            return 'WARN', '사용 안 함 (DB 파일 없음)'
        except OSError, RuntimeError:
            return 'WARN', '사용 안 함 (DB 스키마 또는 읽기 확인 실패)'
        return 'OK', f'{"사용 안 함" if control.mode == "off" else "사용"} ({detail})'

    def lookup(self, question: str, model: str, fmt: str) -> Decision:
        started = time.perf_counter()
        if is_calculation_question(question):
            return Decision('bypass', 'calculation_question', time.perf_counter() - started, False)
        eligible = (model, fmt) == (MODEL, FORMAT)
        mode = 'unknown'

        def result(reason: str, match: Match | None = None):
            return Decision(mode, reason, time.perf_counter() - started, eligible, match)

        try:
            with translate_sqlite_errors():
                if not eligible:
                    mode = self.store.control().mode
                    return result('unsupported_scope')
                namespace_id = self.snapshot.namespace.id
                normalized = normalize(question)
                control, answer, reason = self.store.lookup(namespace_id, normalized)
                mode = control.mode
                if answer is None:
                    return result(reason)
                articles = self.snapshot.resolve(answer.citations)
                if articles is None:
                    return result('citation_mismatch')
                expected = (self.snapshot.release_id, control.activation_event)
                if active_token(self.corpus_db, PROFILE, timeout=READ_TIMEOUT) != expected:
                    return result('activation_mismatch')
                # Read approval again between two corpus reads, after resolving the entire payload.
                _, current, _ = self.store.lookup(namespace_id, normalized)
                if current != answer:
                    return result('state_changed')
                if active_token(self.corpus_db, PROFILE, timeout=READ_TIMEOUT) != expected:
                    return result('activation_mismatch')
        except CacheDatabaseError as exc:
            return result(exc.reason)
        except OSError:
            return result('db_error')
        except UnicodeError:
            return result('unsupported_input')
        return result('hit', Match(answer.answer_id, answer.text, articles))
