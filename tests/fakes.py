"""离线测试用 FakeRunner：脚本化 DSH 原始事件，可模拟崩溃/超时/多 step/搜索。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from ask_gateway.runtime_pool.events import StoredEvent
from ask_gateway.runtime_pool.runner import RunOutcome, build_outcome


@dataclass
class FakeRunner:
    upstream_key_label: str
    scripted_events: list[dict] = field(default_factory=list)
    final_response: str = "模拟答案"
    finish_reason: str | None = "stop"
    raise_exc: Exception | None = None

    started: bool = False
    closed: bool = False
    run_count: int = 0
    calls: list[dict] = field(default_factory=list)

    def start(self) -> None:
        self.started = True

    def health_alive(self) -> bool:
        return not self.closed

    def run(
        self,
        messages: list[dict],
        *,
        session_id: str | None = None,
        on_event: Callable[[StoredEvent], None] | None = None,
    ) -> RunOutcome:
        self.run_count += 1
        self.calls.append({"messages": messages, "session_id": session_id})
        if self.raise_exc is not None:
            raise self.raise_exc

        # 与 DshRunner 一样整个 run 共用一个 projector，seq 连续
        from ask_gateway.runtime_pool.events import EventProjector

        projector = EventProjector()
        for raw in self.scripted_events:
            for event in projector.ingest_raw(raw):
                if on_event is not None:
                    on_event(event)
        return build_outcome(
            session_id=session_id or f"fake-{uuid.uuid4().hex}",
            raw_events=self.scripted_events,
            final_response=self.final_response,
            finish_reason=self.finish_reason,
            projector=projector,
        )

    def close(self) -> None:
        self.closed = True


class FakeRunnerFactory:
    """记录创建序列的工厂，池测试据此断言懒扩容与回收。"""

    def __init__(
        self,
        *,
        scripted_events: list[dict] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._scripted_events = scripted_events or []
        self._raise_exc = raise_exc
        self.runners: list[FakeRunner] = []

    def __call__(self, label: str) -> FakeRunner:
        runner = FakeRunner(
            upstream_key_label=label,
            scripted_events=list(self._scripted_events),
            raise_exc=self._raise_exc,
        )
        self.runners.append(runner)
        return runner

    @property
    def created_count(self) -> int:
        return len(self.runners)

    def configure(
        self,
        *,
        scripted_events: list[dict] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        """切换后续新建 runner 的脚本与故障（已创建的 runner 不受影响）。

        显式传参才覆盖；raise_exc 默认 None 同时承担"清除故障"语义。
        """
        if scripted_events is not None:
            self._scripted_events = scripted_events
        self._raise_exc = raise_exc
