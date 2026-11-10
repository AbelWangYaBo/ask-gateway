"""用户门户 schema：持有人只读视图。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PortalKeyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key_prefix: str
    label: str
    status: str
    disabled: bool
    expires_at: datetime | None
    rpm_limit: int | None
    daily_budget_cents: int | None
    max_concurrency: int | None
    last_used_at: datetime | None
    created_at: datetime


class PortalProfileOut(BaseModel):
    holder_name: str
    keys: list[PortalKeyItem]


class PortalDashboardOut(BaseModel):
    total_requests: int
    succeeded: int
    success_rate: float
    total_cost_cents: float
    total_input_tokens: int
    total_output_tokens: int
