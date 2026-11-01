"""수집기 인터페이스 (원 계획서 5.1).

수집기는 상태를 읽고 비교해 CollectResult를 돌려줄 뿐 저장하지 않는다. 실행기가 모든 수집기를 돌린 뒤
한 트랜잭션으로 저장하므로, 중간에 실패하면 상태가 바뀌지 않아 다음 실행에서 다시 잡힌다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from law_monitor.config import Config
from law_monitor.paths import Paths
from law_monitor.state import ItemState, State


@dataclass
class RunContext:
    config: Config
    state: State
    paths: Paths
    mode: str  # 'bootstrap' | 'normal' | 'replay'
    run_id: str
    now: Callable[[], datetime]


@dataclass
class CollectResult:
    events: list[dict] = field(default_factory=list)
    diffs: dict[str, str] = field(default_factory=dict)  # event_id → unified diff 원문
    cursors: dict[str, str] = field(default_factory=dict)
    items: list[ItemState] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)  # (source, item_id)
    replace_sources: list[str] = field(default_factory=list)  # bootstrap: 이 source의 기존 items를 지우고 새로


class Collector(Protocol):
    name: str

    def has_baseline(self, state: State) -> bool: ...

    def collect(self, ctx: RunContext) -> CollectResult: ...
