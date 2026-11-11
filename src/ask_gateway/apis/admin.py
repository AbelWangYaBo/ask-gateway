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
from fastapi.responses import JSONResponse, Response
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
    KeyVerifyRequest,
    KeyVerifyResultOut,
    LoginRequest,
    LoginResponse,
    PricingCreateRequest,
    PricingRuleOut,
    PricingUpdateRequest,
    QuotaUpdateRequest,
    RequestDetailOut,
    RequestListItem,
    RequestPage,
    UpstreamKeyCreateRequest,
    UpstreamKeyOut,
    UpstreamKeyUpdateRequest,
    UsageChart,
    UsageChartSeries,
    UsageSummaryOut,
    UsageSummaryRow,
)
from ask_gateway.services import (
    admin_auth,
    admin_service,
    audit_service,
    upstream_key_service,
)
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


@router.post("/api/admin/auth/login", response_model=LoginResponse)
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
    "/api/admin/keys",
    response_model=list[KeyOut],
    dependencies=[Depends(require_admin)],
)
def list_keys(session: Session = Depends(session_scope)) -> list[KeyOut]:
    return [KeyOut.model_validate(row) for row in admin_service.list_keys(session)]


@router.post(
    "/api/admin/keys",
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
    "/api/admin/keys/{key_id}/revoke",
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
    "/api/admin/keys/{key_id}",
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


@router.post(
    "/api/admin/keys/verify",
    response_model=KeyVerifyResultOut,
    dependencies=[Depends(require_admin)],
)
def verify_key(
    payload: KeyVerifyRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> KeyVerifyResultOut:
    """输入明文 Key，判定归属（平台 sk-ask- / 上游 DeepSeek），返回匹配详情。"""
    control = getattr(request.app.state, "upstream_control", None)
    box = control.box if control is not None else None
    if box is None:
        from ask_gateway.services.secret_box import SecretBox

        box = SecretBox(b"v" * 32)  # 无上游管理面环境（轻量 CI）也能验平台 Key
    result = admin_service.verify_key_plaintext(session, box, payload.plaintext)
    audit_service.log_admin_action(
        session,
        "key_verify",
        target_type="key",
        detail={"kind": result.kind, "key_id": result.key_id},
        client_ip=_client_ip(request),
    )
    return KeyVerifyResultOut(
        kind=result.kind,
        key_id=result.key_id,
        label=result.label,
        holder_name=result.holder_name,
        preview=result.preview,
        status=result.status,
        expires_at=result.expires_at,
    )


# ---- 审计检索 ----


@router.get(
    "/api/admin/requests",
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
    upstream_key_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(session_scope),
) -> RequestPage:
    upstream_label = None
    if upstream_key_id is not None:
        from ask_gateway.models.api_key import UpstreamKey

        row = session.get(UpstreamKey, upstream_key_id)
        upstream_label = row.pool_label if row is not None else f"upstream-{upstream_key_id.hex[:8]}"
    filters = RequestFilters(
        holder_name=holder_name,
        key_id=key_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        upstream_label=upstream_label,
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
    "/api/admin/requests/{request_id}",
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
    "/api/admin/requests/{request_id}/export",
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
    "/api/admin/usage/summary",
    response_model=UsageSummaryOut,
    dependencies=[Depends(require_admin)],
)
def usage_summary(
    group_by: Literal["holder", "key", "model", "day", "upstream_key"] = "day",
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
    "/api/admin/pricing",
    response_model=list[PricingRuleOut],
    dependencies=[Depends(require_admin)],
)
def list_pricing(session: Session = Depends(session_scope)) -> list[PricingRuleOut]:
    return [
        PricingRuleOut.model_validate(row) for row in admin_service.list_pricing(session)
    ]


@router.put(
    "/api/admin/pricing",
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


@router.put(
    "/api/admin/pricing/{rule_id}",
    response_model=PricingRuleOut,
    dependencies=[Depends(require_admin)],
)
def update_pricing_rule(
    rule_id: UUID,
    payload: PricingUpdateRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> PricingRuleOut:
    row = admin_service.update_pricing_rule(session, rule_id, payload, _client_ip(request))
    return PricingRuleOut.model_validate(row)


# ---- 管理审计日志 ----


@router.get(
    "/api/admin/audit-logs",
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
    "/api/admin/dashboard",
    response_model=DashboardOut,
    dependencies=[Depends(require_admin)],
)
def dashboard(session: Session = Depends(session_scope)) -> DashboardOut:
    return DashboardOut(**dataclasses.asdict(admin_service.dashboard(session)))


# ---- 上游 Key 管理（增删改即时生效，不重启服务） ----


def _reconcile_pool(request: Request) -> None:
    """CRUD 后按 DB 现状重配池分组；池未挂载（测试环境）时跳过。"""
    control = getattr(request.app.state, "upstream_control", None)
    pool = getattr(request.app.state, "pool", None)
    if control is None or pool is None:
        return
    control.reconcile_pool(pool)


@router.get(
    "/api/admin/upstream-keys",
    response_model=list[UpstreamKeyOut],
    dependencies=[Depends(require_admin)],
)
def list_upstream_keys(session: Session = Depends(session_scope)) -> list[UpstreamKeyOut]:
    return [UpstreamKeyOut.model_validate(r) for r in upstream_key_service.list_upstream_keys(session)]


@router.post(
    "/api/admin/upstream-keys",
    response_model=UpstreamKeyOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_upstream_key(
    payload: UpstreamKeyCreateRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> UpstreamKeyOut:
    control = request.app.state.upstream_control
    row = upstream_key_service.create_upstream_key(
        session,
        control.box,
        label=payload.label,
        plaintext=payload.plaintext,
        notes=payload.notes,
        client_ip=_client_ip(request),
    )
    session.commit()
    _reconcile_pool(request)
    return UpstreamKeyOut.model_validate(row)


@router.patch(
    "/api/admin/upstream-keys/{key_id}",
    response_model=UpstreamKeyOut,
    dependencies=[Depends(require_admin)],
)
def update_upstream_key(
    key_id: UUID,
    payload: UpstreamKeyUpdateRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> UpstreamKeyOut:
    row = upstream_key_service.update_upstream_key(
        session,
        key_id,
        label=payload.label,
        notes=payload.notes,
        is_active=payload.is_active,
        client_ip=_client_ip(request),
    )
    session.commit()
    _reconcile_pool(request)
    return UpstreamKeyOut.model_validate(row)


@router.delete(
    "/api/admin/upstream-keys/{key_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def delete_upstream_key(
    key_id: UUID,
    request: Request,
    session: Session = Depends(session_scope),
) -> Response:
    upstream_key_service.delete_upstream_key(session, key_id, client_ip=_client_ip(request))
    session.commit()
    _reconcile_pool(request)
    return Response(status_code=204)
