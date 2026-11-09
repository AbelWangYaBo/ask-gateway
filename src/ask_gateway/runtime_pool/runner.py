"""Runner 协议与统一运行结果。生产实现 DshRunner，测试实现 FakeRunner。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ask_gateway.runtime_pool.events import (
    EventProjector,
    StepUsage,
    StoredEvent,
    aggregate_usage,
    count_search,
)


@dataclass(slots=True)
class RunOutcome:
    session_id: str
    final_response: str
    finish_reason: str | None
    events: list[StoredEvent]
    step_usages: list[StepUsage]
    search_count: int


class Runner(Protocol):
    """一个可复用的 runtime 句柄。所有实现必须线程使用上由池保证单租约。"""

    upstream_key_label: str

    def start(self) -> None: ...

    def run(
        self,
        messages: list[dict],
        *,
        session_id: str | None = None,
        on_event: Callable[[StoredEvent], None] | None = None,
    ) -> RunOutcome: ...

    def health_alive(self) -> bool: ...

    def close(self) -> None: ...


def build_outcome(
    *,
    session_id: str,
    raw_events: list[dict],
    final_response: str,
    finish_reason: str | None,
    projector: EventProjector | None = None,
) -> RunOutcome:
    """从原始事件统一构造 RunOutcome；DshRunner 与 FakeRunner 共用，保证投影口径一致。"""
    if projector is None:
        projector = EventProjector()
        for raw in raw_events:
            projector.ingest_raw(raw)
    return RunOutcome(
        session_id=session_id,
        final_response=final_response,
        finish_reason=finish_reason,
        events=projector.stored,
        step_usages=aggregate_usage(raw_events),
        search_count=count_search(raw_events),
    )
