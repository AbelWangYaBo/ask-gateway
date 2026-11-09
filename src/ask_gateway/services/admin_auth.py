"""管理员认证：口令登录签发会话 token，库存哈希，过期清理。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ask_gateway.models.admin import AdminSession
from ask_gateway.services.audit_service import log_admin_action
from ask_gateway.services.errors import AuthError
from ask_gateway.services.security import (
    generate_token,
    hash_token,
    verify_password,
)


def login(
    session: Session,
    password: str,
    expected_password: str,
    *,
    ttl_hours: int,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """校验口令并签发会话 token；失败抛 AuthError，不留任何行。"""
    if not verify_password(password, expected_password):
        raise AuthError("invalid_credentials", "admin password mismatch")

    now = datetime.now(UTC)
    token = generate_token()
    row = AdminSession(
        token_hash=hash_token(token),
        expires_at=now + timedelta(hours=ttl_hours),
        client_ip=client_ip,
        user_agent=user_agent,
    )
    session.add(row)
    session.flush()
    log_admin_action(
        session,
        "login",
        target_type="admin_session",
        target_id=str(row.id),
        client_ip=client_ip,
    )
    return token


def authenticate(session: Session, token: str) -> AdminSession | None:
    if not token:
        return None
    return session.scalar(
        select(AdminSession).where(
            AdminSession.token_hash == hash_token(token),
            AdminSession.expires_at > datetime.now(UTC),
        )
    )


def logout(session: Session, token: str) -> None:
    session.execute(
        delete(AdminSession).where(AdminSession.token_hash == hash_token(token))
    )
    session.flush()


def purge_expired(session: Session, *, now: datetime | None = None) -> int:
    """删除过期登录态，返回删除行数。"""
    moment = now or datetime.now(UTC)
    result = session.execute(
        delete(AdminSession).where(AdminSession.expires_at <= moment)
    )
    return result.rowcount
