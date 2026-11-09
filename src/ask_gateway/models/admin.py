"""管理员登录态与管理动作自审计。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ask_gateway.db.base import Base, JSONType, TimestampMixin

# sqlite 仅对精确的 INTEGER PRIMARY KEY 自增，PG 上仍是 BIGINT
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminAuditLog(Base, TimestampMixin):
    """审查者自身受审查：所有管理写操作与审计查看/导出都留痕。"""

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
