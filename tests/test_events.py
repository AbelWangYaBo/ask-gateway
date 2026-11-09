"""DSH 原始事件 → 存储事件投影、step usage 聚合、搜索计数。"""

from __future__ import annotations

import pytest

from ask_gateway.runtime_pool.events import (
    EventProjector,
    StepUsage,
    aggregate_usage,
    count_search,
)
from ask_gateway.runtime_pool.runner import RunOutcome, build_outcome


def _assistant_message(
    step: int,
    *,
    blocks: list[dict],
    usage: dict | None = None,
    turn: int = 1,
) -> dict:
    data: dict = {"turn": turn, "step": step, "stream": [], "message": {"content": blocks}}
    if usage is not None:
        data["usage"] = usage
    return {"type": "assistant/message", "seq": step * 10, "time": step, "data": data}


def _tool_call(step: int, name: str, call_id: str = "call-1") -> dict:
    return {
        "type": "tool/call",
        "seq": step * 10 + 1,
        "time": step,
        "data": {"turn": 1, "step": step, "callId": call_id, "name": name, "arguments": "{}"},
    }


def test_projector_keeps_raw_and_adds_thinking_text_projections() -> None:
    raw = [
        {"type": "turn/start", "seq": 0, "time": 1, "data": {"turn": 1}},
        _assistant_message(
            1,
            blocks=[
                {"type": "reasoning", "text": "先想想"},
                {"type": "text", "text": "答案正文"},
            ],
            usage={"inputTokens": 10, "outputTokens": 5},
        ),
        {"type": "turn/end", "seq": 9, "time": 2, "data": {"turn": 1, "reason": {"kind": "stop"}}},
    ]
    projector = EventProjector()
    stored: list = []
    for event in raw:
        stored.extend(projector.ingest_raw(event))

    assert [s.event_type for s in stored] == [
        "turn/start",
        "assistant/message",
        "thinking",
        "text",
        "turn/end",
    ]
    assert [s.seq for s in stored] == [1, 2, 3, 4, 5]
    # 原始事件完整保真
    assert stored[1].payload["data"]["usage"] == {"inputTokens": 10, "outputTokens": 5}
    # 投影只带文本与定位
    assert stored[2].payload == {"text": "先想想", "turn": 1, "step": 1}
    assert stored[3].payload == {"text": "答案正文", "turn": 1, "step": 1}


def test_projector_ignores_tool_call_blocks() -> None:
    raw = [
        _assistant_message(1, blocks=[{"type": "tool-call", "id": "c1", "name": "web_search"}]),
    ]
    stored = EventProjector().ingest_raw(raw[0])
    assert [s.event_type for s in stored] == ["assistant/message"]


def test_projector_tolerates_missing_data() -> None:
    stored = EventProjector().ingest_raw({"type": "turn/start"})
    assert stored[0].event_type == "turn/start"
    assert stored[0].payload == {"type": "turn/start"}


def test_aggregate_usage_complete_step_sums_tokens() -> None:
    raw = [
        _assistant_message(
            1,
            blocks=[{"type": "text", "text": "a"}],
            usage={
                "inputTokens": 100,
                "outputTokens": 40,
                "cacheReadTokens": 10,
                "cacheWriteTokens": 5,
                "reasoningTokens": 8,
            },
        ),
        _assistant_message(
            1,
            blocks=[{"type": "text", "text": "b"}],
            usage={"inputTokens": 20, "outputTokens": 10, "reasoningTokens": 2},
        ),
        _tool_call(1, "web_search"),
    ]
    usages = aggregate_usage(raw)
    assert len(usages) == 1
    u = usages[0]
    assert u.complete is True
    assert u.step == 1
    assert u.input_tokens == 120
    assert u.output_tokens == 50
    assert u.cache_read_tokens == 10
    assert u.cache_write_tokens == 5
    assert u.reasoning_tokens == 10
    assert u.search_count == 1


def test_aggregate_usage_incomplete_step_nulls_tokens() -> None:
    # 同一 step 两个 attempt，第二个没报 usage → 整 step complete=False，不伪造合计
    raw = [
        _assistant_message(
            1, blocks=[{"type": "text", "text": "a"}], usage={"inputTokens": 100, "outputTokens": 40}
        ),
        _assistant_message(1, blocks=[{"type": "text", "text": "b"}]),
    ]
    usages = aggregate_usage(raw)
    assert usages == [
        StepUsage(
            step=1,
            complete=False,
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            reasoning_tokens=None,
            search_count=0,
        )
    ]


def test_aggregate_usage_multi_step_and_search_split() -> None:
    raw = [
        _assistant_message(1, blocks=[], usage={"inputTokens": 10, "outputTokens": 5}),
        _tool_call(1, "web_search", "c1"),
        _tool_call(1, "web_search", "c2"),
        _assistant_message(2, blocks=[], usage={"inputTokens": 7, "outputTokens": 3}),
        _tool_call(2, "web_search", "c3"),
    ]
    usages = aggregate_usage(raw)
    assert [u.step for u in usages] == [1, 2]
    assert usages[0].search_count == 2
    assert usages[1].search_count == 1
    assert count_search(raw) == 3


def test_aggregate_usage_no_messages_returns_empty() -> None:
    assert aggregate_usage([{"type": "turn/start", "data": {"turn": 1}}]) == []


def test_build_outcome_assembles_parts() -> None:
    raw = [
        _assistant_message(1, blocks=[{"type": "text", "text": "最终答案"}], usage={"inputTokens": 1, "outputTokens": 1}),
        {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "stop"}}},
    ]
    outcome = build_outcome(
        session_id="s-1",
        raw_events=raw,
        final_response="最终答案",
        finish_reason="stop",
    )
    assert isinstance(outcome, RunOutcome)
    assert outcome.session_id == "s-1"
    assert outcome.final_response == "最终答案"
    assert outcome.finish_reason == "stop"
    assert outcome.search_count == 0
    assert len(outcome.step_usages) == 1
    assert [e.event_type for e in outcome.events] == [
        "assistant/message",
        "text",
        "turn/end",
    ]


def test_projector_requires_dict_events() -> None:
    with pytest.raises(TypeError):
        EventProjector().ingest_raw("not-a-dict")  # type: ignore[arg-type]
