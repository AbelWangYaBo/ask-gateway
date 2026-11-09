"""DSH SessionEvent → 内部存储事件投影，以及 step 粒度 usage 聚合。

两类入库事件：
- 原始事件：event_type 即 DSH 类型（turn/start、tool/call、step/end …），payload 保留完整原事件；
- 投影事件：assistant/message 的 reasoning/text content block 拆成 thinking/text，便于控制台直接渲染。
"""

from __future__ import annotations

from dataclasses import dataclass

WEB_SEARCH_TOOL = "web_search"


@dataclass(frozen=True, slots=True)
class StoredEvent:
    seq: int
    event_type: str
    payload: dict


@dataclass(frozen=True, slots=True)
class StepUsage:
    step: int
    complete: bool
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    search_count: int


class EventProjector:
    """随 run 生命周期增量摄入原始事件，统一编号 seq。"""

    def __init__(self) -> None:
        self._seq = 0
        self.stored: list[StoredEvent] = []
        self.raw_events: list[dict] = []

    def ingest_raw(self, raw: dict) -> list[StoredEvent]:
        if not isinstance(raw, dict):
            raise TypeError(f"event must be a dict, got {type(raw).__name__}")
        self.raw_events.append(raw)
        produced: list[StoredEvent] = []
        produced.append(self._append(raw.get("type", "unknown"), raw))

        if raw.get("type") == "assistant/message":
            data = raw.get("data")
            if isinstance(data, dict):
                message = data.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type in ("reasoning", "text"):
                            projected_type = (
                                "thinking" if block_type == "reasoning" else "text"
                            )
                            produced.append(
                                self._append(
                                    projected_type,
                                    {
                                        "text": block.get("text", ""),
                                        "turn": data.get("turn"),
                                        "step": data.get("step"),
                                    },
                                )
                            )
        self.stored.extend(produced)
        return produced

    def _append(self, event_type: str, payload: dict) -> StoredEvent:
        self._seq += 1
        event = StoredEvent(seq=self._seq, event_type=event_type, payload=payload)
        return event


# ---------- usage 聚合 ----------

_TOKEN_FIELDS = (
    ("input_tokens", "inputTokens"),
    ("output_tokens", "outputTokens"),
    ("cache_read_tokens", "cacheReadTokens"),
    ("cache_write_tokens", "cacheWriteTokens"),
    ("reasoning_tokens", "reasoningTokens"),
)


def aggregate_usage(raw_events: list[dict]) -> list[StepUsage]:
    """按 step 聚合 assistant/message 的 usage；任一 attempt 缺 usage 则该 step 不完整。"""
    messages_by_step: dict[int, list[dict]] = {}
    searches_by_step: dict[int, int] = {}

    for raw in raw_events:
        data = raw.get("data")
        if not isinstance(data, dict):
            continue
        step = data.get("step")
        if not isinstance(step, int):
            continue
        if raw.get("type") == "assistant/message":
            messages_by_step.setdefault(step, []).append(data)
        elif raw.get("type") == "tool/call" and data.get("name") == WEB_SEARCH_TOOL:
            searches_by_step[step] = searches_by_step.get(step, 0) + 1

    result: list[StepUsage] = []
    for step in sorted(set(messages_by_step) | set(searches_by_step)):
        messages = messages_by_step.get(step, [])
        usages = [
            m.get("usage")
            for m in messages
            if isinstance(m.get("usage"), dict)
        ]
        complete = bool(messages) and len(usages) == len(messages)
        search_count = searches_by_step.get(step, 0)

        if not complete:
            result.append(
                StepUsage(
                    step=step,
                    complete=False,
                    input_tokens=None,
                    output_tokens=None,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    reasoning_tokens=None,
                    search_count=search_count,
                )
            )
            continue

        totals: dict[str, int | None] = {}
        for py_name, wire_name in _TOKEN_FIELDS:
            values = [
                int(u[wire_name])
                for u in usages
                if isinstance(u.get(wire_name), int)
            ]
            totals[py_name] = sum(values) if values else None
        result.append(
            StepUsage(
                step=step,
                complete=True,
                input_tokens=totals["input_tokens"],
                output_tokens=totals["output_tokens"],
                cache_read_tokens=totals["cache_read_tokens"],
                cache_write_tokens=totals["cache_write_tokens"],
                reasoning_tokens=totals["reasoning_tokens"],
                search_count=search_count,
            )
        )
    return result


def count_search(raw_events: list[dict]) -> int:
    return sum(usage.search_count for usage in aggregate_usage(raw_events))
