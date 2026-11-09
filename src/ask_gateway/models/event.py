"""事件溯源表：一次问答 N 行，ORDER BY seq 可无损重放完整内部链路。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ask_gateway.db.base import Base, JSONType

# sqlite 仅对精确的 INTEGER PRIMARY KEY 自增，PG 上仍是 BIGINT
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("request_id", "seq", name="uq_events_request_seq"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("requests.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # DSH 原始 SessionEvent 类型，或适配层投影类型 thinking/text
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 完整原始事件内容：thinking 文本、web_search query 与结果摘要均在内
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped[RequestRecord] = relationship(back_populates="events")  # noqa: F821
