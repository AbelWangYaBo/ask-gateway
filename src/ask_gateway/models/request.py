"""每次问答一行的审计主表。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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

# 请求状态机
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CLIENT_DISCONNECTED = "client_disconnected"
STATUS_RATE_LIMITED = "rate_limited"


class RequestRecord(Base, TimestampMixin):
    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 对外暴露的请求 ID，写入响应头 X-Request-Id
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=False, index=True, default=uuid.uuid4
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("api_keys.id"), nullable=False, index=True
    )
    # 冗余快照：Key 吊销/改名后历史归因仍可读
    holder_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    key_label: Mapped[str] = mapped_column(String(128), nullable=False)

    model: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enable_web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    thinking_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)

    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_QUEUED, index=True)

    # OpenAI messages 原样保真
    prompt_messages: Mapped[list] = mapped_column(JSONType, nullable=False)
    final_answer: Mapped[str | None] = mapped_column(String, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    dsh_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    upstream_key_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 汇总冗余，列表页免聚合
    total_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    search_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_cents: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, default=Decimal(0)
    )

    api_key: Mapped[ApiKey] = relationship(back_populates="requests")  # noqa: F821
    events: Mapped[list[EventRecord]] = relationship(  # noqa: F821
        back_populates="request", order_by="EventRecord.seq", cascade="all, delete-orphan"
    )
    usage_records: Mapped[list[UsageRecord]] = relationship(  # noqa: F821
        back_populates="request", order_by="UsageRecord.step", cascade="all, delete-orphan"
    )
