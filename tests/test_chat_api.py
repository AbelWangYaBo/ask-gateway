"""对外 Chat API 端到端（FakeRunner，离线）：成功/流式/鉴权/配额/池满/上游失败/断连语义。"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fakes import FakeRunner, FakeRunnerFactory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ask_gateway.db.base import json_dumps_utf8
from ask_gateway.db.session import session_scope
from ask_gateway.models import Base
from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.event import EventRecord
from ask_gateway.models.request import (
    STATUS_FAILED,
    STATUS_RATE_LIMITED,
    STATUS_SUCCEEDED,
    RequestRecord,
)
from ask_gateway.models.usage import PricingRule, UsageRecord
from ask_gateway.runtime_pool.errors import (
    RunnerBroken,
    RunnerError,
    RunnerTimeout,
)
from ask_gateway.runtime_pool.pool import RuntimePool
from ask_gateway.schemas.chat import ChatCompletionRequest
from ask_gateway.server.app import create_app
from ask_gateway.services.chat_service import ChatRuntime, sse_chat
from ask_gateway.services.key_service import create_api_key
from ask_gateway.services.quota_service import QuotaService

MODEL = "deepseek-flash"


# ---------- DSH 事件夹具 ----------


def _assistant_message(
    step: int,
    *,
    blocks: list[dict],
    usage: dict | None = None,
    turn: int = 1,
) -> dict:
    data: dict = {"turn": turn, "step": step, "stream": [], "message": {"content": blocks}}
    if usage is not None:
        data["usage"] = usage
    return {"type": "assistant/message", "seq": step * 10, "time": step, "data": data}


def _tool_call(step: int, name: str = "web_search", call_id: str = "c1") -> dict:
    return {
        "type": "tool/call",
        "seq": step * 10 + 1,
        "time": step,
        "data": {"turn": 1, "step": step, "callId": call_id, "name": name, "arguments": "{}"},
    }


def standard_events() -> list[dict]:
    return [
        {"type": "turn/start", "seq": 0, "time": 1, "data": {"turn": 1}},
        _tool_call(1),
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
        {"type": "turn/end", "seq": 9, "time": 2, "data": {"turn": 1, "reason": {"kind": "stop"}}},
    ]


def incomplete_events() -> list[dict]:
    return [
        _assistant_message(1, blocks=[{"type": "text", "text": "a"}], usage={"inputTokens": 10}),
        _assistant_message(1, blocks=[{"type": "text", "text": "b"}]),
    ]


# ---------- 夹具 ----------


def _seed_pricing(session: Session, model: str = MODEL) -> PricingRule:
    rule = PricingRule(
        model=model,
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
    session.add(rule)
    session.commit()
    return rule


@pytest.fixture
def db_engine(tmp_path) -> Iterator[Engine]:
    # 文件库 + 默认连接池：管线工作线程与请求级依赖各持独立连接，
    # 复刻生产 PG 的多连接语义（StaticPool 单连接会让 session 关闭互相回滚）
    eng = create_engine(
        f"sqlite:///{tmp_path}/chat.db",
        future=True,
        json_serializer=json_dumps_utf8,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(db_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=db_engine, expire_on_commit=False, future=True)


@pytest.fixture
def seeded(factory: sessionmaker) -> tuple[sessionmaker, ApiKey, str]:
    session = factory()
    _seed_pricing(session)
    key_row, plaintext = create_api_key(session, "张三", "个人 Key")
    session.commit()
    session.close()
    return factory, key_row, plaintext


def _make_pool(runner_factory: FakeRunnerFactory, **kw: object) -> RuntimePool:
    defaults = dict(
        labels=["up-1"],
        min_size=0,
        max_size=2,
        queue_maxsize=2,
        acquire_timeout=0.5,
        idle_ttl_seconds=600.0,
        reaper_interval_seconds=1000.0,
        circuit_break_seconds=300.0,
    )
    defaults.update(kw)
    pool = RuntimePool(runner_factory=runner_factory, **defaults)
    pool.start()
    return pool


@pytest.fixture
def client(
    seeded,
) -> Iterator[tuple[TestClient, RuntimePool, FakeRunnerFactory, sessionmaker]]:
    session_factory, _key_row, _plaintext = seeded
    runner_factory = FakeRunnerFactory(scripted_events=standard_events())
    pool = _make_pool(runner_factory)
    quota = QuotaService(
        default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4
    )
    runtime = ChatRuntime(
        session_factory=session_factory,
        quota=quota,
        pool=pool,
        allowed_models=(MODEL, "deepseek-v4-pro"),
    )
    app = create_app(chat_runtime=runtime)
    app.dependency_overrides[session_scope] = _override_scope(session_factory)
    with TestClient(app) as test_client:
        yield test_client, pool, runner_factory, session_factory
    pool.shutdown()


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
    """dependency_overrides 需要生成器函数本身，lambda 只会把生成器对象当返回值。"""

    def _dep() -> Iterator[Session]:
        yield from _session_dep(factory)

    return _dep


def _auth(plaintext: str) -> dict:
    return {"Authorization": f"Bearer {plaintext}"}


def _body() -> dict:
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "今天新闻？"}],
        "stream": False,
    }


def _only_request(session: Session) -> RequestRecord:
    return session.scalar(select(RequestRecord))


# ---------- 非流式 ----------


def test_non_stream_success_shape_and_persistence(
    client, seeded
) -> None:
    test_client, _pool, runner_factory, session_factory = client
    _factory, key_row, plaintext = seeded

    response = test_client.post("/v1/chat/completions", json=_body(), headers=_auth(plaintext))
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == MODEL
    assert response.headers["X-Request-Id"] == str(
        uuid.UUID(body["id"].removeprefix("chatcmpl-"))
    )
    message = body["choices"][0]["message"]
    assert message["role"] == "assistant"
    assert message["content"] == "模拟答案"
    assert message["reasoning_content"] == "先推理"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "reasoning_tokens": 8,
        "cache_read_tokens": 5,
        "cache_write_tokens": None,
        "search_count": 1,
    }

    session = session_factory()
    row = _only_request(session)
    assert row.status == STATUS_SUCCEEDED
    assert row.holder_name == "张三"
    assert row.key_label == "个人 Key"
    assert row.api_key_id == key_row.id
    assert row.prompt_messages == _body()["messages"]
    assert row.final_answer == "模拟答案"
    assert row.search_count == 1
    assert row.upstream_key_label == "up-1"
    assert row.total_input_tokens == 100
    assert row.total_output_tokens == 50
    # input 100/1e6*100=0.01 + output 50/1e6*200=0.01 + search 1/1000*10=0.01
    assert row.total_cost_cents == Decimal("0.030")

    events = list(session.scalars(select(EventRecord).order_by(EventRecord.seq)).all())
    assert [e.event_type for e in events] == [
        "turn/start",
        "tool/call",
        "assistant/message",
        "thinking",
        "text",
        "turn/end",
    ]
    # 原始事件完整保真，web_search 调用细节可审计
    assert events[1].payload["data"]["name"] == "web_search"

    usage_rows = list(session.scalars(select(UsageRecord)).all())
    assert len(usage_rows) == 1
    assert usage_rows[0].complete is True
    assert usage_rows[0].cost_cents == Decimal("0.030")
    assert usage_rows[0].pricing_snapshot["model"] == MODEL
    session.close()

    assert runner_factory.runners[0].run_count == 1


def test_incomplete_usage_bills_zero_and_nulls_tokens(client, seeded) -> None:
    test_client, _pool, _runner_factory, session_factory = client
    _f, _key, plaintext = seeded
    # 直接换一个跑不完整事件的池：重建 runtime
    runner_factory = FakeRunnerFactory(scripted_events=incomplete_events())
    pool = _make_pool(runner_factory)
    quota = QuotaService(
        default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4
    )
    runtime = ChatRuntime(
        session_factory=session_factory,
        quota=quota,
        pool=pool,
        allowed_models=(MODEL,),
    )
    app = create_app(chat_runtime=runtime)
    app.dependency_overrides[session_scope] = _override_scope(session_factory)
    try:
        with TestClient(app) as local_client:
            response = local_client.post(
                "/v1/chat/completions", json=_body(), headers=_auth(plaintext)
            )
            assert response.status_code == 200
            assert response.json()["usage"]["prompt_tokens"] == 0
            session = session_factory()
            row = _only_request(session)
            assert row.total_cost_cents == Decimal("0.000")
            assert row.total_input_tokens is None
            usage_row = session.scalar(select(UsageRecord))
            assert usage_row.complete is False
            assert usage_row.cost_cents == Decimal("0.000")
            session.close()
    finally:
        pool.shutdown()


# ---------- SSE ----------


def _parse_sse(lines: Iterator[str]) -> list[dict]:
    frames: list[dict] = []
    event_name: str | None = None
    for line in lines:
        if line == "":
            event_name = None
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data = line.removeprefix("data:").strip()
            frames.append({"event": event_name, "data": data})
    return frames


def test_stream_emits_openai_chunks_and_custom_events(client, seeded) -> None:
    test_client, _pool, _runner_factory, _session_factory = client
    _f, _key, plaintext = seeded
    body = _body()
    body["stream"] = True

    with test_client.stream(
        "POST", "/v1/chat/completions", json=body, headers=_auth(plaintext)
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        request_id = response.headers["X-Request-Id"]
        frames = _parse_sse(response.iter_lines())

    # 首帧即 X-Request-Id
    assert frames[0] == {"event": "x-request-id", "data": json.dumps({"request_id": request_id})}
    # 第二帧 role
    role_data = json.loads(frames[1]["data"])
    assert role_data["choices"][0]["delta"] == {"role": "assistant"}

    events_seen = [f["event"] for f in frames]
    assert "x-dsh-event" in events_seen

    def _deltas() -> Iterator[dict]:
        for f in frames:
            if f["event"] is not None or f["data"] == "[DONE]":
                continue
            choices = json.loads(f["data"])["choices"]
            if choices:
                yield choices[0]["delta"]

    deltas = list(_deltas())
    assert next(d["reasoning_content"] for d in deltas if "reasoning_content" in d) == "先推理"
    assert next(d["content"] for d in deltas if "content" in d) == "最终答案"

    # 自定义事件承载原始 DSH 过程
    custom = [json.loads(f["data"]) for f in frames if f["event"] == "x-dsh-event"]
    assert any(c["type"] == "tool/call" for c in custom)

    last = frames[-1]
    assert last["data"] == "[DONE]"
    usage_frame = json.loads(frames[-2]["data"])
    assert usage_frame["choices"] == []
    assert usage_frame["usage"]["total_tokens"] == 150
    finish_frame = json.loads(frames[-3]["data"])
    assert finish_frame["choices"][0]["finish_reason"] == "stop"


# ---------- 鉴权与请求校验 ----------


def test_auth_failures_return_401_envelope(client) -> None:
    test_client, *_ = client
    for headers in ({}, {"Authorization": "Bearer sk-ask-deadbeef"}, {"Authorization": "token"}):
        response = test_client.post("/v1/chat/completions", json=_body(), headers=headers)
        assert response.status_code == 401
        error = response.json()["error"]
        assert error["code"] == "invalid_credentials"
        assert "request_id" in error


def test_model_not_allowed_400(client, seeded) -> None:
    test_client, *_ = client
    _f, _key, plaintext = seeded
    body = _body()
    body["model"] = "gpt-x"
    response = test_client.post("/v1/chat/completions", json=body, headers=_auth(plaintext))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "model_not_allowed"


def test_empty_messages_400(client, seeded) -> None:
    test_client, *_ = client
    _f, _key, plaintext = seeded
    response = test_client.post(
        "/v1/chat/completions",
        json={"model": MODEL, "messages": []},
        headers=_auth(plaintext),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


# ---------- 配额 ----------


def test_rpm_zero_rejects_and_persists_rate_limited(client, seeded) -> None:
    test_client, _pool, _r, session_factory = client
    session = session_factory()
    key_row = session.get(ApiKey, seeded[1].id)
    key_row.rpm_limit = 0
    session.commit()
    session.close()

    response = test_client.post(
        "/v1/chat/completions", json=_body(), headers=_auth(seeded[2])
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rpm_exceeded"

    session = session_factory()
    row = _only_request(session)
    assert row.status == STATUS_RATE_LIMITED
    assert row.error_code == "rpm_exceeded"
    assert row.finished_at is not None
    session.close()


def test_concurrency_slot_rejected_then_recovered(client, seeded) -> None:
    test_client, _pool, _r, session_factory = client
    session = session_factory()
    key_row = session.get(ApiKey, seeded[1].id)
    key_row.max_concurrency = 1
    session.commit()
    quota = test_client.app.state.chat_runtime.quota
    quota.acquire_slot(key_row)  # 外部占满唯一槽位
    try:
        response = test_client.post(
            "/v1/chat/completions", json=_body(), headers=_auth(seeded[2])
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "concurrency_exceeded"
    finally:
        quota.release_slot(key_row.id)

    # 槽位释放后立刻可用
    assert test_client.post(
        "/v1/chat/completions", json=_body(), headers=_auth(seeded[2])
    ).status_code == 200


def test_daily_budget_rejects_429(client, seeded) -> None:
    test_client, _pool, _r, session_factory = client
    session = session_factory()
    key_row = session.get(ApiKey, seeded[1].id)
    key_row.daily_budget_cents = 100
    prior = RequestRecord(
        request_id=uuid.uuid4(),
        api_key_id=key_row.id,
        holder_name=key_row.holder_name,
        key_label=key_row.label,
        model=MODEL,
        stream=False,
        status=STATUS_SUCCEEDED,
        prompt_messages=[],
        queued_at=datetime.now(UTC),
        total_cost_cents=Decimal("100"),
    )
    session.add(prior)
    session.commit()
    session.close()

    response = test_client.post(
        "/v1/chat/completions", json=_body(), headers=_auth(seeded[2])
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "daily_budget_exceeded"


# ---------- 池与上游失败 ----------


def _standalone_app(
    session_factory: sessionmaker,
    runner_factory: FakeRunnerFactory,
    *,
    pool_kw: dict | None = None,
    quota: QuotaService | None = None,
) -> tuple:
    pool = _make_pool(runner_factory, **(pool_kw or {}))
    runtime = ChatRuntime(
        session_factory=session_factory,
        quota=quota
        or QuotaService(
            default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4
        ),
        pool=pool,
        allowed_models=(MODEL,),
    )
    app = create_app(chat_runtime=runtime)
    app.dependency_overrides[session_scope] = _override_scope(session_factory)
    return app, pool


def test_pool_exhausted_returns_429(seeded) -> None:
    session_factory, _key, plaintext = seeded
    runner_factory = FakeRunnerFactory(scripted_events=standard_events())
    app, pool = _standalone_app(
        session_factory, runner_factory, pool_kw={"min_size": 0, "max_size": 0, "queue_maxsize": 0}
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/v1/chat/completions", json=_body(), headers=_auth(plaintext)
            )
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "pool_busy"
        session = session_factory()
        assert _only_request(session).status == STATUS_RATE_LIMITED
        session.close()
    finally:
        pool.shutdown()


def test_runner_timeout_504_and_handle_recycled(seeded) -> None:
    session_factory, _key, plaintext = seeded
    runner_factory = FakeRunnerFactory(raise_exc=RunnerTimeout("slow"))
    app, pool = _standalone_app(session_factory, runner_factory)
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/v1/chat/completions", json=_body(), headers=_auth(plaintext)
            )
            assert response.status_code == 504
            assert response.json()["error"]["code"] == "upstream_timeout"
        assert runner_factory.runners[0].closed is True  # 超时句柄保守回收

        # 池补上新实例，后续请求成功
        runner_factory.configure(scripted_events=standard_events())
        with TestClient(app) as test_client:
            ok = test_client.post(
                "/v1/chat/completions", json=_body(), headers=_auth(plaintext)
            )
            assert ok.status_code == 200
        session = session_factory()
        rows = list(session.scalars(select(RequestRecord).order_by(RequestRecord.created_at)).all())
        assert rows[0].status == STATUS_FAILED
        assert rows[0].error_code == "upstream_timeout"
        assert rows[1].status == STATUS_SUCCEEDED
        session.close()
    finally:
        pool.shutdown()


def test_runner_broken_502_replaced_runner_error_502_reusable(seeded) -> None:
    session_factory, _key, plaintext = seeded

    # RunnerBroken：句柄销毁，池补新实例
    broken_factory = FakeRunnerFactory(raise_exc=RunnerBroken("transport closed"))
    app, pool = _standalone_app(session_factory, broken_factory)
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/v1/chat/completions", json=_body(), headers=_auth(plaintext)
            )
            assert response.status_code == 502
            assert response.json()["error"]["code"] == "upstream_broken"
        assert broken_factory.runners[0].closed is True
    finally:
        pool.shutdown()

    # RunnerError（JSON-RPC 业务错误）：进程仍健康，句柄保留复用
    error_factory = FakeRunnerFactory(raise_exc=RunnerError("rpc failed"))
    app2, pool2 = _standalone_app(session_factory, error_factory)
    try:
        with TestClient(app2) as test_client:
            response = test_client.post(
                "/v1/chat/completions", json=_body(), headers=_auth(plaintext)
            )
            assert response.status_code == 502
            assert response.json()["error"]["code"] == "upstream_error"
        assert error_factory.runners[0].closed is False
        session = session_factory()
        row = list(session.scalars(select(RequestRecord)).all())[-1]
        assert row.status == STATUS_FAILED
        assert row.error_code == "upstream_error"
        session.close()
    finally:
        pool2.shutdown()


# ---------- 模型列表与健康检查 ----------


def test_stream_quota_failure_emits_error_frame(client, seeded) -> None:
    test_client, _pool, _r, session_factory = client
    session = session_factory()
    key_row = session.get(ApiKey, seeded[1].id)
    key_row.rpm_limit = 0
    session.commit()
    session.close()

    body = _body()
    body["stream"] = True
    with test_client.stream(
        "POST", "/v1/chat/completions", json=body, headers=_auth(seeded[2])
    ) as response:
        assert response.status_code == 200  # 流已开始，错误走自定义事件帧
        frames = _parse_sse(response.iter_lines())
    error_frames = [f for f in frames if f["event"] == "error"]
    assert len(error_frames) == 1
    assert json.loads(error_frames[0]["data"])["code"] == "rpm_exceeded"


async def test_sse_generator_marks_client_disconnected_but_persists(seeded) -> None:
    # TestClient 不投递 ASGI disconnect，直接驱动生成器并注入断连探针
    session_factory, key_row, _plaintext = seeded

    class SlowRunner(FakeRunner):
        def run(self, messages, *, session_id=None, on_event=None):  # type: ignore[no-untyped-def]
            time.sleep(0.6)  # 让断连标志先置位，管线仍在跑
            return super().run(messages, session_id=session_id, on_event=on_event)

    class SlowFactory:
        def __call__(self, label: str) -> SlowRunner:
            return SlowRunner(
                upstream_key_label=label, scripted_events=standard_events()
            )

    pool = _make_pool(SlowFactory())  # type: ignore[arg-type]
    quota = QuotaService(
        default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4
    )
    runtime = ChatRuntime(
        session_factory=session_factory,
        quota=quota,
        pool=pool,
        allowed_models=(MODEL,),
    )
    try:
        polls = 0

        async def is_disconnected() -> bool:
            nonlocal polls
            polls += 1
            return True  # 第一次队列空转即报告断连

        gen = sse_chat(
            runtime,
            api_key=key_row,
            payload=ChatCompletionRequest(
                model=MODEL,
                messages=[{"role": "user", "content": "q"}],
                stream=True,
            ),
            request_id=uuid.uuid4(),
            client_ip=None,
            user_agent=None,
            is_disconnected=is_disconnected,
        )
        frames = [frame async for frame in gen]
        assert polls >= 1
        assert frames[0]["event"] == "x-request-id"

        # 工作线程继续跑完并自行提交，轮询终态
        deadline = time.monotonic() + 5.0
        session = session_factory()
        row = None
        while time.monotonic() < deadline:
            session.expire_all()
            row = _only_request(session)
            if row is not None and row.finished_at is not None:
                break
            time.sleep(0.1)
        assert row is not None and row.status == "client_disconnected"
        # 已产生的事件与 usage 照常落库
        events = list(session.scalars(select(EventRecord)).all())
        assert {e.event_type for e in events} >= {"thinking", "text", "tool/call"}
        assert session.scalar(select(UsageRecord)) is not None
        session.close()
    finally:
        pool.shutdown()


def test_models_and_healthz(client) -> None:
    test_client, _pool, _r, session_factory = client
    models = test_client.get("/v1/models")
    assert models.status_code == 200
    assert models.json() == {
        "object": "list",
        "data": [
            {"id": MODEL, "object": "model", "owned_by": "ask-gateway"},
            {"id": "deepseek-v4-pro", "object": "model", "owned_by": "ask-gateway"},
        ],
    }
    health = test_client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
