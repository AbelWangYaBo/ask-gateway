"""本平台签发的 API Key 与上游 DeepSeek Key 池元数据。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid
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

    # 手动启停开关（区别于吊销终态：可反复切换，不删除历史）
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 到期时间：NULL = 永不过期；到期后拒绝鉴权，改回 NULL/未来时间即恢复
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    """上游 DeepSeek Key 池：AES-GCM 密文入库，指纹与 preview 供明文比对。

    池组目录名由 id 派生（upstream-<hex8>），新增/删除 Key 即时生效不重启。
    """

    __tablename__ = "upstream_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    # AES-GCM 密文（base64(nonce+ciphertext)），解密只发生在注入 DSH 子进程前
    key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 前 16 hex：创建查重 + 与上游平台侧肉眼/脚本比对
    key_fingerprint: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    # 展示形态（sk-abc1…wxyz）
    key_preview: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    @property
    def pool_label(self) -> str:
        return f"upstream-{self.id.hex[:8]}"

    @staticmethod
    def pool_label_of(key_id: uuid.UUID) -> str:
        return f"upstream-{key_id.hex[:8]}"
