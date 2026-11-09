"""审计写入：管理动作留痕，以及请求/事件/usage 的持久化函数。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from ask_gateway.models.admin import AdminAuditLog
from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.event import EventRecord
from ask_gateway.models.request import STATUS_QUEUED, RequestRecord
from ask_gateway.models.usage import UsageRecord


def log_admin_action(
    session: Session,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    client_ip: str | None = None,
) -> AdminAuditLog:
    """审查者自身受审查：所有管理写操作与审计查看都经此留痕。"""
    row = AdminAuditLog(
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        client_ip=client_ip,
    )
    session.add(row)
    session.flush()
    return row


def create_request_row(
    session: Session,
    *,
    api_key: ApiKey,
    model: str,
    stream: bool,
    prompt_messages: list,
    queued_at: datetime,
    enable_web_search: bool = True,
    thinking_effort: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> RequestRecord:
    """建请求审计行（status=queued），持有人/标签从 Key 冗余快照。"""
    row = RequestRecord(
        api_key_id=api_key.id,
        holder_name=api_key.holder_name,
        key_label=api_key.label,
        model=model,
        stream=stream,
        enable_web_search=enable_web_search,
        thinking_effort=thinking_effort,
        client_ip=client_ip,
        user_agent=user_agent,
        status=STATUS_QUEUED,
        prompt_messages=prompt_messages,
        queued_at=queued_at,
    )
    session.add(row)
    session.flush()
    return row


def bulk_insert_events(
    session: Session,
    request_id: UUID,
    rows: Iterable[tuple[int, str, dict]],
) -> None:
    """批量落事件行：rows 为 (seq, event_type, payload)。"""
    session.add_all(
        [
            EventRecord(request_id=request_id, seq=seq, event_type=event_type, payload=payload)
            for seq, event_type, payload in rows
        ]
    )
    session.flush()


def insert_usage_record(
    session: Session,
    *,
    request_id: UUID,
    step: int,
    complete: bool,
    pricing_snapshot: dict,
    created_at: datetime,
    cost_cents: Decimal | str = Decimal("0"),
    search_count: int = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> UsageRecord:
    row = UsageRecord(
        request_id=request_id,
        step=step,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        complete=complete,
        search_count=search_count,
        pricing_snapshot=pricing_snapshot,
        cost_cents=Decimal(str(cost_cents)),
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    return row
