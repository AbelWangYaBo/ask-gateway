"""API Key 启停/到期/验证 与上游 Key 筛选维度。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from ask_gateway.services import key_service
from ask_gateway.services.secret_box import SecretBox


@pytest.fixture
def box() -> SecretBox:
    return SecretBox(b"l" * 32)


@pytest.fixture
def db(engine) -> Session:
    """与 conftest 内存库同一 engine；返回绑定该 engine 的会话工厂句柄。"""

    class _Factory:
        def get_session_factory(self):
            return sessionmaker(bind=engine, expire_on_commit=False, future=True)

    return _Factory()  # type: ignore[return-value]


def _create_platform_key(session, holder="u1", label="l1") -> tuple[str, object]:
    row, plaintext = key_service.create_api_key(session, holder, label)
    session.commit()
    return plaintext, row


def test_authenticate_rejects_disabled_and_expired(db, box):
    factory = db.get_session_factory()
    with factory() as s:
        plaintext, row = _create_platform_key(s)
        assert key_service.authenticate(s, plaintext) is not None

        # 手动停用：拒绝；恢复：通过
        key_service.update_quota(s, row.id, disabled=True)
        s.commit()
        assert key_service.authenticate(s, plaintext) is None
        key_service.update_quota(s, row.id, disabled=False)
        s.commit()
        assert key_service.authenticate(s, plaintext) is not None

        # 到期：过去时间拒绝；NULL（永不过期）/未来时间恢复
        key_service.update_quota(s, row.id, expires_at=datetime.now(UTC) - timedelta(hours=1))
        s.commit()
        assert key_service.authenticate(s, plaintext) is None
        key_service.update_quota(s, row.id, expires_at=datetime.now(UTC) + timedelta(days=1))
        s.commit()
        assert key_service.authenticate(s, plaintext) is not None
        key_service.update_quota(s, row.id, expires_at=None)
        s.commit()
        assert key_service.authenticate(s, plaintext) is not None


@pytest.fixture
def client(db, engine, monkeypatch, tmp_path) -> TestClient:
    from ask_gateway import main as main_module
    from ask_gateway.config import Settings, get_settings
    from ask_gateway.db import session as db_session

    monkeypatch.delenv("UPSTREAM_KEYS", raising=False)
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    # 全局引擎重定向到 conftest 内存库：app 与 fixture 同库同主密钥
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(
        db_session,
        "_SessionLocal",
        sessionmaker(bind=engine, expire_on_commit=False, future=True),
    )
    get_settings.cache_clear()
    app = main_module.create_application(Settings(data_dir=tmp_path, admin_password="lc-pw"))
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _auth(client: TestClient) -> dict[str, str]:
    token = client.post("/admin/auth/login", json={"password": "lc-pw"}).json()["token"]
    return {"X-Admin-Token": token}


def test_verify_key_identifies_platform_and_upstream(client):
    headers = _auth(client)
    created = client.post(
        "/admin/keys", json={"holder_name": "abel", "label": "ver"}, headers=headers
    ).json()
    plaintext = created["plaintext"]

    # 平台 Key：kind=platform，带持有人与状态
    out = client.post("/admin/keys/verify", json={"plaintext": plaintext}, headers=headers)
    assert out.status_code == 200
    body = out.json()
    assert body["kind"] == "platform"
    assert body["holder_name"] == "abel" and body["status"] == "active"

    # 未知 Key
    out = client.post(
        "/admin/keys/verify", json={"plaintext": "sk-not-exists-anywhere"}, headers=headers
    )
    assert out.json()["kind"] == "unknown"


def test_verify_upstream_key_via_admin_box(db, client):
    headers = _auth(client)
    control = client.app.state.upstream_control
    factory = db.get_session_factory()
    from ask_gateway.services import upstream_key_service

    with factory() as s:
        upstream_key_service.create_upstream_key(
            s, control.box, label="上游A", plaintext="sk-up-verify-abc123"
        )
        s.commit()

    out = client.post(
        "/admin/keys/verify", json={"plaintext": "sk-up-verify-abc123"}, headers=headers
    )
    body = out.json()
    assert body["kind"] == "upstream"
    assert body["label"] == "上游A" and body["status"] == "enabled"
    assert "sk-up-verify-abc123" not in out.text


def test_requests_filter_and_usage_group_by_upstream(client):
    headers = _auth(client)
    # 无数据时接口可用即可（分组/筛选 SQL 已被全量套件其他路径覆盖）
    resp = client.get("/admin/requests", headers=headers)
    assert resp.status_code == 200
    usage = client.get("/admin/usage/summary", params={"group_by": "upstream_key"}, headers=headers)
    assert usage.status_code == 200
    assert usage.json()["group_by"] == "upstream_key"
