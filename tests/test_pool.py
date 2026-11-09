"""RuntimePool：懒扩容、复用、队列超时、broken 回收、reaper、熔断、优雅关停。"""

from __future__ import annotations

import time

import pytest
from fakes import FakeRunner, FakeRunnerFactory

from ask_gateway.runtime_pool.errors import (
    PoolClosing,
    PoolTimeout,
    RunnerBroken,
)
from ask_gateway.runtime_pool.pool import RuntimePool


def make_pool(factory: FakeRunnerFactory, labels: list[str] | None = None, **kw: object) -> RuntimePool:
    defaults = dict(
        min_size=1,
        max_size=2,
        queue_maxsize=2,
        acquire_timeout=0.3,
        idle_ttl_seconds=10.0,
        reaper_interval_seconds=100.0,
        circuit_break_seconds=300.0,
        circuit_fail_threshold=3,
    )
    defaults.update(kw)
    pool = RuntimePool(
        runner_factory=factory,
        labels=labels or ["key-a", "key-b"],
        **defaults,
    )
    pool.start()
    return pool


def test_prewarm_and_handle_reuse() -> None:
    factory = FakeRunnerFactory()
    # 单标签才能确定性地断言同一个句柄被 LIFO 复用；多标签路由由熔断测试覆盖
    pool = make_pool(factory, labels=["key-a"], min_size=2, max_size=3)
    try:
        assert factory.created_count == 2  # 预热到 min_size

        handle = pool.acquire()
        assert isinstance(handle.runner, FakeRunner)
        assert handle.state == "running"
        pool.release(handle)

        again = pool.acquire()
        assert again is handle  # 同一实例复用
        assert factory.created_count == 2
        pool.release(again)
    finally:
        pool.shutdown()


def test_grow_to_max_then_queue_timeout() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory, min_size=1, max_size=2, acquire_timeout=0.2)
    try:
        h1 = pool.acquire()
        h2 = pool.acquire()  # 懒扩容到上限
        assert factory.created_count == 2

        with pytest.raises(PoolTimeout):
            pool.acquire()  # 无空闲、达上限，等待超时

        pool.release(h1)
        h3 = pool.acquire()  # 归还后立刻可借
        assert h3 is h1
        pool.release(h2)
        pool.release(h3)
    finally:
        pool.shutdown()


def test_queue_full_rejects_immediately() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory, min_size=1, max_size=1, queue_maxsize=0, acquire_timeout=5)
    try:
        handle = pool.acquire()
        start = time.monotonic()
        with pytest.raises(PoolTimeout):
            pool.acquire()
        assert time.monotonic() - start < 0.1  # 队列硬上限，不等
        pool.release(handle)
    finally:
        pool.shutdown()


def test_broken_handle_destroyed_and_replaced() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory)
    try:
        handle = pool.acquire()
        runner = handle.runner
        pool.release(handle, broken=True)
        assert runner.closed is True

        replacement = pool.acquire()
        assert replacement.runner is not runner
        assert factory.created_count >= 2
        pool.release(replacement)
    finally:
        pool.shutdown()


def test_dead_idle_handle_is_recycled_on_acquire() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory)
    try:
        handle = pool.acquire()
        pool.release(handle)
        handle.runner.closed = True  # 模拟进程已死

        fresh = pool.acquire()
        assert fresh is not handle
        assert fresh.runner.closed is False
        pool.release(fresh)
    finally:
        pool.shutdown()


def test_reaper_shrinks_idle_to_min_size() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory, min_size=1, max_size=4, idle_ttl_seconds=10.0)
    try:
        handles = [pool.acquire() for _ in range(3)]
        for h in handles:
            pool.release(h)
        assert pool.idle_count == 3

        now = pool.clock()
        for h in handles:
            h.last_used_at = now - 11  # 全部超过 TTL
        closed = pool.reap_once()
        assert len(closed) == 2  # 回收到 min_size=1
        assert pool.idle_count == 1
        assert all(runner.closed for runner in closed)

        # 再跑一次不会低于 min_size
        assert pool.reap_once() == []
    finally:
        pool.shutdown()


def test_reaper_keeps_fresh_idle() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory, min_size=1, max_size=3, idle_ttl_seconds=10.0)
    try:
        handles = [pool.acquire() for _ in range(2)]
        for h in handles:
            pool.release(h)
        assert pool.reap_once() == []  # 都没超 TTL
        assert pool.idle_count == 2
    finally:
        pool.shutdown()


def test_circuit_breaker_routes_to_healthy_label() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(
        factory,
        labels=["key-a", "key-b"],
        min_size=0,
        max_size=4,
        circuit_fail_threshold=2,
    )
    try:
        # 预热为 0，新建在健康组间轮询：key-a、key-b、key-a
        h_a1 = pool.acquire()
        h_b = pool.acquire()
        h_a2 = pool.acquire()
        assert [h.label for h in (h_a1, h_b, h_a2)] == ["key-a", "key-b", "key-a"]
        pool.release(h_b)  # key-b 健康归还，留作后续路由目标
        pool.release(h_a1, broken=True)
        pool.release(h_a2, broken=True)  # key-a 连续两次失败，触发熔断

        # key-a 熔断，后续全部路由到 key-b（含复用刚归还的 key-b 空闲实例）
        for _ in range(2):
            handle = pool.acquire()
            assert handle.label == "key-b"
            pool.release(handle)
    finally:
        pool.shutdown()


def test_circuit_half_opens_after_cooldown() -> None:
    clock = {"t": 1000.0}
    factory = FakeRunnerFactory()
    pool = make_pool(
        factory,
        labels=["key-a"],
        min_size=0,
        max_size=2,
        circuit_fail_threshold=1,
        circuit_break_seconds=60.0,
    )
    pool.clock = lambda: clock["t"]  # type: ignore[method-assign]
    try:
        handle = pool.acquire()
        pool.release(handle, broken=True)  # 立即熔断

        with pytest.raises(RunnerBroken):
            pool.acquire()  # 唯一上游组熔断中

        clock["t"] += 61  # 冷却过去，半开试探
        recovered = pool.acquire()
        assert recovered.label == "key-a"
        pool.release(recovered)
    finally:
        pool.shutdown()


def test_shutdown_closes_all_and_rejects_new_acquire() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory, min_size=2)
    runners = list(factory.runners)
    pool.shutdown(grace_seconds=0.1)

    assert all(r.closed for r in runners)
    with pytest.raises(PoolClosing):
        pool.acquire()


def test_shutdown_waits_for_running_within_grace() -> None:
    factory = FakeRunnerFactory()
    pool = make_pool(factory, min_size=1, max_size=1)
    handle = pool.acquire()  # 故意不还，模拟在跑
    start = time.monotonic()
    pool.shutdown(grace_seconds=0.2)
    assert time.monotonic() - start >= 0.15
    assert handle.runner.closed  #  grace 后强制收割
