"""管理面 REST 的请求/响应模型。

独立于服务层与 ORM：不 import services/models，仅靠 from_attributes 做 ORM 行转换。
金额字段统一用 Decimal（序列化为字符串，避免浮点失真）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---- 认证 ----


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime


# ---- API Key ----


class KeyCreateRequest(BaseModel):
    holder_name: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)
    # NULL 表示回落全局默认配额
    rpm_limit: int | None = Field(default=None, ge=1)
    daily_budget_cents: int | None = Field(default=None, ge=0)
    max_concurrency: int | None = Field(default=None, ge=1)


class QuotaUpdateRequest(BaseModel):
    """全部可选：未传字段保持不动，显式传 null 表示清除并回落全局默认。"""

    rpm_limit: int | None = Field(default=None, ge=1)
    daily_budget_cents: int | None = Field(default=None, ge=0)
    max_concurrency: int | None = Field(default=None, ge=1)
    disabled: bool | None = None
    expires_at: datetime | None = None


class KeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key_prefix: str
    holder_name: str
    label: str
    status: str
    rpm_limit: int | None
    daily_budget_cents: int | None
    max_concurrency: int | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    disabled: bool = False
    expires_at: datetime | None = None


class KeyCreatedOut(KeyOut):
    """明文 Key 仅在创建响应里出现这一次；路由必须显式覆写 plaintext。"""

    plaintext: str = ""


# ---- 审计检索 ----


class RequestListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    api_key_id: UUID
    holder_name: str
    key_label: str
    model: str
    stream: bool
    status: str
    total_input_tokens: int | None
    total_output_tokens: int | None
    search_count: int
    total_cost_cents: Decimal
    error_code: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    event_type: str
    payload: dict
    created_at: datetime


class UsageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: int
    complete: bool
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    search_count: int
    pricing_snapshot: dict
    cost_cents: Decimal
    created_at: datetime


class RequestDetailOut(RequestListItem):
    enable_web_search: bool
    thinking_effort: str | None
    client_ip: str | None
    user_agent: str | None
    prompt_messages: list
    final_answer: str | None
    finish_reason: str | None
    error_message: str | None
    dsh_session_id: str | None
    upstream_key_label: str | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    thinking: str = ""
    events: list[EventOut] = Field(default_factory=list)
    usage_records: list[UsageOut] = Field(default_factory=list)


class RequestPage(BaseModel):
    items: list[RequestListItem]
    total: int
    page: int
    page_size: int


# ---- 费用汇总 ----


class UsageSummaryRow(BaseModel):
    group: str
    group_label: str
    request_count: int
    input_tokens: int
    output_tokens: int
    search_count: int
    cost_cents: Decimal


class UsageChartSeries(BaseModel):
    name: str
    data: list[float]


class UsageChart(BaseModel):
    categories: list[str]
    series: list[UsageChartSeries]


class UsageSummaryOut(BaseModel):
    group_by: str
    date_from: datetime | None
    date_to: datetime | None
    rows: list[UsageSummaryRow]
    chart: UsageChart


# ---- 定价 ----


class PricingCreateRequest(BaseModel):
    model: str = Field(min_length=1, max_length=64)
    rule_name: str = Field(min_length=1, max_length=128)
    # NULL 表示全时段；否则北京时区 [peak_start_hour, peak_end_hour)
    peak_start_hour: int | None = Field(default=None, ge=0, le=23)
    peak_end_hour: int | None = Field(default=None, ge=1, le=24)
    weekdays_only: bool = False
    effective_from: datetime
    # 生效周期（半开区间）；effective_to 为 NULL 表示长期有效
    effective_to: datetime | None = None
    # 权重：同时刻多条匹配时大者胜（常规价 0、临时优惠 1）
    priority: int = Field(default=0)
    input_price_per_mtok: Decimal = Field(ge=0)
    output_price_per_mtok: Decimal = Field(ge=0)
    reasoning_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    cache_read_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    cache_write_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    search_price_per_k: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def _check_window(self) -> PricingCreateRequest:
        start, end = self.peak_start_hour, self.peak_end_hour
        if (start is None) != (end is None):
            raise ValueError("peak_start_hour 与 peak_end_hour 必须同时提供或同时省略")
        if start is not None and end is not None and start >= end:
            raise ValueError("peak_start_hour 必须小于 peak_end_hour")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to 必须晚于 effective_from")
        return self


class PricingUpdateRequest(PricingCreateRequest):
    """编辑定价规则：全量字段 + 启停；usage 落库时已冻结快照，改规则不影响历史账单。"""

    is_active: bool = True


class PricingRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model: str
    rule_name: str
    peak_start_hour: int | None
    peak_end_hour: int | None
    weekdays_only: bool
    is_active: bool
    effective_from: datetime
    effective_to: datetime | None
    priority: int
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal
    reasoning_price_per_mtok: Decimal | None
    cache_read_price_per_mtok: Decimal | None
    cache_write_price_per_mtok: Decimal | None
    search_price_per_k: Decimal
    created_at: datetime


# ---- 管理审计日志 ----


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    target_type: str | None
    target_id: str | None
    detail: dict | None
    client_ip: str | None
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int


# ---- 看板 ----


class DashboardOut(BaseModel):
    total_requests: int
    succeeded: int
    success_rate: float
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    today_cost_cents: Decimal
    active_keys: int
    status_distribution: dict[str, int]


# ---- 上游 Key 管理 ----


class UpstreamKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    key_fingerprint: str
    key_preview: str
    is_active: bool
    notes: str | None
    created_at: datetime


class UpstreamKeyCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    plaintext: str = Field(min_length=8, max_length=512)
    notes: str | None = None


class UpstreamKeyUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None


class KeyVerifyRequest(BaseModel):
    plaintext: str = Field(min_length=1, max_length=512)


class KeyVerifyResultOut(BaseModel):
    kind: Literal["platform", "upstream", "unknown"]
    key_id: str | None
    label: str | None
    holder_name: str | None
    preview: str | None
    status: str | None
    expires_at: datetime | None = None
