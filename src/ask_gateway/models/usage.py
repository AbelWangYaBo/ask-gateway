"""step 粒度 usage 计量与可配置峰谷定价规则。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ask_gateway.db.base import Base, JSONType, TimestampMixin

# sqlite 仅对精确的 INTEGER PRIMARY KEY 自增，PG 上仍是 BIGINT
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class UsageRecord(Base):
    """每个 step/end 一行。complete=FALSE 表示 DSH 口径下 usage 不完整，不做伪造合计。"""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("requests.id"), nullable=False, index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    search_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 计费时生效规则的快照，规则后续变更不影响历史账单
    pricing_snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    cost_cents: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal(0))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    request: Mapped[RequestRecord] = relationship(back_populates="usage_records")  # noqa: F821


class PricingRule(Base, TimestampMixin):
    __tablename__ = "pricing_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # NULL 表示全时段；否则为北京时区小时区间 [peak_start_hour, peak_end_hour)
    peak_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekdays_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 生效截止（不含）；NULL = 长期有效。与 effective_from 构成生效周期，
    # 支持提前录入未来调价/临时优惠，到点自动切换或回落
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 权重：同时刻多条规则匹配时大者胜（常规价 0、临时优惠 1，优惠期整体覆盖常规）
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 单价：每百万 token 的人民币分；搜索为每千次的分
    input_price_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    output_price_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    reasoning_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    cache_read_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    cache_write_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    search_price_per_k: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
