"""配额判定：RPM 滑动窗口、并发槽、日预算。

运行期计数（RPM 窗口、在途并发）是进程内状态，用 threading.Lock 保护——
DSH SDK 是阻塞式调用，问答管线会在 worker 线程里跑，同步 API 比绑定事件循环的
asyncio.Semaphore 更通用；拒绝语义不变（超限制立即抛 QuotaError，排队由 runtime 池负责）。
日预算是持久口径，查 requests 当日 total_cost_cents 汇总，日界按北京时间。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.request import RequestRecord
from ask_gateway.services.errors import QuotaError

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_WINDOW_SECONDS = 60.0


class QuotaService:
    def __init__(
        self,
        *,
        default_rpm: int,
        default_daily_budget_cents: int,
        default_concurrency: int,
    ) -> None:
        self._default_rpm = default_rpm
        self._default_budget = default_daily_budget_cents
        self._default_concurrency = default_concurrency
        self._rpm_windows: dict[uuid.UUID, deque[float]] = {}
        self._inflight: dict[uuid.UUID, int] = {}
        self._lock = threading.Lock()

    def effective_rpm(self, key: ApiKey) -> int:
        return key.rpm_limit if key.rpm_limit is not None else self._default_rpm

    def effective_concurrency(self, key: ApiKey) -> int:
        return (
            key.max_concurrency
            if key.max_concurrency is not None
            else self._default_concurrency
        )

    def effective_budget(self, key: ApiKey) -> int:
        return (
            key.daily_budget_cents
            if key.daily_budget_cents is not None
            else self._default_budget
        )

    def check_rpm(self, key: ApiKey, *, now: float | None = None) -> None:
        current = now if now is not None else _monotonic()
        limit = self.effective_rpm(key)
        with self._lock:
            window = self._rpm_windows.setdefault(key.id, deque())
            cutoff = current - _WINDOW_SECONDS
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= limit:
                raise QuotaError(
                    "rpm_exceeded",
                    f"rate limit exceeded: {limit} requests per minute",
                )
            window.append(current)

    def acquire_slot(self, key: ApiKey) -> None:
        limit = self.effective_concurrency(key)
        with self._lock:
            current = self._inflight.get(key.id, 0)
            if current >= limit:
                raise QuotaError(
                    "concurrency_exceeded",
                    f"concurrency limit exceeded: {limit} in-flight requests",
                )
            self._inflight[key.id] = current + 1

    def release_slot(self, key_id: uuid.UUID) -> None:
        with self._lock:
            current = self._inflight.get(key_id, 0)
            if current <= 1:
                self._inflight.pop(key_id, None)
            else:
                self._inflight[key_id] = current - 1

    def check_daily_budget(
        self,
        session: Session,
        key: ApiKey,
        *,
        now: datetime | None = None,
    ) -> Decimal:
        """当日（北京日界）累计费用达到预算即拒绝，返回当前累计。"""
        spent = self.today_spent_cents(session, key.id, now=now)
        budget = self.effective_budget(key)
        if spent >= Decimal(budget):
            raise QuotaError(
                "daily_budget_exceeded",
                f"daily budget exhausted: {spent} / {budget} cents",
            )
        return spent

    def today_spent_cents(
        self,
        session: Session,
        key_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> Decimal:
        start_utc, end_utc = _beijing_day_bounds_utc(now or datetime.now(UTC))
        total = session.scalar(
            select(func.coalesce(func.sum(RequestRecord.total_cost_cents), 0)).where(
                RequestRecord.api_key_id == key_id,
                RequestRecord.created_at >= start_utc,
                RequestRecord.created_at < end_utc,
            )
        )
        return Decimal(total)  # type: ignore[arg-type]


def _beijing_day_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    beijing_now = now.astimezone(BEIJING_TZ)
    start_bj = datetime(
        beijing_now.year, beijing_now.month, beijing_now.day, tzinfo=BEIJING_TZ
    )
    end_bj = start_bj + timedelta(days=1)
    return start_bj.astimezone(UTC), end_bj.astimezone(UTC)


def _monotonic() -> float:
    return time.monotonic()
