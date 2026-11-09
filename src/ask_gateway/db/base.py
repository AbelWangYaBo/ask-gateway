"""SQLAlchemy 声明式基类与跨方言通用类型。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def json_dumps_utf8(value: Any) -> str:
    """JSON 落库不转义非 ASCII：中文按原字符存储，管理面关键词 LIKE 才能命中全文。

    通过 create_engine(json_serializer=...) 注入（SQLAlchemy 2.1 起类型级
    json_serializer 参数已移除）；PG JSONB 本就原生存 unicode，行为一致。
    """
    return json.dumps(value, ensure_ascii=False)


# PG 上用原生 JSONB，其他方言（测试用 sqlite）退回通用 JSON
JSONType = JSON().with_variant(JSONB(), "postgresql")

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
