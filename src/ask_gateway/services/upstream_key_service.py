"""上游 Key 池服务：加密入库、指纹查重、池 label 解析。

管理端 CRUD 在此完成加密与审计留痕；runtime 工厂经 plaintext_for_label
解密注入 DSH 子进程。明文只在两个出口短暂存在：创建响应（不留）与
子进程环境变量（不落日志）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import UpstreamKey
from ask_gateway.services import audit_service
from ask_gateway.services.errors import ServiceError
from ask_gateway.services.secret_box import SecretBox


class UpstreamKeyExistsError(ServiceError):
    http_status = 409

    def __init__(self, fingerprint: str) -> None:
        super().__init__("upstream_key_exists", f"该 Key 已存在（指纹 {fingerprint}）")


class UpstreamKeyNotFoundError(ServiceError):
    http_status = 404

    def __init__(self) -> None:
        super().__init__("upstream_key_not_found", "上游 Key 不存在")


def list_upstream_keys(session: Session) -> list[UpstreamKey]:
    return list(session.scalars(select(UpstreamKey).order_by(UpstreamKey.created_at)))


def create_upstream_key(
    session: Session,
    box: SecretBox,
    *,
    label: str,
    plaintext: str,
    notes: str | None = None,
    client_ip: str | None = None,
) -> UpstreamKey:
    plaintext = plaintext.strip()
    fingerprint = SecretBox.fingerprint(plaintext)
    exists = session.scalar(select(UpstreamKey).where(UpstreamKey.key_fingerprint == fingerprint))
    if exists is not None:
        raise UpstreamKeyExistsError(fingerprint)
    row = UpstreamKey(
        label=label,
        key_ciphertext=box.encrypt(plaintext),
        key_fingerprint=fingerprint,
        key_preview=SecretBox.preview(plaintext),
        notes=notes,
    )
    session.add(row)
    session.flush()
    audit_service.log_admin_action(
        session,
        "upstream_key.create",
        target_type="upstream_key",
        target_id=str(row.id),
        detail={"label": label, "fingerprint": fingerprint, "preview": row.key_preview},
        client_ip=client_ip,
    )
    return row


def update_upstream_key(
    session: Session,
    key_id: UUID,
    *,
    label: str | None = None,
    notes: str | None = None,
    is_active: bool | None = None,
    client_ip: str | None = None,
) -> UpstreamKey:
    row = session.get(UpstreamKey, key_id)
    if row is None:
        raise UpstreamKeyNotFoundError()
    changes: dict = {}
    if label is not None and label != row.label:
        row.label = label
        changes["label"] = label
    if notes is not None and notes != row.notes:
        row.notes = notes
        changes["notes"] = notes
    if is_active is not None and is_active != row.is_active:
        row.is_active = is_active
        changes["is_active"] = is_active
    if changes:
        audit_service.log_admin_action(
            session,
            "upstream_key.update",
            target_type="upstream_key",
            target_id=str(row.id),
            detail=changes,
            client_ip=client_ip,
        )
    session.flush()
    return row


def delete_upstream_key(
    session: Session,
    key_id: UUID,
    *,
    client_ip: str | None = None,
) -> None:
    row = session.get(UpstreamKey, key_id)
    if row is None:
        raise UpstreamKeyNotFoundError()
    audit_service.log_admin_action(
        session,
        "upstream_key.delete",
        target_type="upstream_key",
        target_id=str(row.id),
        detail={"label": row.label, "fingerprint": row.key_fingerprint},
        client_ip=client_ip,
    )
    session.delete(row)
    session.flush()


def active_pool_labels(session: Session) -> list[str]:
    """当前应在线的池组 label（active 的上游 Key）。"""
    rows = session.scalars(
        select(UpstreamKey).where(UpstreamKey.is_active.is_(True)).order_by(UpstreamKey.created_at)
    )
    return [row.pool_label for row in rows]


def plaintext_for_label(session: Session, box: SecretBox, pool_label: str) -> str | None:
    """runtime 工厂取明文：label → 解密。找不到（未配置/已删）返回 None。"""
    prefix = "upstream-"
    if not pool_label.startswith(prefix):
        return None
    hex8 = pool_label[len(prefix) :]
    rows = session.scalars(select(UpstreamKey))
    for row in rows:
        if row.id.hex.startswith(hex8):
            if not row.is_active:
                return None
            return box.decrypt(row.key_ciphertext)
    return None


def seed_from_env_once(session: Session, box: SecretBox, env_keys: list[str]) -> int:
    """首次启动引导：DB 为空且 env 配了 Key 时一次性入库，此后以 DB 为准。"""
    existing = session.scalar(select(UpstreamKey).limit(1))
    if existing is not None:
        return 0
    inserted = 0
    for i, plaintext in enumerate(env_keys, start=1):
        plaintext = plaintext.strip()
        if not plaintext:
            continue
        fingerprint = SecretBox.fingerprint(plaintext)
        dup = session.scalar(
            select(UpstreamKey).where(UpstreamKey.key_fingerprint == fingerprint)
        )
        if dup is not None:
            continue
        session.add(
            UpstreamKey(
                label=f"upstream-{inserted + 1}",
                key_ciphertext=box.encrypt(plaintext),
                key_fingerprint=fingerprint,
                key_preview=SecretBox.preview(plaintext),
                notes="从 UPSTREAM_KEYS 环境变量引导入库",
            )
        )
        inserted += 1
    if inserted:
        audit_service.log_admin_action(
            session,
            "upstream_key.seed_from_env",
            target_type="upstream_key",
            detail={"count": inserted},
        )
        session.flush()
    return inserted
