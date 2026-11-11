"""入口组装层离线测试：runner 工厂、无 Key 兜底、完整应用的 lifespan 启停。

不启动真实 DSH 子进程：无 UPSTREAM_KEYS 时池 min_size 强制为 0，start() 只起 reaper。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ask_gateway import main as main_module
from ask_gateway.config import Settings, get_settings
from ask_gateway.db import session as db_session
from ask_gateway.db.base import Base
from ask_gateway.main import (
    UpstreamControl,
    bootstrap_upstream_keys,
    create_application,
)
from ask_gateway.runtime_pool.dsh_runner import DshRunner
from ask_gateway.services import upstream_key_service
from ask_gateway.services.secret_box import SecretBox


@pytest.fixture
def bootstrap_settings(tmp_path, monkeypatch) -> Iterator[Settings]:
    """把全局 Settings/引擎单例重定向到临时库，建表后交还；测试后恢复全局。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/boot.db")
    monkeypatch.setenv("ADMIN_PASSWORD", "boot-pw")
    monkeypatch.delenv("UPSTREAM_KEYS", raising=False)
    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_SessionLocal", None)
    get_settings.cache_clear()
    settings = get_settings()
    Base.metadata.create_all(db_session.get_engine())
    yield settings
    get_settings.cache_clear()


def test_bootstrap_without_upstream_keys(bootstrap_settings: Settings) -> None:
    session_factory = db_session.get_session_factory()
    box = SecretBox(b"x" * 32)
    labels = bootstrap_upstream_keys(bootstrap_settings, session_factory, box)
    assert labels == []
    control = UpstreamControl(
        settings=bootstrap_settings, session_factory=session_factory, box=box
    )
    with pytest.raises(RuntimeError, match="上游 Key 不可用"):
        control.runner_factory("upstream-unconfigured")


def test_factory_with_upstream_key_builds_without_spawn(
    bootstrap_settings: Settings,
) -> None:
    session_factory = db_session.get_session_factory()
    box = SecretBox(b"x" * 32)
    with session_factory() as session:
        row = upstream_key_service.create_upstream_key(
            session, box, label="k1", plaintext="sk-fake-upstream-1"
        )
        session.commit()
        label = row.pool_label

    control = UpstreamControl(
        settings=bootstrap_settings, session_factory=session_factory, box=box
    )
    runner = control.runner_factory(label)
    assert isinstance(runner, DshRunner)
    assert runner.upstream_key_label == label
    assert runner._config.upstream_key == "sk-fake-upstream-1"
    # 构造不等于启动：未进入池 start() 前不得有存活子进程
    assert runner.health_alive() is False


def test_application_lifespan_routes_and_shutdown(
    bootstrap_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 仓库里提交了构建产物；这里显式模拟 dist 缺失以覆盖跳过挂载分支
    monkeypatch.setattr(main_module, "_STATIC_DIST", tmp_path / "no-such-dist")
    app = create_application(bootstrap_settings)
    pool = app.state.chat_runtime.pool
    assert pool._reaper_thread is None

    with TestClient(app) as client:
        # lifespan 已启动：reaper 线程存活，但 min_size=0 不拉起任何 runtime
        assert pool._reaper_thread is not None and pool._reaper_thread.is_alive()
        assert pool.running_count == 0

        assert client.get("/healthz").status_code == 200
        # SPA dist 缺失时挂载被跳过：根路径 404，且不吞 API 路由
        assert client.get("/").status_code == 404
        assert client.get("/api/admin/keys").status_code == 401

        # 完整组装链路上的管理登录（临时库已建表）
        ok = client.post("/api/admin/auth/login", json={"password": "boot-pw"})
        assert ok.status_code == 200
        token = ok.json()["token"]
        authed = client.get("/api/admin/keys", headers={"X-Admin-Token": token})
        assert authed.status_code == 200

    # 优雅关停后 reaper 退出
    assert pool._reaper_thread is not None and not pool._reaper_thread.is_alive()


def test_spa_mounted_with_index_fallback(
    bootstrap_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>spa-shell</title>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIST", dist)

    app = create_application(bootstrap_settings)
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "spa-shell" in root.text

        # 前端深层路由由 index.html 兜底
        deep = client.get("/requests")
        assert deep.status_code == 200
        assert deep.text == root.text

        # API 路由优先于 SPA 挂载，不会被兜底吞掉
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/admin/keys").status_code == 401
