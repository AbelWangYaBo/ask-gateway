"""问答管线编排：鉴权后建审计行 → 配额 → 池借用 → DSH run → 事件/usage 落库 → 计费汇总。

同步管线（DSH SDK 阻塞式）。非流式直接返回 ChatResult；SSE 由工作线程跑同一条管线，
经队列把 OpenAI chunk 喂给生成器。管线自己持有 Session（从 factory 新开），
因此客户端断连、生成器提前关闭时，工作线程仍能把已产生的事件与 usage 完整落库。
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import anyio
from sqlalchemy.orm import Session, sessionmaker

from ask_gateway.billing.cost import compute_cost, snapshot_rule
from ask_gateway.billing.pricing import select_rule
from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.request import (
    STATUS_CLIENT_DISCONNECTED,
    STATUS_FAILED,
    STATUS_RATE_LIMITED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    RequestRecord,
)
from ask_gateway.runtime_pool.errors import (
    PoolClosing,
    PoolTimeout,
    RunnerBroken,
    RunnerError,
    RunnerTimeout,
)
from ask_gateway.runtime_pool.events import StoredEvent
from ask_gateway.runtime_pool.pool import RuntimePool
from ask_gateway.runtime_pool.runner import RunOutcome
from ask_gateway.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ResponseMessage,
    UsageInfo,
)
from ask_gateway.services import audit_service
from ask_gateway.services.errors import (
    ModelNotAllowedError,
    PoolBusyError,
    QuotaError,
    ServiceError,
    ShuttingDownError,
    UpstreamError,
    UpstreamTimeoutError,
)
from ask_gateway.services.key_service import touch_last_used
from ask_gateway.services.quota_service import QuotaService


@dataclass(frozen=True, slots=True)
class ChatRuntime:
    """管线运行依赖，应用工厂组装一次，路由与测试共用。"""

    session_factory: sessionmaker
    quota: QuotaService
    pool: RuntimePool
    allowed_models: tuple[str, ...]


@dataclass(slots=True)
class ChatResult:
    request_id: uuid.UUID
    status: str
    created: int
    model: str
    dsh_session_id: str | None
    final_response: str
    finish_reason: str | None
    thinking_text: str
    events: list[StoredEvent]
    totals: dict[str, int | None]
    search_count: int
    total_cost_cents: Decimal
    upstream_key_label: str | None


# ---------- 非流式管线 ----------


def execute_chat(
    runtime: ChatRuntime,
    *,
    api_key: ApiKey,
    payload: ChatCompletionRequest,
    request_id: uuid.UUID,
    client_ip: str | None,
    user_agent: str | None,
    on_event: Callable[[StoredEvent], None] | None = None,
    disconnect: threading.Event | None = None,
) -> ChatResult:
    if payload.model not in runtime.allowed_models:
        raise ModelNotAllowedError(payload.model)

    messages = [m.model_dump(exclude_none=True) for m in payload.messages]
    session: Session = runtime.session_factory()
    row: RequestRecord | None = None
    handle = None
    slot_acquired = False
    release_broken = False
    collected: list[StoredEvent] = []

    try:
        now = datetime.now(UTC)
        row = audit_service.create_request_row(
            session,
            api_key=api_key,
            model=payload.model,
            stream=payload.stream,
            prompt_messages=messages,
            queued_at=now,
            enable_web_search=payload.enable_web_search,
            thinking_effort=payload.thinking_effort,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        row.request_id = request_id
        touch_last_used(session, api_key.id)

        try:
            runtime.quota.check_rpm(api_key)
            runtime.quota.check_daily_budget(session, api_key)
            runtime.quota.acquire_slot(api_key)
        except QuotaError as exc:
            _terminal(
                session,
                row,
                STATUS_RATE_LIMITED,
                error_code=exc.code,
                error_message=exc.message,
            )
            session.commit()
            raise
        slot_acquired = True

        try:
            handle = runtime.pool.acquire()
        except PoolTimeout as exc:
            _terminal(
                session,
                row,
                STATUS_RATE_LIMITED,
                error_code="pool_busy",
                error_message=str(exc),
            )
            session.commit()
            raise PoolBusyError(str(exc)) from exc
        except PoolClosing as exc:
            _terminal(
                session, row, STATUS_FAILED, error_code="shutting_down", error_message=str(exc)
            )
            session.commit()
            raise ShuttingDownError from exc
        except RunnerBroken as exc:  # 全部上游组熔断
            _terminal(
                session,
                row,
                STATUS_FAILED,
                error_code="upstream_broken",
                error_message=str(exc),
            )
            session.commit()
            raise UpstreamError("upstream_broken", str(exc)) from exc

        row.status = STATUS_RUNNING
        row.started_at = datetime.now(UTC)
        row.upstream_key_label = handle.label
        session.flush()

        def _on_event(event: StoredEvent) -> None:
            collected.append(event)
            if on_event is not None:
                on_event(event)

        try:
            outcome = handle.runner.run(messages, on_event=_on_event)
        except RunnerTimeout as exc:
            release_broken = True  # 超时后进程状态不明，保守回收
            _fail_with_events(session, row, collected, "upstream_timeout", str(exc))
            session.commit()
            raise UpstreamTimeoutError(str(exc)) from exc
        except RunnerBroken as exc:
            release_broken = True
            _fail_with_events(session, row, collected, "upstream_broken", str(exc))
            session.commit()
            raise UpstreamError("upstream_broken", str(exc)) from exc
        except RunnerError as exc:
            _fail_with_events(session, row, collected, "upstream_error", str(exc))
            session.commit()
            raise UpstreamError("upstream_error", str(exc)) from exc

        _persist_outcome(session, row, outcome, collected, payload.model)
        status = (
            STATUS_CLIENT_DISCONNECTED
            if disconnect is not None and disconnect.is_set()
            else STATUS_SUCCEEDED
        )
        row.status = status
        row.finished_at = datetime.now(UTC)
        session.commit()

        return _to_result(row, outcome, collected)
    except ServiceError:
        raise
    except Exception as exc:  # 未预期错误：尽力留痕，不吞异常
        session.rollback()
        if row is not None:
            try:
                row.status = STATUS_FAILED
                row.error_code = "internal_error"
                row.error_message = str(exc)[:1024]
                row.finished_at = datetime.now(UTC)
                session.commit()
            except Exception:
                session.rollback()
        raise
    finally:
        if slot_acquired:
            runtime.quota.release_slot(api_key.id)
        if handle is not None:
            runtime.pool.release(handle, broken=release_broken)
        session.close()


def _persist_outcome(
    session: Session,
    row: RequestRecord,
    outcome: RunOutcome,
    collected: list[StoredEvent],
    model: str,
) -> None:
    audit_service.bulk_insert_events(
        session,
        row.id,
        [(event.seq, event.event_type, event.payload) for event in collected],
    )

    total_cost = Decimal("0")
    for step_usage in outcome.step_usages:
        rule = select_rule(session, model)
        snapshot = snapshot_rule(rule) if rule is not None else {}
        cost = compute_cost(
            complete=step_usage.complete,
            input_tokens=step_usage.input_tokens,
            output_tokens=step_usage.output_tokens,
            cache_read_tokens=step_usage.cache_read_tokens,
            cache_write_tokens=step_usage.cache_write_tokens,
            reasoning_tokens=step_usage.reasoning_tokens,
            search_count=step_usage.search_count,
            snapshot=snapshot,
        )
        audit_service.insert_usage_record(
            session,
            request_id=row.id,
            step=step_usage.step,
            complete=step_usage.complete,
            pricing_snapshot=snapshot,
            created_at=datetime.now(UTC),
            cost_cents=cost,
            search_count=step_usage.search_count,
            input_tokens=step_usage.input_tokens,
            output_tokens=step_usage.output_tokens,
            cache_read_tokens=step_usage.cache_read_tokens,
            cache_write_tokens=step_usage.cache_write_tokens,
            reasoning_tokens=step_usage.reasoning_tokens,
        )
        total_cost += cost

    totals = _sum_totals(outcome)
    row.dsh_session_id = outcome.session_id
    row.final_answer = outcome.final_response
    row.finish_reason = outcome.finish_reason
    row.total_input_tokens = totals["input_tokens"]
    row.total_output_tokens = totals["output_tokens"]
    row.cache_read_tokens = totals["cache_read_tokens"]
    row.cache_write_tokens = totals["cache_write_tokens"]
    row.reasoning_tokens = totals["reasoning_tokens"]
    row.search_count = outcome.search_count
    row.total_cost_cents = total_cost


def _sum_totals(outcome: RunOutcome) -> dict[str, int | None]:
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    )
    totals: dict[str, int | None] = {}
    for name in fields:
        values = [getattr(u, name) for u in outcome.step_usages if u.complete]
        known = [v for v in values if v is not None]
        totals[name] = sum(known) if known else None
    return totals


def _to_result(
    row: RequestRecord, outcome: RunOutcome, collected: list[StoredEvent]
) -> ChatResult:
    thinking_text = "".join(
        event.payload.get("text", "") for event in collected if event.event_type == "thinking"
    )
    return ChatResult(
        request_id=row.request_id,
        status=row.status,
        created=int(row.queued_at.timestamp()),
        model=row.model,
        dsh_session_id=outcome.session_id,
        final_response=outcome.final_response,
        finish_reason=outcome.finish_reason,
        thinking_text=thinking_text,
        events=list(collected),
        totals=_sum_totals(outcome),
        search_count=outcome.search_count,
        total_cost_cents=Decimal(str(row.total_cost_cents)),
        upstream_key_label=row.upstream_key_label,
    )


def _terminal(
    session: Session,
    row: RequestRecord,
    status: str,
    *,
    error_code: str,
    error_message: str,
) -> None:
    row.status = status
    row.error_code = error_code
    row.error_message = error_message[:1024]
    row.finished_at = datetime.now(UTC)
    session.flush()


def _fail_with_events(
    session: Session,
    row: RequestRecord,
    collected: list[StoredEvent],
    error_code: str,
    error_message: str,
) -> None:
    # 失败前已产生的事件照常落库，完整保留内部链路
    if collected:
        audit_service.bulk_insert_events(
            session,
            row.id,
            [(event.seq, event.event_type, event.payload) for event in collected],
        )
    _terminal(session, row, STATUS_FAILED, error_code=error_code, error_message=error_message)


# ---------- 响应组装 ----------


def build_completion_response(result: ChatResult) -> ChatCompletionResponse:
    totals = result.totals
    input_tokens = totals["input_tokens"] or 0
    output_tokens = totals["output_tokens"] or 0
    usage = UsageInfo(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        reasoning_tokens=totals["reasoning_tokens"],
        cache_read_tokens=totals["cache_read_tokens"],
        cache_write_tokens=totals["cache_write_tokens"],
        search_count=result.search_count,
    )
    message = ResponseMessage(
        content=result.final_response,
        reasoning_content=result.thinking_text or None,
    )
    return ChatCompletionResponse(
        id=f"chatcmpl-{result.request_id.hex}",
        created=result.created,
        model=result.model,
        choices=[Choice(message=message, finish_reason=result.finish_reason)],
        usage=usage,
    )


# ---------- SSE ----------

_SSE_DONE = object()


async def sse_chat(
    runtime: ChatRuntime,
    *,
    api_key: ApiKey,
    payload: ChatCompletionRequest,
    request_id: uuid.UUID,
    client_ip: str | None,
    user_agent: str | None,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> Any:
    """生成 SSE 帧（sse-starlette 可直接消费的 async 生成器）。

    工作线程跑阻塞管线，本生成器带超时读队列并轮询断连状态；
    客户端断连时置位 disconnect，管线把请求收尾为 client_disconnected，
    已产生的事件与 usage 由工作线程自行提交。
    """
    frame_queue: queue.Queue = queue.Queue()
    disconnect = threading.Event()

    def worker() -> None:
        try:
            result = execute_chat(
                runtime,
                api_key=api_key,
                payload=payload,
                request_id=request_id,
                client_ip=client_ip,
                user_agent=user_agent,
                on_event=lambda event: frame_queue.put(
                    event_chunk(request_id, event)
                ),
                disconnect=disconnect,
            )
            for frame in finish_chunks(result):
                frame_queue.put(frame)
        except ServiceError as exc:
            frame_queue.put(_error_frame(exc.code, exc.message, request_id))
        except Exception as exc:  # 流已开始无法改 HTTP 状态，用错误帧告知客户端
            frame_queue.put(
                _error_frame("internal_error", str(exc)[:1024], request_id)
            )
        finally:
            frame_queue.put(_SSE_DONE)

    thread = threading.Thread(target=worker, name=f"chat-sse-{request_id.hex[:8]}", daemon=True)
    thread.start()

    yield {
        "event": "x-request-id",
        "data": json.dumps({"request_id": str(request_id)}),
    }
    yield _chunk(f"chatcmpl-{request_id.hex}", {"role": "assistant"})

    try:
        while True:
            try:
                frame = await anyio.to_thread.run_sync(
                    frame_queue.get, True, 0.5
                )
            except queue.Empty:
                if await is_disconnected():
                    disconnect.set()
                    return
                continue
            if frame is _SSE_DONE:
                return
            yield frame
    finally:
        disconnect.set()


def _error_frame(code: str, message: str, request_id: uuid.UUID) -> dict[str, Any]:
    return {
        "event": "error",
        "data": json.dumps(
            {
                "code": code,
                "message": message,
                "request_id": str(request_id),
            }
        ),
    }


def _chunk(
    chunk_id: str, delta: dict, *, finish_reason: str | None = None
) -> dict[str, Any]:
    return {
        "data": json.dumps(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason}
                ],
            }
        )
    }


def event_chunk(request_id: uuid.UUID, event: StoredEvent) -> dict[str, Any]:
    chunk_id = f"chatcmpl-{request_id.hex}"
    if event.event_type == "thinking":
        return _chunk(chunk_id, {"reasoning_content": event.payload.get("text", "")})
    if event.event_type == "text":
        return _chunk(chunk_id, {"content": event.payload.get("text", "")})
    # DSH 原始过程事件走自定义事件，不影响标准 OpenAI 客户端
    return {
        "event": "x-dsh-event",
        "data": json.dumps(
            {"seq": event.seq, "type": event.event_type, "payload": event.payload}
        ),
    }


def finish_chunks(result: ChatResult) -> list[dict[str, Any]]:
    chunk_id = f"chatcmpl-{result.request_id.hex}"
    totals = result.totals
    input_tokens = totals["input_tokens"] or 0
    output_tokens = totals["output_tokens"] or 0
    finish = {
        "data": json.dumps(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": result.finish_reason}
                ],
            }
        )
    }
    usage = {
        "data": json.dumps(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "reasoning_tokens": totals["reasoning_tokens"],
                    "cache_read_tokens": totals["cache_read_tokens"],
                    "cache_write_tokens": totals["cache_write_tokens"],
                    "search_count": result.search_count,
                },
            }
        )
    }
    return [finish, usage, {"data": "[DONE]"}]
