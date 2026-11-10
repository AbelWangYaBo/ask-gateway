"""本平台 API Key 的签发、鉴权、吊销与配额维护。

明文 Key 只在 create_api_key 的返回值里出现一次；数据库只存 SHA-256 hex。
Key 只吊销不删除，历史请求的 FK 与冗余快照保持可读。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import ApiKey
from ask_gateway.services.errors import KeyNotFoundError
from ask_gateway.services.security import (
    generate_api_key,
    hash_key,
    key_prefix_of,
)

_UNSET = object()


def create_api_key(
    session: Session,
    holder_name: str,
    label: str,
    *,
    rpm_limit: int | None = None,
    daily_budget_cents: int | None = None,
    max_concurrency: int | None = None,
) -> tuple[ApiKey, str]:
    """签发新 Key，返回 (ORM 行, 明文)。明文仅此次返回。"""
    plaintext = generate_api_key()
    key_row = ApiKey(
        key_prefix=key_prefix_of(plaintext),
        key_hash=hash_key(plaintext),
        holder_name=holder_name,
        label=label,
        rpm_limit=rpm_limit,
        daily_budget_cents=daily_budget_cents,
        max_concurrency=max_concurrency,
    )
    session.add(key_row)
    session.flush()
    return key_row, plaintext


def authenticate(session: Session, plaintext: str) -> ApiKey | None:
    """按明文 Key 找可用行。

    拒绝条件：不存在、已吊销、手动停用（disabled）、已到期（expires_at 早于当前）。
    expires_at 为 NULL 表示永不过期；改回 NULL/未来时间即恢复。
    """
    if not plaintext:
        return None
    row = session.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == hash_key(plaintext),
            ApiKey.status == "active",
        )
    )
    if row is None:
        return None
    if row.disabled:
        return None
    if row.expires_at is not None:
        # sqlite 读回 naive（UTC 写入），PG 带 tz：统一按 UTC 比较
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if datetime.now(UTC) >= expires:
            return None
    return row


def revoke_key(session: Session, key_id: uuid.UUID) -> ApiKey:
    key_row = session.get(ApiKey, key_id)
    if key_row is None:
        raise KeyNotFoundError()
    if key_row.status != "revoked":
        key_row.status = "revoked"
        key_row.revoked_at = datetime.now(UTC)
        session.flush()
    return key_row


def update_quota(
    session: Session,
    key_id: uuid.UUID,
    *,
    rpm_limit: Any = _UNSET,
    daily_budget_cents: Any = _UNSET,
    max_concurrency: Any = _UNSET,
    disabled: Any = _UNSET,
    expires_at: Any = _UNSET,
) -> ApiKey:
    """更新配额/启停/到期字段。未传的字段保持不动；显式传 None 表示清除。"""
    key_row = session.get(ApiKey, key_id)
    if key_row is None:
        raise KeyNotFoundError()
    if rpm_limit is not _UNSET:
        key_row.rpm_limit = rpm_limit
    if daily_budget_cents is not _UNSET:
        key_row.daily_budget_cents = daily_budget_cents
    if max_concurrency is not _UNSET:
        key_row.max_concurrency = max_concurrency
    if disabled is not _UNSET:
        key_row.disabled = bool(disabled)
    if expires_at is not _UNSET:
        key_row.expires_at = expires_at
    session.flush()
    return key_row


def touch_last_used(
    session: Session, key_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    key_row = session.get(ApiKey, key_id)
    if key_row is not None:
        key_row.last_used_at = now or datetime.now(UTC)
        session.flush()


def list_keys(session: Session, *, include_revoked: bool = True) -> list[ApiKey]:
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
    if not include_revoked:
        stmt = stmt.where(ApiKey.status == "active")
    return list(session.scalars(stmt).all())
