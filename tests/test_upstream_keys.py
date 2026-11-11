"""上游 Key 动态管理：加密、指纹比对、CRUD REST 与池即时重配置。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fakes import FakeRunner
from fastapi.testclient import TestClient

from ask_gateway.config import Settings
from ask_gateway.db import session as db_session
from ask_gateway.db.base import Base
from ask_gateway.runtime_pool.pool import RuntimePool
from ask_gateway.runtime_pool.runner import Runner
from ask_gateway.services import upstream_key_service
from ask_gateway.services.secret_box import SecretBox, build_secret_box

MASTER = b"0123456789abcdef0123456789abcdef"


@pytest.fixture
def box() -> SecretBox:
    return SecretBox(MASTER)


class _FakeFactory:
    """按 label 记账的 FakeRunner 工厂，供池 reconfigure 断言。"""

    def __init__(self) -> None:
        self.created: list[str] = []

    def __call__(self, label: str) -> Runner:
        self.created.append(label)
        return FakeRunner(upstream_key_label=label)


# ---- secret_box ----


def test_secret_box_roundtrip_and_fingerprint(box: SecretBox) -> None:
    plaintext = "sk-real-deepseek-key-9f8e7d"
    sealed = box.encrypt(plaintext)
    assert sealed != plaintext and "sk-real" not in sealed
    assert box.decrypt(sealed) == plaintext
    # 同一明文指纹稳定；不同明文指纹不同
    assert SecretBox.fingerprint(plaintext) == SecretBox.fingerprint(plaintext)
    assert SecretBox.fingerprint(plaintext) != SecretBox.fingerprint("sk-other")
    assert len(SecretBox.fingerprint(plaintext)) == 16
    assert SecretBox.preview(plaintext) == "sk-rea…8e7d"


def test_secret_box_rejects_bad_master_key() -> None:
    with pytest.raises(Exception):
        SecretBox(b"short")
    other = SecretBox(b"z" * 32)
    sealed = SecretBox(MASTER).encrypt("sk-x")
    with pytest.raises(Exception, match="密文损坏|主密钥"):
        other.decrypt(sealed)


def test_build_secret_box_env_override(tmp_path, monkeypatch) -> None:
    import base64

    key = base64.b64encode(b"a" * 32).decode()
    monkeypatch.setenv("SECRETS_MASTER_KEY", key)
    settings = Settings(data_dir=tmp_path)
    assert build_secret_box(settings).encrypt("sk-1")

    monkeypatch.setenv("SECRETS_MASTER_KEY", "not-base64!!")
    with pytest.raises(Exception, match="SECRETS_MASTER_KEY"):
        build_secret_box(settings)


def test_build_secret_box_autogenerates_file_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    settings = Settings(data_dir=tmp_path)
    b1 = build_secret_box(settings)
    sealed = b1.encrypt("sk-2")
    # 二次构建复用落盘主密钥：旧密文仍可解
    assert build_secret_box(settings).decrypt(sealed) == "sk-2"
    assert (tmp_path / "secrets" / "master_key").stat().st_mode & 0o777 == 0o600


# ---- 服务层 ----


@pytest.fixture
def db(tmp_path, monkeypatch) -> Iterator[type[db_session]]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/up.db")
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    monkeypatch.delenv("UPSTREAM_KEYS", raising=False)
    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_SessionLocal", None)
    Base.metadata.create_all(db_session.get_engine())
    yield db_session
    from ask_gateway.config import get_settings

    get_settings.cache_clear()


def test_create_dedup_by_fingerprint(db, box: SecretBox) -> None:
    factory = db.get_session_factory()
    with factory() as s:
        row = upstream_key_service.create_upstream_key(
            s, box, label="main", plaintext="sk-dedup-key-1"
        )
        s.commit()
        with pytest.raises(upstream_key_service.UpstreamKeyExistsError):
            upstream_key_service.create_upstream_key(
                s, box, label="again", plaintext="sk-dedup-key-1"
            )
        # 密文/指纹/preview 落库形态
        assert row.key_ciphertext != "sk-dedup-key-1"
        assert row.key_fingerprint == SecretBox.fingerprint("sk-dedup-key-1")
        assert row.key_preview == "sk-ded…ey-1"


def test_plaintext_for_label_roundtrip(db, box: SecretBox) -> None:
    factory = db.get_session_factory()
    with factory() as s:
        row = upstream_key_service.create_upstream_key(
            s, box, label="main", plaintext="sk-roundtrip-key"
        )
        s.commit()
        label = row.pool_label
    with factory() as s:
        assert upstream_key_service.plaintext_for_label(s, box, label) == "sk-roundtrip-key"
        assert upstream_key_service.active_pool_labels(s) == [label]
        # 停用后：池组消失、明文不可取
        upstream_key_service.update_upstream_key(s, row.id, is_active=False)
        s.commit()
        assert upstream_key_service.active_pool_labels(s) == []
        assert upstream_key_service.plaintext_for_label(s, box, label) is None


def test_seed_from_env_once(db, box: SecretBox) -> None:
    factory = db.get_session_factory()
    with factory() as s:
        n = upstream_key_service.seed_from_env_once(s, box, ["sk-env-1", "sk-env-2"])
        s.commit()
        assert n == 2
    with factory() as s:
        # 已有数据时 env 不再覆盖
        assert upstream_key_service.seed_from_env_once(s, box, ["sk-env-3"]) == 0
        labels = upstream_key_service.active_pool_labels(s)
        assert len(labels) == 2


# ---- 池 reconfigure ----


def test_pool_reconfigure_add_and_remove() -> None:
    pool = RuntimePool(
        runner_factory=_FakeFactory(),
        labels=["a", "b"],
        min_size=0,
        max_size=4,
        queue_maxsize=8,
        acquire_timeout=1.0,
        idle_ttl_seconds=60,
        reaper_interval_seconds=60,
        circuit_break_seconds=60,
    )
    pool.start()
    h1 = pool.acquire()
    assert h1.label in {"a", "b"}
    pool.release(h1)
    assert pool.idle_count == 1

    # 增组：空闲保留，新组随扩容生效
    pool.reconfigure(["a", "b", "c"])
    h2 = pool.acquire()  # 复用空闲
    h3 = pool.acquire()  # 懒扩容轮询命中新组的机会
    assert h3.label in {"a", "b", "c"}
    pool.release(h2)
    pool.release(h3)

    # 删组 a：a 的空闲立即回收，其他不受影响
    pool.reconfigure(["b", "c"])
    idle_labels = {h.label for h in pool._idle}
    assert "a" not in idle_labels
    pool.shutdown()


def test_pool_release_of_removed_group_recycles() -> None:
    factory = _FakeFactory()
    pool = RuntimePool(
        runner_factory=factory,
        labels=["keep", "drop"],
        min_size=0,
        max_size=4,
        queue_maxsize=8,
        acquire_timeout=1.0,
        idle_ttl_seconds=60,
        reaper_interval_seconds=60,
        circuit_break_seconds=60,
    )
    pool.start()
    handles = [pool.acquire() for _ in range(4)]
    drop_handle = next(h for h in handles if h.label == "drop")
    pool.reconfigure(["keep"])
    # 在途实例归还时被回收，不回空闲栈
    pool.release(drop_handle)
    assert drop_handle not in pool._idle
    assert drop_handle.state == "broken"
    for h in handles:
        if h is not drop_handle:
            pool.release(h)
    pool.shutdown()


# ---- REST 层 ----


@pytest.fixture
def client(db, monkeypatch, tmp_path) -> Iterator[TestClient]:
    from ask_gateway.main import UpstreamControl, create_application

    settings = Settings(data_dir=tmp_path, admin_password="rest-pw")
    box = build_secret_box(settings)
    factory = db.get_session_factory()
    control = UpstreamControl(settings=settings, session_factory=factory, box=box)
    pool = RuntimePool(
        runner_factory=control.runner_factory,
        labels=["upstream-unconfigured"],
        min_size=0,
        max_size=2,
        queue_maxsize=4,
        acquire_timeout=1.0,
        idle_ttl_seconds=60,
        reaper_interval_seconds=60,
        circuit_break_seconds=60,
    )
    app = create_application(settings)
    app.state.upstream_control = control
    app.state.pool = pool
    with TestClient(app) as c:
        c.app.state.pool = pool  # lifespan 后维持注入
        yield c, pool
    pool.shutdown()


def _auth(client: TestClient) -> dict[str, str]:
    token = client.post("/api/admin/auth/login", json={"password": "rest-pw"}).json()["token"]
    return {"X-Admin-Token": token}


def test_upstream_key_crud_flow(client) -> None:
    c, pool = client
    headers = _auth(c)

    # 创建：响应只含指纹/preview，绝无明文
    created = c.post(
        "/api/admin/upstream-keys",
        json={"label": "main", "plaintext": "sk-crud-test-key"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["key_fingerprint"] == SecretBox.fingerprint("sk-crud-test-key")
    assert body["key_preview"] == "sk-cru…-key"
    assert "plaintext" not in body and "sk-crud-test-key" not in created.text
    key_id = body["id"]

    # 池即时生效：分组已更新
    assert pool._labels == [f"upstream-{key_id.replace('-', '')[:8]}"]

    # 重复添加同一明文 → 409
    dup = c.post(
        "/api/admin/upstream-keys",
        json={"label": "dup", "plaintext": "sk-crud-test-key"},
        headers=headers,
    )
    assert dup.status_code == 409

    # 停用：池组即时移除
    patched = c.patch(
        f"/api/admin/upstream-keys/{key_id}", json={"is_active": False}, headers=headers
    )
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    assert pool._labels == []

    # 删除：204；未授权访问 401
    assert c.delete(f"/api/admin/upstream-keys/{key_id}", headers=headers).status_code == 204
    assert c.get("/api/admin/upstream-keys").status_code == 401

    # 管理操作日志留痕
    token = c.post("/api/admin/auth/login", json={"password": "rest-pw"}).json()["token"]
    logs = c.get("/api/admin/audit-logs", headers={"X-Admin-Token": token}).json()
    actions = {item["action"] for item in logs["items"]}
    assert {"upstream_key.create", "upstream_key.update", "upstream_key.delete"} <= actions
