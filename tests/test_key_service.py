"""Key 服务：创建一次性明文、hash 鉴权、吊销、配额更新、last_used。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import ApiKey
from ask_gateway.services.errors import KeyNotFoundError
from ask_gateway.services.key_service import (
    authenticate,
    create_api_key,
    revoke_key,
    touch_last_used,
    update_quota,
)


def test_create_returns_plaintext_once_and_persists_hash(db_session: Session) -> None:
    key_row, plaintext = create_api_key(
        db_session, holder_name="用户A", label="个人 Key"
    )
    db_session.commit()

    assert plaintext.startswith("sk-ask-")
    assert key_row.key_hash not in plaintext
    assert key_row.status == "active"
    assert len(key_row.key_prefix) == 16
    assert plaintext.startswith(key_row.key_prefix)
    # 库里没有明文
    rows = db_session.scalars(select(ApiKey)).all()
    assert all(plaintext not in (r.key_hash, r.key_prefix) for r in rows)


def test_authenticate_active_key(db_session: Session) -> None:
    key_row, plaintext = create_api_key(db_session, "用户A", "系统1")
    db_session.commit()

    found = authenticate(db_session, plaintext)
    assert found is not None
    assert found.id == key_row.id


def test_authenticate_unknown_and_garbage(db_session: Session) -> None:
    create_api_key(db_session, "用户A", "个人")
    db_session.commit()
    assert authenticate(db_session, "sk-ask-" + "0" * 32) is None
    assert authenticate(db_session, "not-a-key") is None


def test_revoked_key_cannot_authenticate(db_session: Session) -> None:
    key_row, plaintext = create_api_key(db_session, "用户A", "个人")
    db_session.commit()

    revoked = revoke_key(db_session, key_row.id)
    db_session.commit()
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
    assert authenticate(db_session, plaintext) is None
    # 行仍然保留（只吊销不删除）
    assert db_session.get(ApiKey, key_row.id) is not None


def test_revoke_unknown_raises(db_session: Session) -> None:
    import uuid

    with pytest.raises(KeyNotFoundError):
        revoke_key(db_session, uuid.uuid4())


def test_update_quota_partial_and_clear(db_session: Session) -> None:
    key_row, _ = create_api_key(db_session, "用户A", "个人")
    db_session.commit()

    update_quota(db_session, key_row.id, rpm_limit=60, max_concurrency=8)
    db_session.commit()
    fetched = db_session.get(ApiKey, key_row.id)
    assert fetched is not None
    assert fetched.rpm_limit == 60
    assert fetched.max_concurrency == 8
    assert fetched.daily_budget_cents is None  # 未传不动

    update_quota(db_session, rpm_limit=None, key_id=key_row.id)
    db_session.commit()
    fetched = db_session.get(ApiKey, key_row.id)
    assert fetched is not None
    assert fetched.rpm_limit is None  # 显式传 None 清除，回落默认


def test_touch_last_used(db_session: Session) -> None:
    key_row, _ = create_api_key(db_session, "用户A", "个人")
    db_session.commit()
    assert key_row.last_used_at is None

    marker = datetime(2030, 1, 1, tzinfo=UTC)
    touch_last_used(db_session, key_row.id, now=marker)
    db_session.commit()

    fetched = db_session.get(ApiKey, key_row.id)
    assert fetched is not None
    assert fetched.last_used_at == marker
