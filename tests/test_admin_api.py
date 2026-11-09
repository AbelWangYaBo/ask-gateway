"""管理面 REST 端到端（FakeRunner + 文件 sqlite，离线）。

覆盖：登录/会话校验、Key 全生命周期与自审计、审计检索筛选分页、详情时间线、
导出脱敏、费用汇总四种分组、定价冲突校验、审计日志、看板。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes import FakeRunnerFactory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ask_gateway.db.base import json_dumps_utf8
from ask_gateway.db.session import session_scope
from ask_gateway.models import Base
from ask_gateway.models.admin import AdminAuditLog, AdminSession
from ask_gateway.models.usage import PricingRule
from ask_gateway.runtime_pool.pool import RuntimePool
from ask_gateway.server.app import create_app
from ask_gateway.services.chat_service import ChatRuntime
from ask_gateway.services.quota_service import QuotaService

MODEL = "deepseek-flash"
ADMIN_PASSWORD = "test-admin-pw"


# ---------- DSH 事件夹具（与 test_chat_api 同构的最小版本） ----------


def _assistant_message(step: int, blocks: list[dict], usage: dict | None = None) -> dict:
    data: dict = {"turn": 1, "step": step, "stream": [], "message": {"content": blocks}}
    if usage is not None:
        data["usage"] = usage
    return {"type": "assistant/message", "seq": step * 10, "time": step, "data": data}


def _standard_events() -> list[dict]:
    return [
        {"type": "turn/start", "seq": 0, "time": 1, "data": {"turn": 1}},
        {
            "type": "tool/call",
            "seq": 11,
            "time": 1,
            "data": {
                "turn": 1,
                "step": 1,
                "callId": "c1",
                "name": "web_search",
                "arguments": "{}",
            },
        },
        _assistant_message(
            1,
            blocks=[
                {"type": "reasoning", "text": "先推理"},
                {"type": "text", "text": "最终答案"},
            ],
            usage={
                "inputTokens": 100,
                "outputTokens": 50,
                "reasoningTokens": 8,
                "cacheReadTokens": 5,
            },
        ),
        {"type": "turn/end", "seq": 19, "time": 2, "data": {"turn": 1, "reason": {"kind": "stop"}}},
    ]


# ---------- 夹具 ----------


@pytest.fixture
def db_engine(tmp_path) -> Iterator[Engine]:
    eng = create_engine(
        f"sqlite:///{tmp_path}/admin.db",
        future=True,
        json_serializer=json_dumps_utf8,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(db_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=db_engine, expire_on_commit=False, future=True)


def _session_dep(factory: sessionmaker) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _override_scope(factory: sessionmaker):
    def _dep() -> Iterator[Session]:
        yield from _session_dep(factory)

    return _dep


@pytest.fixture
def context(factory: sessionmaker) -> Iterator[tuple[TestClient, sessionmaker]]:
    session = factory()
    session.add(
        PricingRule(
            model=MODEL,
            rule_name="标准计费",
            peak_start_hour=None,
            peak_end_hour=None,
            weekdays_only=False,
            is_active=True,
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            input_price_per_mtok=Decimal("100.0000"),
            output_price_per_mtok=Decimal("200.0000"),
            search_price_per_k=Decimal("10.0000"),
        )
    )
    session.commit()
    session.close()

    runner_factory = FakeRunnerFactory(scripted_events=_standard_events())
    pool = RuntimePool(
        runner_factory=runner_factory,
        labels=["up-1"],
        min_size=0,
        max_size=4,
        queue_maxsize=4,
        acquire_timeout=0.5,
        idle_ttl_seconds=600.0,
        reaper_interval_seconds=1000.0,
        circuit_break_seconds=300.0,
    )
    pool.start()
    runtime = ChatRuntime(
        session_factory=factory,
        quota=QuotaService(
            default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4
        ),
        pool=pool,
        allowed_models=(MODEL,),
    )
    app = create_app(
        chat_runtime=runtime,
        admin_password=ADMIN_PASSWORD,
        admin_session_ttl_hours=12,
    )
    app.dependency_overrides[session_scope] = _override_scope(factory)
    with TestClient(app) as client:
        yield client, factory
    pool.shutdown()


@pytest.fixture
def client(context: tuple[TestClient, sessionmaker]) -> TestClient:
    return context[0]


@pytest.fixture
def factory_fixture(context: tuple[TestClient, sessionmaker]) -> sessionmaker:
    return context[1]


def _login(client: TestClient) -> str:
    resp = client.post("/admin/auth/login", json={"password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return resp.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"X-Admin-Token": token}


def _create_key(client: TestClient, token: str, holder: str, label: str) -> dict:
    resp = client.post(
        "/admin/keys",
        headers=_auth(token),
        json={"holder_name": holder, "label": label},
    )
    assert resp.status_code == 201
    return resp.json()


def _ask(client: TestClient, plaintext: str, content: str) -> str:
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": content}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.headers["X-Request-Id"]


# ---------- 认证 ----------


def test_login_wrong_password_is_401_and_persists_nothing(
    client: TestClient, factory_fixture: sessionmaker
) -> None:
    resp = client.post("/admin/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"

    session = factory_fixture()
    try:
        assert session.scalar(select(AdminSession)) is None
        assert session.scalar(select(AdminAuditLog)) is None
    finally:
        session.close()


def test_admin_routes_require_token(client: TestClient) -> None:
    assert client.get("/admin/keys").status_code == 401
    resp = client.get("/admin/keys", headers=_auth("garbage"))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_expired_token_rejected(
    client: TestClient, factory_fixture: sessionmaker
) -> None:
    token = _login(client)
    session = factory_fixture()
    try:
        row = session.scalar(select(AdminSession))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    finally:
        session.close()
    assert client.get("/admin/keys", headers=_auth(token)).status_code == 401


def test_login_success_returns_token_and_expiry(client: TestClient) -> None:
    resp = client.post("/admin/auth/login", json={"password": ADMIN_PASSWORD})
    body = resp.json()
    assert len(body["token"]) >= 32
    assert body["expires_at"].endswith("+00:00") or "T" in body["expires_at"]


# ---------- Key 生命周期 ----------


def test_key_create_list_revoke_and_audit(client: TestClient) -> None:
    token = _login(client)
    created = _create_key(client, token, "张三", "个人 Key")
    assert created["plaintext"].startswith("sk-ask-")
    assert "key_hash" not in created
    assert created["status"] == "active"
    key_id = created["id"]

    listed = client.get("/admin/keys", headers=_auth(token)).json()
    assert any(row["id"] == key_id and "key_hash" not in row for row in listed)

    # 吊销
    resp = client.post(f"/admin/keys/{key_id}/revoke", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"
    assert resp.json()["revoked_at"] is not None

    # 重复吊销幂等
    assert client.post(f"/admin/keys/{key_id}/revoke", headers=_auth(token)).status_code == 200

    # 不存在的 Key
    missing = uuid.uuid4()
    assert client.post(
        f"/admin/keys/{missing}/revoke", headers=_auth(token)
    ).status_code == 404
    assert client.patch(
        f"/admin/keys/{missing}", headers=_auth(token), json={"rpm_limit": 1}
    ).status_code == 404

    actions = client.get(
        "/admin/audit-logs?action=key_create", headers=_auth(token)
    ).json()
    assert actions["total"] >= 1
    revoke_logs = client.get(
        "/admin/audit-logs?action=key_revoke", headers=_auth(token)
    ).json()
    assert revoke_logs["total"] == 2


def test_key_quota_patch_partial_and_null_clears(client: TestClient) -> None:
    token = _login(client)
    created = _create_key(client, token, "李四", "系统 A")
    key_id = created["id"]

    resp = client.patch(
        f"/admin/keys/{key_id}",
        headers=_auth(token),
        json={"rpm_limit": 99, "daily_budget_cents": 1234},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rpm_limit"] == 99
    assert body["daily_budget_cents"] == 1234
    assert body["max_concurrency"] is None  # 未传不动

    # 显式 null 清除
    resp = client.patch(
        f"/admin/keys/{key_id}", headers=_auth(token), json={"rpm_limit": None}
    )
    assert resp.status_code == 200
    assert resp.json()["rpm_limit"] is None
    assert resp.json()["daily_budget_cents"] == 1234

    logs = client.get("/admin/audit-logs?action=key_update", headers=_auth(token)).json()
    assert logs["items"][0]["detail"] == {"rpm_limit": None}


# ---------- 审计检索 ----------


def test_request_list_filters_pagination_and_audit(
    client: TestClient, factory_fixture: sessionmaker
) -> None:
    token = _login(client)
    zhang = _create_key(client, token, "张三", "个人 Key")
    li = _create_key(client, token, "李四", "系统 A")

    _ask(client, zhang["plaintext"], "量子加密话题XYZ 第一问")
    _ask(client, zhang["plaintext"], "量子加密话题XYZ 第二问")
    _ask(client, li["plaintext"], "完全不同的天气预报问题")

    headers = _auth(token)
    page = client.get("/admin/requests?page=1&page_size=2", headers=headers).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["page"] == 1 and page["page_size"] == 2
    page2 = client.get("/admin/requests?page=2&page_size=2", headers=headers).json()
    assert len(page2["items"]) == 1
    # 列表项不含全文，只带汇总字段
    assert "prompt_messages" not in page["items"][0]
    assert page["items"][0]["total_cost_cents"] == "0.030"

    # 持有人精确筛选
    zhang_rows = client.get(
        "/admin/requests", params={"holder_name": "张三"}, headers=headers
    ).json()
    assert zhang_rows["total"] == 2
    assert all(row["holder_name"] == "张三" for row in zhang_rows["items"])

    # key_id 筛选
    by_key = client.get(
        "/admin/requests", params={"key_id": li["id"]}, headers=headers
    ).json()
    assert by_key["total"] == 1

    # 状态筛选
    succeeded = client.get(
        "/admin/requests", params={"status": "succeeded"}, headers=headers
    ).json()
    assert succeeded["total"] == 3

    # 关键词命中 prompt 全文（JSON  cast 检索）
    kw = client.get(
        "/admin/requests", params={"keyword": "量子"}, headers=headers
    ).json()
    assert kw["total"] == 2
    # final_answer 列存 FakeRunner 的最终答复（文本块内容在事件 payload 里）
    kw_answer = client.get(
        "/admin/requests", params={"keyword": "模拟答案"}, headers=headers
    ).json()
    assert kw_answer["total"] == 3

    # 时间范围：未来日界 → 空
    empty = client.get(
        "/admin/requests",
        params={"date_from": "2099-01-01T00:00:00Z"},
        headers=headers,
    ).json()
    assert empty["total"] == 0

    # 列表查看本身留痕
    views = client.get(
        "/admin/audit-logs?action=audit_view", headers=headers
    ).json()
    assert views["total"] >= 1


def test_request_detail_timeline_thinking_usage(client: TestClient) -> None:
    token = _login(client)
    key = _create_key(client, token, "王五", "个人 Key")
    request_id = _ask(client, key["plaintext"], "详情时间线问题")

    resp = client.get(f"/admin/requests/{request_id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == request_id
    assert body["thinking"] == "先推理"
    event_types = [event["event_type"] for event in body["events"]]
    assert "thinking" in event_types and "text" in event_types
    # 事件按 seq 排序
    seqs = [event["seq"] for event in body["events"]]
    assert seqs == sorted(seqs)
    assert len(body["usage_records"]) == 1
    usage = body["usage_records"][0]
    assert usage["cost_cents"] == "0.030"
    assert usage["complete"] is True
    assert usage["pricing_snapshot"]["model"] == MODEL
    assert body["prompt_messages"][0]["content"] == "详情时间线问题"
    assert body["final_answer"] == "模拟答案"
    # 文本块投影进事件时间线，最终答复列来自 runner 的 final_response
    assert any(
        event["event_type"] == "text"
        and event["payload"].get("text") == "最终答案"
        for event in body["events"]
    )

    # 不存在
    assert client.get(
        f"/admin/requests/{uuid.uuid4()}", headers=_auth(token)
    ).status_code == 404

    logs = client.get(
        "/admin/audit-logs?action=audit_view", headers=_auth(token)
    ).json()
    assert any(row["target_id"] == request_id for row in logs["items"])


def test_export_full_json_redacts_key_hash(client: TestClient) -> None:
    token = _login(client)
    key = _create_key(client, token, "赵六", "合规导出 Key")
    request_id = _ask(client, key["plaintext"], "导出用问题")

    resp = client.get(f"/admin/requests/{request_id}/export", headers=_auth(token))
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert request_id in disposition

    body = resp.json()
    # 全 JSON 基元：UUID/Decimal/datetime 都已转字符串
    assert isinstance(body["request"]["request_id"], str)
    assert body["request"]["total_cost_cents"] == "0.030"
    assert len(body["events"]) >= 1
    assert body["usage_records"][0]["cost_cents"] == "0.030"
    assert body["api_key"]["key_prefix"].startswith("sk-ask-")
    assert "key_hash" not in body["api_key"]
    assert "plaintext" not in body["api_key"]

    logs = client.get(
        "/admin/audit-logs?action=audit_export", headers=_auth(token)
    ).json()
    assert logs["total"] == 1
    assert logs["items"][0]["target_id"] == request_id


# ---------- 费用汇总 ----------


def test_usage_summary_all_group_bys(client: TestClient) -> None:
    token = _login(client)
    zhang = _create_key(client, token, "张三", "个人 Key")
    li = _create_key(client, token, "李四", "系统 A")
    _ask(client, zhang["plaintext"], "汇总问题一")
    _ask(client, zhang["plaintext"], "汇总问题二")
    _ask(client, li["plaintext"], "汇总问题三")
    headers = _auth(token)

    for group_by, expected_groups in (
        ("holder", {"张三", "李四"}),
        ("key", {"张三 / 个人 Key", "李四 / 系统 A"}),
        ("model", {MODEL}),
        ("day", None),
    ):
        body = client.get(
            "/admin/usage/summary", params={"group_by": group_by}, headers=headers
        ).json()
        assert body["group_by"] == group_by
        rows = body["rows"]
        if expected_groups is not None:
            assert {row["group_label"] for row in rows} == expected_groups
        assert sum(row["request_count"] for row in rows) == 3
        assert sum(row["search_count"] for row in rows) == 3
        assert Decimal(str(sum(Decimal(row["cost_cents"]) for row in rows))) == Decimal(
            "0.090"
        )
        chart = body["chart"]
        assert len(chart["categories"]) == len(rows)
        assert [series["name"] for series in chart["series"]] == [
            "费用(分)",
            "请求数",
            "搜索次数",
        ]
        assert all(len(series["data"]) == len(rows) for series in chart["series"])

    # 非法分组 → 400
    resp = client.get(
        "/admin/usage/summary", params={"group_by": "hour"}, headers=headers
    )
    assert resp.status_code == 400


# ---------- 定价 ----------


def test_pricing_create_conflict_and_validation(client: TestClient) -> None:
    token = _login(client)
    headers = _auth(token)
    existing = client.get("/admin/pricing", headers=headers).json()
    assert len(existing) == 1

    created = client.put(
        "/admin/pricing",
        headers=headers,
        json={
            "model": MODEL,
            "rule_name": "工作时段峰价",
            "peak_start_hour": 9,
            "peak_end_hour": 18,
            "weekdays_only": True,
            "effective_from": "2026-01-01T00:00:00Z",
            "input_price_per_mtok": "300.0000",
            "output_price_per_mtok": "600.0000",
            "search_price_per_k": "20.0000",
        },
    )
    assert created.status_code == 201
    assert created.json()["peak_start_hour"] == 9

    # 同模型同时段冲突
    conflict = client.put(
        "/admin/pricing",
        headers=headers,
        json={
            "model": MODEL,
            "rule_name": "重复窗口",
            "peak_start_hour": 9,
            "peak_end_hour": 18,
            "weekdays_only": True,
            "effective_from": "2026-02-01T00:00:00Z",
            "input_price_per_mtok": "1",
            "output_price_per_mtok": "1",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "pricing_conflict"

    # 第二条全时段规则与种子冲突
    all_day_conflict = client.put(
        "/admin/pricing",
        headers=headers,
        json={
            "model": MODEL,
            "rule_name": "第二条全时段",
            "effective_from": "2026-03-01T00:00:00Z",
            "input_price_per_mtok": "1",
            "output_price_per_mtok": "1",
        },
    )
    assert all_day_conflict.status_code == 409

    # 窗口语义非法 → 400
    bad_window = client.put(
        "/admin/pricing",
        headers=headers,
        json={
            "model": MODEL,
            "rule_name": "倒挂窗口",
            "peak_start_hour": 20,
            "peak_end_hour": 10,
            "effective_from": "2026-01-01T00:00:00Z",
            "input_price_per_mtok": "1",
            "output_price_per_mtok": "1",
        },
    )
    assert bad_window.status_code == 400

    logs = client.get(
        "/admin/audit-logs?action=pricing_update", headers=headers
    ).json()
    assert logs["total"] == 1


# ---------- 审计日志与看板 ----------


def test_audit_logs_pagination(client: TestClient) -> None:
    token = _login(client)
    _create_key(client, token, "孙七", "Key-1")
    _create_key(client, token, "孙七", "Key-2")
    headers = _auth(token)

    page = client.get(
        "/admin/audit-logs?page=1&page_size=2", headers=headers
    ).json()
    assert page["page_size"] == 2
    assert len(page["items"]) == 2
    # 倒序
    assert page["items"][0]["id"] > page["items"][1]["id"]
    assert page["total"] >= 3  # login + 2 key_create


def test_dashboard_metrics(client: TestClient) -> None:
    token = _login(client)
    key = _create_key(client, token, "周八", "看板 Key")
    _ask(client, key["plaintext"], "看板问题一")
    _ask(client, key["plaintext"], "看板问题二")

    body = client.get("/admin/dashboard", headers=_auth(token)).json()
    assert body["total_requests"] == 2
    assert body["succeeded"] == 2
    assert body["success_rate"] == 1.0
    assert body["active_keys"] >= 1
    assert body["status_distribution"] == {"succeeded": 2}
    assert body["p50_latency_ms"] is not None
    assert body["p95_latency_ms"] is not None
    assert body["p95_latency_ms"] >= body["p50_latency_ms"]
    assert Decimal(str(body["today_cost_cents"])) == Decimal("0.060")


def test_dashboard_empty_database(
    db_engine: Engine, factory: sessionmaker
) -> None:
    runtime = ChatRuntime(
        session_factory=factory,
        quota=QuotaService(
            default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4
        ),
        pool=RuntimePool(
            runner_factory=FakeRunnerFactory(),
            labels=["up-1"],
            min_size=0,
            max_size=1,
            queue_maxsize=1,
            acquire_timeout=0.5,
            idle_ttl_seconds=600.0,
            reaper_interval_seconds=1000.0,
            circuit_break_seconds=300.0,
        ),
        allowed_models=(MODEL,),
    )
    runtime.pool.start()
    app = create_app(chat_runtime=runtime, admin_password=ADMIN_PASSWORD)
    app.dependency_overrides[session_scope] = _override_scope(factory)
    try:
        with TestClient(app) as bare:
            token = _login(bare)
            body = bare.get("/admin/dashboard", headers=_auth(token)).json()
            assert body["total_requests"] == 0
            assert body["success_rate"] == 0.0
            assert body["p50_latency_ms"] is None
            assert body["p95_latency_ms"] is None
            assert body["active_keys"] == 0
            assert body["status_distribution"] == {}
    finally:
        runtime.pool.shutdown()
