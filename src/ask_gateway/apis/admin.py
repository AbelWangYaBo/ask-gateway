"""管理面 REST 薄路由：X-Admin-Token 鉴权，参数解析与 schema 序列化。

业务逻辑全部在 services/admin_service；写操作与审计查看的留痕也在服务层完成。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ask_gateway.db.session import session_scope
from ask_gateway.models.admin import AdminSession
from ask_gateway.schemas.admin import (
    AuditLogOut,
    AuditLogPage,
    DashboardOut,
    KeyCreatedOut,
    KeyCreateRequest,
    KeyOut,
    LoginRequest,
    LoginResponse,
    PricingCreateRequest,
    PricingRuleOut,
    QuotaUpdateRequest,
    RequestDetailOut,
    RequestListItem,
    RequestPage,
    UsageChart,
    UsageChartSeries,
    UsageSummaryOut,
    UsageSummaryRow,
)
from ask_gateway.services import admin_auth, admin_service
from ask_gateway.services.admin_service import RequestFilters
from ask_gateway.services.errors import AuthError

router = APIRouter()


@dataclass(frozen=True, slots=True)
class AdminConfig:
    """登录口令与 TTL，由应用工厂从 Settings 注入。"""

    admin_password: str
    session_ttl_hours: int


def require_admin(
    x_admin_token: str | None = Header(default=None),
    session: Session = Depends(session_scope),
) -> AdminSession:
    row = admin_auth.authenticate(session, x_admin_token or "")
    if row is None:
        raise AuthError("unauthorized", "admin token missing or expired")
    return row


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---- 认证 ----


@router.post("/admin/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> LoginResponse:
    config: AdminConfig = request.app.state.admin_config
    token = admin_auth.login(
        session,
        payload.password,
        config.admin_password,
        ttl_hours=config.session_ttl_hours,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session_row = admin_auth.authenticate(session, token)
    assert session_row is not None
    return LoginResponse(token=token, expires_at=session_row.expires_at)


# ---- Key 管理 ----


@router.get(
    "/admin/keys",
    response_model=list[KeyOut],
    dependencies=[Depends(require_admin)],
)
def list_keys(session: Session = Depends(session_scope)) -> list[KeyOut]:
    return [KeyOut.model_validate(row) for row in admin_service.list_keys(session)]


@router.post(
    "/admin/keys",
    response_model=KeyCreatedOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_key(
    payload: KeyCreateRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> KeyCreatedOut:
    row, plaintext = admin_service.create_key(session, payload, _client_ip(request))
    out = KeyCreatedOut.model_validate(row)
    out.plaintext = plaintext
    return out


@router.post(
    "/admin/keys/{key_id}/revoke",
    response_model=KeyOut,
    dependencies=[Depends(require_admin)],
)
def revoke_key(
    key_id: UUID,
    request: Request,
    session: Session = Depends(session_scope),
) -> KeyOut:
    row = admin_service.revoke_key(session, key_id, _client_ip(request))
    return KeyOut.model_validate(row)


@router.patch(
    "/admin/keys/{key_id}",
    response_model=KeyOut,
    dependencies=[Depends(require_admin)],
)
def update_key_quota(
    key_id: UUID,
    payload: QuotaUpdateRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> KeyOut:
    row = admin_service.update_key_quota(
        session, key_id, payload, _client_ip(request)
    )
    return KeyOut.model_validate(row)


# ---- 审计检索 ----


@router.get(
    "/admin/requests",
    response_model=RequestPage,
    dependencies=[Depends(require_admin)],
)
def list_requests(
    request: Request,
    holder_name: str | None = None,
    key_id: UUID | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(session_scope),
) -> RequestPage:
    filters = RequestFilters(
        holder_name=holder_name,
        key_id=key_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
    )
    rows, total = admin_service.list_requests(
        session, filters, page, page_size, _client_ip(request)
    )
    return RequestPage(
        items=[RequestListItem.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/admin/requests/{request_id}",
    response_model=RequestDetailOut,
    dependencies=[Depends(require_admin)],
)
def get_request(
    request_id: UUID,
    request: Request,
    session: Session = Depends(session_scope),
) -> RequestDetailOut:
    row = admin_service.get_request(session, request_id, _client_ip(request))
    detail = RequestDetailOut.model_validate(row)
    detail.thinking = admin_service.thinking_text(row)
    return detail


@router.get(
    "/admin/requests/{request_id}/export",
    dependencies=[Depends(require_admin)],
)
def export_request(
    request_id: UUID,
    request: Request,
    session: Session = Depends(session_scope),
) -> JSONResponse:
    payload = admin_service.export_request(session, request_id, _client_ip(request))
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="ask-gateway-request-{request_id}.json"'
            )
        },
    )


# ---- 费用汇总 ----


@router.get(
    "/admin/usage/summary",
    response_model=UsageSummaryOut,
    dependencies=[Depends(require_admin)],
)
def usage_summary(
    group_by: Literal["holder", "key", "model", "day"] = "day",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    session: Session = Depends(session_scope),
) -> UsageSummaryOut:
    rows = admin_service.usage_summary(session, group_by, date_from, date_to)
    return UsageSummaryOut(
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
        rows=[
            UsageSummaryRow(
                group=row.group,
                group_label=row.group_label,
                request_count=row.request_count,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                search_count=row.search_count,
                cost_cents=row.cost_cents,
            )
            for row in rows
        ],
        chart=UsageChart(
            categories=[row.group_label for row in rows],
            series=[
                UsageChartSeries(
                    name="费用(分)",
                    data=[float(row.cost_cents) for row in rows],
                ),
                UsageChartSeries(
                    name="请求数",
                    data=[float(row.request_count) for row in rows],
                ),
                UsageChartSeries(
                    name="搜索次数",
                    data=[float(row.search_count) for row in rows],
                ),
            ],
        ),
    )


# ---- 定价 ----


@router.get(
    "/admin/pricing",
    response_model=list[PricingRuleOut],
    dependencies=[Depends(require_admin)],
)
def list_pricing(session: Session = Depends(session_scope)) -> list[PricingRuleOut]:
    return [
        PricingRuleOut.model_validate(row) for row in admin_service.list_pricing(session)
    ]


@router.put(
    "/admin/pricing",
    response_model=PricingRuleOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_pricing_rule(
    payload: PricingCreateRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> PricingRuleOut:
    row = admin_service.create_pricing_rule(session, payload, _client_ip(request))
    return PricingRuleOut.model_validate(row)


# ---- 管理审计日志 ----


@router.get(
    "/admin/audit-logs",
    response_model=AuditLogPage,
    dependencies=[Depends(require_admin)],
)
def list_audit_logs(
    action: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(session_scope),
) -> AuditLogPage:
    rows, total = admin_service.list_audit_logs(session, action, page, page_size)
    return AuditLogPage(
        items=[AuditLogOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---- 看板 ----


@router.get(
    "/admin/dashboard",
    response_model=DashboardOut,
    dependencies=[Depends(require_admin)],
)
def dashboard(session: Session = Depends(session_scope)) -> DashboardOut:
    return DashboardOut(**dataclasses.asdict(admin_service.dashboard(session)))
