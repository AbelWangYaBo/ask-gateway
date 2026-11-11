"""用户门户：API Key 登录、持有人数据隔离、只读消耗视图。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from ask_gateway.services import key_service


@pytest.fixture
def client(engine, monkeypatch, tmp_path) -> TestClient:
    from ask_gateway import main as main_module
    from ask_gateway.config import Settings, get_settings
    from ask_gateway.db import session as db_session

    monkeypatch.delenv("UPSTREAM_KEYS", raising=False)
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(
        db_session,
        "_SessionLocal",
        sessionmaker(bind=engine, expire_on_commit=False, future=True),
    )
    get_settings.cache_clear()
    app = main_module.create_application(Settings(data_dir=tmp_path))
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _make_key(engine, holder: str, label: str) -> str:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        _, plaintext = key_service.create_api_key(s, holder, label)
        s.commit()
    return plaintext


def test_profile_and_dashboard_with_api_key(client, engine) -> None:
    plaintext = _make_key(engine, "alice", "工作机")
    headers = {"Authorization": f"Bearer {plaintext}"}

    profile = client.get("/api/portal/profile", headers=headers)
    assert profile.status_code == 200
    body = profile.json()
    assert body["holder_name"] == "alice"
    assert len(body["keys"]) == 1 and body["keys"][0]["label"] == "工作机"

    dash = client.get("/api/portal/dashboard", headers=headers)
    assert dash.status_code == 200
    assert dash.json()["total_requests"] == 0


def test_portal_requires_valid_key(client, engine) -> None:
    _make_key(engine, "alice", "k")
    # 未带 Key / 伪造 Key 均 401
    assert client.get("/api/portal/profile").status_code == 401
    assert (
        client.get(
            "/api/portal/profile", headers={"Authorization": "Bearer sk-fake-none"}
        ).status_code
        == 401
    )


def test_portal_isolation_between_holders(client, engine) -> None:
    from ask_gateway.services import audit_service

    alice_key = _make_key(engine, "alice", "a")
    bob_key = _make_key(engine, "bob", "b")

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        alice_row = key_service.authenticate(s, alice_key)
        # alice 名下一条请求记录
        audit_service.create_request_row(
            s,
            api_key=alice_row,
            model="deepseek-flash",
            stream=False,
            prompt_messages=[],
            queued_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        s.commit()

    # alice 能看到自己的请求；bob 看到空
    alice_list = client.get(
        "/api/portal/requests", headers={"Authorization": f"Bearer {alice_key}"}
    )
    assert alice_list.status_code == 200 and alice_list.json()["total"] == 1
    bob_list = client.get(
        "/api/portal/requests", headers={"Authorization": f"Bearer {bob_key}"}
    )
    assert bob_list.status_code == 200 and bob_list.json()["total"] == 0

    # bob 无法用 id 探测 alice 的请求详情（404 而非 200）
    rid = alice_list.json()["items"][0]["request_id"]
    resp = client.get(
        f"/api/portal/requests/{rid}", headers={"Authorization": f"Bearer {bob_key}"}
    )
    assert resp.status_code == 404
    # alice 自己能看
    assert (
        client.get(
            f"/api/portal/requests/{rid}", headers={"Authorization": f"Bearer {alice_key}"}
        ).status_code
        == 200
    )


def test_portal_usage_grouped_by_day(client, engine) -> None:
    plaintext = _make_key(engine, "carol", "c")
    resp = client.get(
        "/api/portal/usage/summary",
        params={"group_by": "day"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200
    assert resp.json()["group_by"] == "day"
    assert "chart" in resp.json()
