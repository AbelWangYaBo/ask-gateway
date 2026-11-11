"""门户对话：持久化、追加与同点反复分叉（c-d / c-e / c-f）。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fakes import FakeRunnerFactory
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from ask_gateway.db.session import session_scope
from ask_gateway.runtime_pool.pool import RuntimePool
from ask_gateway.server.app import create_app
from ask_gateway.services import key_service
from ask_gateway.services.chat_service import ChatRuntime
from ask_gateway.services.quota_service import QuotaService

MODEL = "deepseek-flash"


def _scripted() -> list[dict[str, Any]]:
    return [
        {"type": "thinking", "payload": {"text": "想一想"}},
        {"type": "text", "payload": {"text": "这是回答。"}},
    ]


@pytest.fixture
def client(engine) -> Iterator[TestClient]:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with session_factory() as s:
        _, plaintext = key_service.create_api_key(s, "alice", "chat")
        s.commit()

    runner_factory = FakeRunnerFactory(scripted_events=_scripted())
    pool = RuntimePool(
        runner_factory=runner_factory,
        labels=["fake-1"],
        min_size=0,
        max_size=2,
        queue_maxsize=4,
        acquire_timeout=5.0,
        idle_ttl_seconds=60.0,
        reaper_interval_seconds=60.0,
        circuit_break_seconds=60.0,
    )
    runtime = ChatRuntime(
        session_factory=session_factory,
        quota=QuotaService(default_rpm=30, default_daily_budget_cents=10**9, default_concurrency=4),
        pool=pool,
        allowed_models=(MODEL,),
    )
    app = create_app(chat_runtime=runtime)
    app.dependency_overrides[session_scope] = _override(session_factory)
    with TestClient(app) as c:
        # 门户鉴权走 session_scope 依赖，同样指到测试库
        c.headers.update({"Authorization": f"Bearer {plaintext}"})
        yield c
    pool.shutdown()


def _override(factory: sessionmaker) -> Any:
    def _scope() -> Iterator[Any]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _scope


def _create(client: TestClient, title: str | None = None) -> str:
    body = {} if title is None else {"title": title}
    resp = client.post("/portal/conversations", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _chat(client: TestClient, cid: str, content: str) -> str:
    resp = client.post(
        f"/portal/conversations/{cid}/chat",
        json={"content": content, "model": MODEL, "enable_web_search": False},
    )
    assert resp.status_code == 200, resp.text
    return resp.text


def test_conversation_crud_and_append(client: TestClient) -> None:
    cid = _create(client, "测试对话")
    _chat(client, cid, "第一个问题")
    _chat(client, cid, "第二个问题")

    detail = client.get(f"/portal/conversations/{cid}")
    msgs = detail.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    # 对话消息以审计库最终回答为权威（FakeRunner 默认 final_response）
    assert msgs[1]["content"] == "模拟答案"
    assert msgs[1]["reasoning"] == "想一想"
    assert msgs[1]["request_id"]
    # 首轮提问成为标题
    assert detail.json()["conversation"]["title"] == "测试对话"

    listing = client.get("/portal/conversations").json()
    assert any(c["id"] == cid for c in listing)
    assert client.delete(f"/portal/conversations/{cid}").status_code == 204


def test_fork_same_point_repeatedly(client: TestClient) -> None:
    """a-b-c 之后，从 c 点反复分叉出 c-d / c-e / c-f。"""
    cid = _create(client)
    _chat(client, cid, "a 问")
    _chat(client, cid, "b 问")
    _chat(client, cid, "c 问")

    base = client.get(f"/portal/conversations/{cid}").json()["messages"]
    assert len(base) == 6  # a/b/c 三轮

    forks: list[str] = []
    for question in ("d 问", "e 问", "f 问"):
        fork = client.post(f"/portal/conversations/{cid}/fork", json={})
        assert fork.status_code == 201
        fork_id = fork.json()["id"]
        assert fork.json()["forked_from_id"] == cid
        forks.append(fork_id)
        # 在分支里继续一轮（服务端拼历史 = a/b/c + 新问题）
        _chat(client, fork_id, question)

    # 三个分支互不影响：都含 a-b-c 历史与各自的新问答
    contents = []
    for fork_id in forks:
        msgs = client.get(f"/portal/conversations/{fork_id}").json()["messages"]
        assert [m["content"] for m in msgs[:6]] == [m["content"] for m in base]
        assert len(msgs) == 8
        contents.append(msgs[6]["content"])
    assert len(set(contents)) == 3
    # 原对话保持 a-b-c 不变
    assert len(client.get(f"/portal/conversations/{cid}").json()["messages"]) == 6


def test_fork_at_seq_truncates(client: TestClient) -> None:
    cid = _create(client)
    _chat(client, cid, "a 问")
    _chat(client, cid, "b 问")
    # seq=2 是第一轮的 assistant：从它之后分叉，只保留第一轮
    fork = client.post(f"/portal/conversations/{cid}/fork", json={"at_seq": 2})
    assert fork.status_code == 201
    msgs = client.get(f"/portal/conversations/{fork.json()['id']}").json()["messages"]
    assert [m["content"] for m in msgs] == ["a 问", "模拟答案"]
    # 空分叉点被拒绝
    bad = client.post(f"/portal/conversations/{cid}/fork", json={"at_seq": 0})
    assert bad.status_code == 400


def test_conversation_isolated_between_holders(client: TestClient, engine) -> None:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with session_factory() as s:
        _, other = key_service.create_api_key(s, "bob", "b")
        s.commit()
    cid = _create(client, "alice 的对话")

    resp = client.get(
        f"/portal/conversations/{cid}",
        headers={"Authorization": f"Bearer {other}"},
    )
    assert resp.status_code == 404
