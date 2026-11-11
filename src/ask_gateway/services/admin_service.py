"""管理面业务逻辑：Key 管理、审计检索、费用汇总、定价、看板。

路由只做参数解析与 schema 序列化；查询/写入与管理自审计留痕在此组合。
所有写操作与审计查看/导出都经 audit_service.log_admin_action 留痕。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ask_gateway.billing.pricing import assert_no_active_overlap
from ask_gateway.models.admin import AdminAuditLog
from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.request import STATUS_SUCCEEDED, RequestRecord
from ask_gateway.models.usage import PricingRule
from ask_gateway.schemas.admin import (
    KeyCreateRequest,
    PricingCreateRequest,
    PricingUpdateRequest,
    QuotaUpdateRequest,
)
from ask_gateway.services import audit_service, key_service
from ask_gateway.services.errors import PricingNotFoundError, RequestNotFoundError
from ask_gateway.services.quota_service import beijing_day_bounds_utc


@dataclass(frozen=True, slots=True)
class RequestFilters:
    holder_name: str | None
    key_id: UUID | None
    status: str | None
    date_from: datetime | None
    date_to: datetime | None
    keyword: str | None
    upstream_label: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryRow:
    group: str
    group_label: str
    request_count: int
    input_tokens: int
    output_tokens: int
    search_count: int
    cost_cents: Decimal


@dataclass(frozen=True, slots=True)
class DashboardData:
    total_requests: int
    succeeded: int
    success_rate: float
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    today_cost_cents: Decimal
    active_keys: int
    status_distribution: dict[str, int]


# ---- Key 管理 ----


def list_keys(session: Session) -> list[ApiKey]:
    return key_service.list_keys(session, include_revoked=True)


def create_key(
    session: Session, payload: KeyCreateRequest, client_ip: str | None
) -> tuple[ApiKey, str]:
    row, plaintext = key_service.create_api_key(
        session,
        payload.holder_name,
        payload.label,
        rpm_limit=payload.rpm_limit,
        daily_budget_cents=payload.daily_budget_cents,
        max_concurrency=payload.max_concurrency,
    )
    audit_service.log_admin_action(
        session,
        "key_create",
        target_type="api_key",
        target_id=str(row.id),
        detail={
            "holder_name": row.holder_name,
            "label": row.label,
            "rpm_limit": row.rpm_limit,
            "daily_budget_cents": row.daily_budget_cents,
            "max_concurrency": row.max_concurrency,
        },
        client_ip=client_ip,
    )
    return row, plaintext


def revoke_key(session: Session, key_id: UUID, client_ip: str | None) -> ApiKey:
    row = key_service.revoke_key(session, key_id)
    audit_service.log_admin_action(
        session,
        "key_revoke",
        target_type="api_key",
        target_id=str(row.id),
        detail={"holder_name": row.holder_name, "label": row.label},
        client_ip=client_ip,
    )
    return row


def update_key_quota(
    session: Session,
    key_id: UUID,
    payload: QuotaUpdateRequest,
    client_ip: str | None,
) -> ApiKey:
    changes = payload.model_dump(exclude_unset=True)
    row = key_service.update_quota(session, key_id, **changes)
    # 审计 JSON 列不能直接装 datetime：统一 isoformat
    audit_detail = {
        k: v.isoformat() if isinstance(v, datetime) else v for k, v in changes.items()
    }
    audit_service.log_admin_action(
        session,
        "key_update",
        target_type="api_key",
        target_id=str(row.id),
        detail=audit_detail,
        client_ip=client_ip,
    )
    return row


# ---- 审计检索 ----


def _apply_filters(stmt, filters: RequestFilters):
    if filters.holder_name:
        stmt = stmt.where(RequestRecord.holder_name == filters.holder_name)
    if filters.key_id is not None:
        stmt = stmt.where(RequestRecord.api_key_id == filters.key_id)
    if filters.status:
        stmt = stmt.where(RequestRecord.status == filters.status)
    if filters.date_from is not None:
        stmt = stmt.where(RequestRecord.queued_at >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(RequestRecord.queued_at <= filters.date_to)
    if filters.upstream_label:
        stmt = stmt.where(RequestRecord.upstream_key_label == filters.upstream_label)
    if filters.keyword:
        # 跨 PG/sqlite：显式 lower+like，不依赖方言 ILIKE 编译差异
        needle = f"%{filters.keyword.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(cast(RequestRecord.prompt_messages, String)).like(needle),
                func.lower(RequestRecord.final_answer).like(needle),
            )
        )
    return stmt


def _filters_detail(filters: RequestFilters, page: int, page_size: int, total: int) -> dict:
    return {
        "holder_name": filters.holder_name,
        "key_id": str(filters.key_id) if filters.key_id is not None else None,
        "status": filters.status,
        "date_from": filters.date_from.isoformat() if filters.date_from else None,
        "date_to": filters.date_to.isoformat() if filters.date_to else None,
        "keyword": filters.keyword,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def query_requests(
    session: Session,
    filters: RequestFilters,
    page: int,
    page_size: int,
) -> tuple[list[RequestRecord], int]:
    """请求检索核心（无留痕）：管理端与用户门户共用。"""
    base = _apply_filters(select(RequestRecord), filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        session.scalars(
            base.order_by(RequestRecord.queued_at.desc(), RequestRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, total


def list_requests(
    session: Session,
    filters: RequestFilters,
    page: int,
    page_size: int,
    client_ip: str | None,
) -> tuple[list[RequestRecord], int]:
    rows, total = query_requests(session, filters, page, page_size)
    audit_service.log_admin_action(
        session,
        "audit_view",
        target_type="request_list",
        detail=_filters_detail(filters, page, page_size, total),
        client_ip=client_ip,
    )
    return rows, total


def get_request(
    session: Session, request_id: UUID, client_ip: str | None
) -> RequestRecord:
    row = session.scalar(
        select(RequestRecord).where(RequestRecord.request_id == request_id)
    )
    if row is None:
        raise RequestNotFoundError(str(request_id))
    audit_service.log_admin_action(
        session,
        "audit_view",
        target_type="request",
        target_id=str(request_id),
        client_ip=client_ip,
    )
    return row


def thinking_text(row: RequestRecord) -> str:
    return "".join(
        event.payload.get("text", "")
        for event in row.events
        if event.event_type == "thinking"
    )


def export_request(
    session: Session, request_id: UUID, client_ip: str | None
) -> dict:
    """单请求完整导出（全原始字段，全 JSON 基元，可直接 json.dumps）。"""
    row = session.scalar(
        select(RequestRecord).where(RequestRecord.request_id == request_id)
    )
    if row is None:
        raise RequestNotFoundError(str(request_id))
    key_row = session.get(ApiKey, row.api_key_id)
    audit_service.log_admin_action(
        session,
        "audit_export",
        target_type="request",
        target_id=str(request_id),
        client_ip=client_ip,
    )
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "request": {
            "id": str(row.id),
            "request_id": str(row.request_id),
            "api_key_id": str(row.api_key_id),
            "holder_name": row.holder_name,
            "key_label": row.key_label,
            "model": row.model,
            "stream": row.stream,
            "enable_web_search": row.enable_web_search,
            "thinking_effort": row.thinking_effort,
            "client_ip": row.client_ip,
            "user_agent": row.user_agent,
            "status": row.status,
            "prompt_messages": row.prompt_messages,
            "final_answer": row.final_answer,
            "finish_reason": row.finish_reason,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "dsh_session_id": row.dsh_session_id,
            "upstream_key_label": row.upstream_key_label,
            "queued_at": row.queued_at.isoformat() if row.queued_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "total_input_tokens": row.total_input_tokens,
            "total_output_tokens": row.total_output_tokens,
            "cache_read_tokens": row.cache_read_tokens,
            "cache_write_tokens": row.cache_write_tokens,
            "reasoning_tokens": row.reasoning_tokens,
            "search_count": row.search_count,
            "total_cost_cents": str(row.total_cost_cents),
        },
        "api_key": (
            None
            if key_row is None
            else {
                "id": str(key_row.id),
                "key_prefix": key_row.key_prefix,
                "holder_name": key_row.holder_name,
                "label": key_row.label,
                "status": key_row.status,
            }
        ),
        "events": [
            {
                "seq": event.seq,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in row.events
        ],
        "usage_records": [
            {
                "step": usage.step,
                "complete": usage.complete,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "search_count": usage.search_count,
                "pricing_snapshot": usage.pricing_snapshot,
                "cost_cents": str(usage.cost_cents),
                "created_at": usage.created_at.isoformat() if usage.created_at else None,
            }
            for usage in row.usage_records
        ],
    }


# ---- 费用汇总 ----

_GROUP_COLUMNS = {
    "holder": RequestRecord.holder_name,
    "key": RequestRecord.api_key_id,
    "model": RequestRecord.model,
    "day": func.date(RequestRecord.queued_at),
    "upstream_key": RequestRecord.upstream_key_label,
}


def usage_summary(
    session: Session,
    group_by: str,
    date_from: datetime | None,
    date_to: datetime | None,
    *,
    extra_conditions: list | None = None,
) -> list[SummaryRow]:
    group_col = _GROUP_COLUMNS[group_by]
    conditions = list(extra_conditions or [])
    if date_from is not None:
        conditions.append(RequestRecord.queued_at >= date_from)
    if date_to is not None:
        conditions.append(RequestRecord.queued_at <= date_to)

    key_labels: dict[UUID, ApiKey] = {
        row.id: row for row in session.scalars(select(ApiKey)).all()
    }
    from ask_gateway.models.api_key import UpstreamKey

    upstream_labels: dict[str, str] = {
        row.pool_label: row.label for row in session.scalars(select(UpstreamKey)).all()
    }

    result: list[SummaryRow] = []
    # 分组列是 str/UUID/date 的联合类型，SQLAlchemy 2.1 的 Row 泛型推断不出，显式 Any
    aggregated: Any = session.execute(
        select(
            group_col.label("grp"),
            func.count().label("request_count"),
            func.coalesce(func.sum(RequestRecord.total_input_tokens), 0),
            func.coalesce(func.sum(RequestRecord.total_output_tokens), 0),
            func.coalesce(func.sum(RequestRecord.search_count), 0),
            func.coalesce(func.sum(RequestRecord.total_cost_cents), 0),
        )
        .where(*conditions)
        .group_by(group_col)
        .order_by(group_col)
    ).all()
    for row in aggregated:
        grp = row[0]
        count, input_tokens, output_tokens, search_count, cost = row[1:]
        group_value = str(grp)
        if group_by == "key":
            key_row = key_labels.get(UUID(group_value))
            label = (
                f"{key_row.holder_name} / {key_row.label}"
                if key_row is not None
                else group_value
            )
        elif group_by == "upstream_key":
            label = upstream_labels.get(group_value, group_value)
        else:
            label = group_value
        result.append(
            SummaryRow(
                group=group_value,
                group_label=label,
                request_count=int(count),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                search_count=int(search_count),
                cost_cents=Decimal(str(cost)),
            )
        )
    return result


# ---- 定价 ----


def list_pricing(session: Session) -> list[PricingRule]:
    return list(
        session.scalars(
            select(PricingRule).order_by(
                PricingRule.model, PricingRule.effective_from.desc()
            )
        ).all()
    )


def create_pricing_rule(
    session: Session, payload: PricingCreateRequest, client_ip: str | None
) -> PricingRule:
    assert_no_active_overlap(
        session,
        payload.model,
        payload.peak_start_hour,
        payload.peak_end_hour,
        payload.weekdays_only,
        effective_from=payload.effective_from,
        priority=payload.priority,
    )
    rule = PricingRule(**payload.model_dump())
    session.add(rule)
    session.flush()
    audit_service.log_admin_action(
        session,
        "pricing_update",
        target_type="pricing_rule",
        target_id=str(rule.id),
        detail={"model": rule.model, "rule_name": rule.rule_name},
        client_ip=client_ip,
    )
    return rule


def update_pricing_rule(
    session: Session,
    rule_id: UUID,
    payload: PricingUpdateRequest,
    client_ip: str | None,
) -> PricingRule:
    """编辑规则：全量覆盖可变字段并支持启停。

    usage 落库时已冻结 pricing_snapshot，此处修改只影响之后的请求计费。
    """
    rule = session.get(PricingRule, rule_id)
    if rule is None:
        raise PricingNotFoundError(str(rule_id))
    # 停用不产生新的 active 冲突，跳过窗口查重（例如停用同时改窗口收尾）
    if payload.is_active:
        assert_no_active_overlap(
            session,
            payload.model,
            payload.peak_start_hour,
            payload.peak_end_hour,
            payload.weekdays_only,
            exclude_id=rule.id,
            effective_from=payload.effective_from,
            priority=payload.priority,
        )
    for field, value in payload.model_dump().items():
        setattr(rule, field, value)
    session.flush()
    audit_service.log_admin_action(
        session,
        "pricing_update",
        target_type="pricing_rule",
        target_id=str(rule.id),
        detail={"model": rule.model, "rule_name": rule.rule_name, "op": "edit"},
        client_ip=client_ip,
    )
    return rule


# ---- 管理审计日志 ----


def list_audit_logs(
    session: Session,
    action: str | None,
    page: int,
    page_size: int,
) -> tuple[list[AdminAuditLog], int]:
    base = select(AdminAuditLog)
    if action:
        base = base.where(AdminAuditLog.action == action)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        session.scalars(
            base.order_by(AdminAuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, total


# ---- 看板 ----


def _percentile(sorted_values: list[float], percentile: int) -> int | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0])
    rank = (len(sorted_values) - 1) * (percentile / 100)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(sorted_values[low])
    fraction = rank - low
    interpolated = sorted_values[low] + (
        sorted_values[high] - sorted_values[low]
    ) * fraction
    return round(interpolated)


def dashboard(session: Session) -> DashboardData:
    total = session.scalar(select(func.count()).select_from(RequestRecord)) or 0
    succeeded = session.scalar(
        select(func.count())
        .select_from(RequestRecord)
        .where(RequestRecord.status == STATUS_SUCCEEDED)
    ) or 0
    status_distribution = {
        status: int(count)
        for status, count in session.execute(
            select(RequestRecord.status, func.count()).group_by(RequestRecord.status)
        ).all()
    }

    latencies = sorted(
        (finished - started).total_seconds() * 1000.0
        for started, finished in session.execute(
            select(RequestRecord.started_at, RequestRecord.finished_at).where(
                RequestRecord.started_at.is_not(None),
                RequestRecord.finished_at.is_not(None),
            )
        ).all()
        if started is not None and finished is not None
    )

    start_utc, end_utc = beijing_day_bounds_utc(datetime.now(UTC))
    today_cost = session.scalar(
        select(func.coalesce(func.sum(RequestRecord.total_cost_cents), 0)).where(
            RequestRecord.created_at >= start_utc,
            RequestRecord.created_at < end_utc,
        )
    )
    active_keys = session.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.status == "active")
    ) or 0

    return DashboardData(
        total_requests=int(total),
        succeeded=int(succeeded),
        success_rate=round(int(succeeded) / int(total), 4) if total else 0.0,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        today_cost_cents=Decimal(str(today_cost)),
        active_keys=int(active_keys),
        status_distribution=status_distribution,
    )


# ---- Key 验证：输入明文，判定归属 ----


@dataclass(frozen=True, slots=True)
class KeyVerifyResult:
    kind: str  # "platform" | "upstream" | "unknown"
    key_id: str | None
    label: str | None
    holder_name: str | None
    preview: str | None
    status: str | None  # platform: active/revoked；upstream: enabled/disabled
    expires_at: datetime | None = None


def verify_key_plaintext(session: Session, box, plaintext: str) -> KeyVerifyResult:
    """按明文探测归属：平台 sk-ask- Key（SHA-256 全文匹配）优先，其次上游 Key（指纹）。"""
    from ask_gateway.models.api_key import UpstreamKey
    from ask_gateway.services.secret_box import SecretBox
    from ask_gateway.services.security import hash_key

    plaintext = plaintext.strip()
    if not plaintext:
        return KeyVerifyResult(kind="unknown", key_id=None, label=None, holder_name=None,
                               preview=None, status=None)

    key_hash = hash_key(plaintext)
    platform = session.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    if platform is not None:
        status = platform.status if platform.status != "active" else (
            "disabled" if platform.disabled else "active"
        )
        return KeyVerifyResult(
            kind="platform",
            key_id=str(platform.id),
            label=platform.label,
            holder_name=platform.holder_name,
            preview=platform.key_prefix,
            status=status,
            expires_at=platform.expires_at,
        )

    fingerprint = SecretBox.fingerprint(plaintext)
    upstream = session.scalar(
        select(UpstreamKey).where(UpstreamKey.key_fingerprint == fingerprint)
    )
    if upstream is not None:
        return KeyVerifyResult(
            kind="upstream",
            key_id=str(upstream.id),
            label=upstream.label,
            holder_name=None,
            preview=upstream.key_preview,
            status="enabled" if upstream.is_active else "disabled",
        )

    return KeyVerifyResult(kind="unknown", key_id=None, label=None, holder_name=None,
                           preview=SecretBox.preview(plaintext), status=None)
