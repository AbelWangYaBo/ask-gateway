"""本平台签发的 API Key 与上游 DeepSeek Key 池元数据。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ask_gateway.db.base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # SHA-256 hex；明文只在创建响应里出现一次
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    holder_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)

    # NULL 表示用全局默认配额
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_budget_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requests: Mapped[list[RequestRecord]] = relationship(  # noqa: F821
        back_populates="api_key"
    )


class UpstreamKey(Base, TimestampMixin):
    """上游 Key 池元数据。明文密钥只存在于环境变量，表里只存变量引用。"""

    __tablename__ = "upstream_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    env_var_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
