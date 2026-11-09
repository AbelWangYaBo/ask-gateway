"""有界弹性 runtime 池：懒扩容、中央有界队列、空闲回收、上游组熔断、优雅关停。

线程模型：DSH SDK 全程阻塞，池用 threading 条件变量实现；FastAPI 侧在 worker 线程里
调用 acquire/run/release。三层背压中的后两层在这里——Key 并发槽在 QuotaService，
本池负责排队（有界）与实例硬上限。空闲实例用全局 LIFO，单租约场景天然热复用；
上游组轮询只决定新建实例落在哪个组。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ask_gateway.runtime_pool.errors import PoolClosing, PoolTimeout, RunnerBroken
from ask_gateway.runtime_pool.runner import Runner

HandleState = Literal["idle", "running", "broken"]


@dataclass(slots=True, eq=False)
class RuntimeHandle:
    runner: Runner
    label: str
    state: HandleState = "idle"
    started_at: float = 0.0
    last_used_at: float = 0.0
    uses: int = 0


@dataclass(slots=True)
class _GroupState:
    fail_streak: int = 0
    open_until: float = 0.0


class RuntimePool:
    def __init__(
        self,
        *,
        runner_factory: Callable[[str], Runner],
        labels: list[str],
        min_size: int,
        max_size: int,
        queue_maxsize: int,
        acquire_timeout: float,
        idle_ttl_seconds: float,
        reaper_interval_seconds: float,
        circuit_break_seconds: float,
        circuit_fail_threshold: int = 5,
    ) -> None:
        if not labels:
            raise ValueError("runtime pool requires at least one upstream label")
        self._factory = runner_factory
        self._labels = list(labels)
        self._min_size = min_size
        self._max_size = max_size
        self._queue_maxsize = queue_maxsize
        self._acquire_timeout = acquire_timeout
        self._idle_ttl = idle_ttl_seconds
        self._reaper_interval = reaper_interval_seconds
        self._break_seconds = circuit_break_seconds
        self._fail_threshold = circuit_fail_threshold

        self._lock = threading.Condition()
        self._groups: dict[str, _GroupState] = {label: _GroupState() for label in labels}
        self._idle: deque[RuntimeHandle] = deque()  # 全局 LIFO
        self._all: set[RuntimeHandle] = set()
        self._running: set[RuntimeHandle] = set()
        self._waiters = 0
        self._closing = False
        self._rr_cursor = 0
        self._reaper_thread: threading.Thread | None = None
        self._reaper_stop = threading.Event()

    # 时钟单独成方法，测试可替换为假时钟
    def clock(self) -> float:
        return time.monotonic()

    @property
    def idle_count(self) -> int:
        with self._lock:
            return len(self._idle)

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """预热 min_size 个实例（按上游组轮询分发）并启动 reaper 线程。"""
        with self._lock:
            if self._reaper_thread is not None:
                return
            for i in range(self._min_size):
                label = self._labels[i % len(self._labels)]
                handle = self._create_locked(label)
                handle.state = "idle"
                self._idle.append(handle)
        self._reaper_stop.clear()
        thread = threading.Thread(
            target=self._reaper_loop, name="runtime-pool-reaper", daemon=True
        )
        self._reaper_thread = thread
        thread.start()

    def shutdown(self, *, grace_seconds: float = 45.0) -> None:
        with self._lock:
            self._closing = True
            self._lock.notify_all()
        self._reaper_stop.set()
        if self._reaper_thread is not None:
            self._reaper_thread.join(timeout=self._reaper_interval + 1)

        with self._lock:
            deadline = self.clock() + grace_seconds
            while self._running and self.clock() < deadline:
                self._lock.wait(timeout=max(0.0, deadline - self.clock()))
            handles = list(self._all)
            self._all.clear()
            self._running.clear()
            self._idle.clear()
        for handle in handles:  # 子进程收割在锁外，避免 close 阻塞整条队列
            self._safe_close(handle)

    # ---------- 借还 ----------

    def acquire(self) -> RuntimeHandle:
        with self._lock:
            if self._closing:
                raise PoolClosing("pool is shutting down")
            deadline = self.clock() + self._acquire_timeout

            while True:
                handle = self._try_take_locked()
                if handle is not None:
                    return handle

                if self._waiters >= self._queue_maxsize:
                    raise PoolTimeout("acquire queue is full")
                if self.clock() >= deadline:
                    raise PoolTimeout("acquire timed out waiting for a runtime")

                self._waiters += 1
                try:
                    remaining = deadline - self.clock()
                    if not self._lock.wait(timeout=remaining):
                        raise PoolTimeout("acquire timed out waiting for a runtime")
                finally:
                    self._waiters -= 1
                if self._closing:
                    raise PoolClosing("pool is shutting down")

    def release(self, handle: RuntimeHandle, *, broken: bool = False) -> None:
        to_close: list[RuntimeHandle] = []
        with self._lock:
            self._running.discard(handle)
            is_broken = (
                broken
                or handle.state == "broken"
                or not handle.runner.health_alive()
            )
            if is_broken:
                handle.state = "broken"
                self._all.discard(handle)
                self._remove_idle_locked(handle)
                to_close.append(handle)
                self._record_failure_locked(handle.label)
            else:
                handle.state = "idle"
                handle.last_used_at = self.clock()
                group = self._groups[handle.label]
                group.fail_streak = 0
                group.open_until = 0.0
                self._idle.append(handle)
            self._lock.notify_all()
        for dead in to_close:
            self._safe_close(dead)

    # ---------- 回收 ----------

    def reap_once(self) -> list[Runner]:
        """回收超出 min_size 且空闲超 TTL 的实例，返回已关闭的 runner。"""
        now = self.clock()
        to_close: list[RuntimeHandle] = []
        with self._lock:
            surplus = len(self._idle) - self._min_size
            if surplus <= 0:
                return []
            # LIFO 队尾是最新空闲，从队首（最老）挑过期实例
            expired: list[RuntimeHandle] = []
            for handle in list(self._idle):
                if now - handle.last_used_at > self._idle_ttl:
                    expired.append(handle)
                    if len(expired) == surplus:
                        break
            for handle in expired:
                self._remove_idle_locked(handle)
                self._all.discard(handle)
                handle.state = "broken"
                to_close.append(handle)
        for handle in to_close:
            self._safe_close(handle)
        return [h.runner for h in to_close]

    def _reaper_loop(self) -> None:
        while not self._reaper_stop.wait(timeout=self._reaper_interval):
            try:
                self.reap_once()
            except Exception:  # 回收线程不能因单次异常死掉
                pass

    # ---------- 内部 ----------

    def _try_take_locked(self) -> RuntimeHandle | None:
        now = self.clock()
        healthy = [label for label in self._labels if self._is_healthy_locked(label, now)]
        if not healthy:
            raise RunnerBroken("all upstream key groups are circuit-broken")

        # 优先复用空闲实例：从 LIFO 栈顶找第一个健康组的存活实例
        stale: list[RuntimeHandle] = []
        while self._idle:
            handle = self._idle.pop()
            if not handle.runner.health_alive():
                self._all.discard(handle)
                stale.append(handle)
                continue
            if handle.label not in healthy:
                self._idle.appendleft(handle)  # 熔断组的实例先放回去
                continue
            self._mark_running(handle)
            self._defer_close(stale)
            return handle
        self._defer_close(stale)

        # 无空闲但未达硬上限：懒扩容，健康组间轮询
        if len(self._all) < self._max_size:
            label = healthy[self._rr_cursor % len(healthy)]
            self._rr_cursor = (self._rr_cursor + 1) % len(healthy)
            handle = self._create_locked(label)
            self._mark_running(handle)
            return handle
        return None

    def _defer_close(self, handles: list[RuntimeHandle]) -> None:
        # 调用方持锁；死实例的 close 可能阻塞，延后到锁外由调用路径处理
        for handle in handles:
            threading.Thread(target=self._safe_close, args=(handle,), daemon=True).start()

    def _mark_running(self, handle: RuntimeHandle) -> None:
        handle.state = "running"
        handle.uses += 1
        handle.last_used_at = self.clock()
        self._running.add(handle)

    def _create_locked(self, label: str) -> RuntimeHandle:
        runner = self._factory(label)
        runner.start()
        now = self.clock()
        handle = RuntimeHandle(
            runner=runner, label=label, started_at=now, last_used_at=now
        )
        self._all.add(handle)
        return handle

    def _remove_idle_locked(self, handle: RuntimeHandle) -> None:
        if handle in self._idle:
            self._idle.remove(handle)

    def _is_healthy_locked(self, label: str, now: float) -> bool:
        group = self._groups[label]
        if group.open_until and now >= group.open_until:
            # 冷却结束：半开，允许一次试探
            group.open_until = 0.0
            group.fail_streak = 0
        return now >= group.open_until

    def _record_failure_locked(self, label: str) -> None:
        group = self._groups[label]
        group.fail_streak += 1
        if group.fail_streak >= self._fail_threshold:
            group.open_until = self.clock() + self._break_seconds

    @staticmethod
    def _safe_close(handle: RuntimeHandle) -> None:
        try:
            handle.runner.close()
        except Exception:
            pass
