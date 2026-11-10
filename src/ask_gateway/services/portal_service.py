"""用户门户服务：持有人视角的只读消耗查询。

API Key（sk-ask-）即登录凭证：所有查询强制按 Key 所属持有人过滤，
与管理台共享查询核心（admin_service.query_requests / usage_summary），
但不产生管理审计留痕——门户查看自身数据不属于管理操作。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.request import RequestRecord
from ask_gateway.services.admin_service import (
    RequestFilters,
    query_requests,
    usage_summary,
)
from ask_gateway.services.errors import RequestNotFoundError


def profile(session: Session, api_key: ApiKey) -> dict:
    """当前 Key 与同持有人全部 Key 的只读概览。"""
    keys = list(
        session.scalars(
            select(ApiKey)
            .where(ApiKey.holder_name == api_key.holder_name)
            .order_by(ApiKey.created_at)
        )
    )
    return {
        "holder_name": api_key.holder_name,
        "keys": [
            {
                "id": str(k.id),
                "key_prefix": k.key_prefix,
                "label": k.label,
                "status": k.status,
                "disabled": k.disabled,
                "expires_at": k.expires_at,
                "rpm_limit": k.rpm_limit,
                "daily_budget_cents": k.daily_budget_cents,
                "max_concurrency": k.max_concurrency,
                "last_used_at": k.last_used_at,
                "created_at": k.created_at,
            }
            for k in keys
        ],
    }


def my_requests(
    session: Session,
    api_key: ApiKey,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[list[RequestRecord], int]:
    """持有人全部 Key 的请求（强制 holder 过滤）。"""
    filters = RequestFilters(
        holder_name=api_key.holder_name,
        key_id=None,
        status=status,
        date_from=date_from,
        date_to=date_to,
        keyword=None,
    )
    return query_requests(session, filters, page, page_size)


def my_request_row(
    session: Session, api_key: ApiKey, request_id: UUID
) -> RequestRecord:
    row = session.scalar(
        select(RequestRecord).where(RequestRecord.request_id == request_id)
    )
    if row is None or row.holder_name != api_key.holder_name:
        # 不存在与无权限同响应，避免探测他人请求 id
        raise RequestNotFoundError(str(request_id))
    return row


def my_usage_summary(
    session: Session,
    api_key: ApiKey,
    group_by: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list:
    conditions = [RequestRecord.holder_name == api_key.holder_name]
    if date_from is not None:
        conditions.append(RequestRecord.queued_at >= date_from)
    if date_to is not None:
        conditions.append(RequestRecord.queued_at <= date_to)
    return usage_summary(session, group_by, date_from, date_to, extra_conditions=conditions)


def my_dashboard(session: Session, api_key: ApiKey) -> dict:
    holder = api_key.holder_name
    total = session.scalar(
        select(func.count()).select_from(RequestRecord).where(
            RequestRecord.holder_name == holder
        )
    ) or 0
    succeeded = session.scalar(
        select(func.count()).select_from(RequestRecord).where(
            RequestRecord.holder_name == holder,
            RequestRecord.status == "succeeded",
        )
    ) or 0
    cost = session.scalar(
        select(func.coalesce(func.sum(RequestRecord.total_cost_cents), 0)).where(
            RequestRecord.holder_name == holder
        )
    ) or 0.0
    tokens_in = session.scalar(
        select(func.coalesce(func.sum(RequestRecord.total_input_tokens), 0)).where(
            RequestRecord.holder_name == holder
        )
    ) or 0
    tokens_out = session.scalar(
        select(func.coalesce(func.sum(RequestRecord.total_output_tokens), 0)).where(
            RequestRecord.holder_name == holder
        )
    ) or 0
    return {
        "total_requests": total,
        "succeeded": succeeded,
        "success_rate": (succeeded / total) if total else 0.0,
        "total_cost_cents": float(cost),
        "total_input_tokens": int(tokens_in),
        "total_output_tokens": int(tokens_out),
    }
