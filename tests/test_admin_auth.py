"""管理员登录态认证与管理审计写入。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ask_gateway.models.admin import AdminAuditLog, AdminSession
from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.event import EventRecord
from ask_gateway.models.request import RequestRecord
from ask_gateway.models.usage import UsageRecord
from ask_gateway.services.admin_auth import (
    authenticate,
    login,
    logout,
    purge_expired,
)
from ask_gateway.services.audit_service import (
    bulk_insert_events,
    create_request_row,
    insert_usage_record,
    log_admin_action,
)
from ask_gateway.services.errors import AuthError
from ask_gateway.services.security import hash_token


def test_login_creates_session_and_audit_log(db_session: Session) -> None:
    token = login(
        db_session,
        "correct-horse",
        "correct-horse",
        ttl_hours=12,
        client_ip="10.0.0.1",
        user_agent="pytest",
    )
    db_session.commit()

    assert len(token) >= 32
    session_row = db_session.scalar(select(AdminSession))
    assert session_row is not None
    assert session_row.token_hash != token  # 库存哈希不存明文
    assert session_row.client_ip == "10.0.0.1"
    delta = session_row.expires_at - session_row.created_at
    assert timedelta(hours=11, minutes=59) < delta < timedelta(hours=12, minutes=1)

    log_row = db_session.scalar(select(AdminAuditLog))
    assert log_row is not None
    assert log_row.action == "login"
    assert log_row.client_ip == "10.0.0.1"


def test_login_wrong_password_raises_and_persists_nothing(db_session: Session) -> None:
    with pytest.raises(AuthError):
        login(db_session, "wrong", "correct-horse", ttl_hours=12)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(AdminSession)) == 0
    assert db_session.scalar(select(func.count()).select_from(AdminAuditLog)) == 0


def test_authenticate_token_lifecycle(db_session: Session) -> None:
    token = login(db_session, "pw", "pw", ttl_hours=12)
    db_session.commit()

    assert authenticate(db_session, token) is not None
    assert authenticate(db_session, "garbage-token") is None

    logout(db_session, token)
    db_session.commit()
    assert authenticate(db_session, token) is None


def test_expired_session_rejected(db_session: Session) -> None:
    token = login(db_session, "pw", "pw", ttl_hours=12)
    db_session.commit()

    session_row = authenticate(db_session, token)
    assert session_row is not None
    session_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert authenticate(db_session, token) is None


def test_purge_expired(db_session: Session) -> None:
    fresh = login(db_session, "pw", "pw", ttl_hours=12)
    stale = login(db_session, "pw", "pw", ttl_hours=12)
    db_session.commit()

    stale_row = db_session.scalar(
        select(AdminSession).where(AdminSession.token_hash == hash_token(stale))
    )
    assert stale_row is not None
    stale_row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    removed = purge_expired(db_session)
    db_session.commit()
    assert removed == 1
    assert authenticate(db_session, fresh) is not None


def test_log_admin_action(db_session: Session) -> None:
    log_admin_action(
        db_session,
        "key_create",
        target_type="api_key",
        target_id="abc",
        detail={"holder_name": "用户A"},
        client_ip="127.0.0.1",
    )
    db_session.commit()
    row = db_session.scalar(select(AdminAuditLog))
    assert row is not None
    assert row.action == "key_create"
    assert row.detail == {"holder_name": "用户A"}


def test_request_event_usage_persistence(db_session: Session) -> None:
    key = ApiKey(
        key_prefix="sk-ask-ab12",
        key_hash="d" * 64,
        holder_name="用户A",
        label="个人",
    )
    db_session.add(key)
    db_session.flush()

    req = create_request_row(
        db_session,
        api_key=key,
        model="deepseek-flash",
        stream=False,
        prompt_messages=[{"role": "user", "content": "你好"}],
        queued_at=datetime.now(UTC),
    )
    db_session.flush()
    assert req.status == "queued"
    assert req.holder_name == "用户A"  # 冗余快照自动填充

    bulk_insert_events(
        db_session,
        req.id,
        [
            (1, "turn/start", {}),
            (2, "thinking", {"text": "思考中"}),
            (3, "text", {"text": "答案"}),
        ],
    )
    insert_usage_record(
        db_session,
        request_id=req.id,
        step=1,
        complete=True,
        search_count=1,
        pricing_snapshot={"model": "deepseek-flash"},
        cost_cents="1.234",
        created_at=datetime.now(UTC),
    )
    db_session.commit()

    events = list(
        db_session.scalars(
            select(EventRecord).where(EventRecord.request_id == req.id).order_by(EventRecord.seq)
        )
    )
    assert [e.event_type for e in events] == ["turn/start", "thinking", "text"]
    assert events[1].payload == {"text": "思考中"}

    usage = db_session.scalar(select(UsageRecord).where(UsageRecord.request_id == req.id))
    assert usage is not None and usage.cost_cents is not None
    assert str(usage.cost_cents) == "1.234"

    # 关系导航
    fetched = db_session.get(RequestRecord, req.id)
    assert fetched is not None
    assert len(fetched.events) == 3
    assert len(fetched.usage_records) == 1
