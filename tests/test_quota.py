"""配额服务：RPM 滑动窗口、并发槽、日预算（北京日界）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.request import RequestRecord
from ask_gateway.services.errors import QuotaError
from ask_gateway.services.quota_service import QuotaService


def _key(**overrides: object) -> ApiKey:
    # 显式给 id：未 flush 的对象不会触发列默认值，内存配额表按 id 隔离
    return ApiKey(
        id=uuid.uuid4(),
        key_prefix="sk-ask-ab12",
        key_hash="x" * 64,
        holder_name="user-a",
        label="个人",
        **overrides,
    )


def _request_row(session: Session, key: ApiKey, cost: str, created_at: datetime) -> None:
    session.add(
        RequestRecord(
            api_key_id=key.id,
            holder_name=key.holder_name,
            key_label=key.label,
            model="deepseek-flash",
            stream=False,
            prompt_messages=[],
            queued_at=created_at,
            total_cost_cents=Decimal(cost),
            created_at=created_at,
        )
    )


def test_rpm_sliding_window_uses_key_override() -> None:
    quota = QuotaService(default_rpm=30, default_daily_budget_cents=10000, default_concurrency=4)
    key = _key(rpm_limit=2)

    quota.check_rpm(key, now=1000.0)
    quota.check_rpm(key, now=1001.0)
    with pytest.raises(QuotaError) as exc:
        quota.check_rpm(key, now=1002.0)
    assert exc.value.code == "rpm_exceeded"

    # 滑出 60s 窗口后恢复
    quota.check_rpm(key, now=1061.0)


def test_rpm_falls_back_to_default() -> None:
    quota = QuotaService(default_rpm=1, default_daily_budget_cents=10000, default_concurrency=4)
    key = _key()
    quota.check_rpm(key, now=0.0)
    with pytest.raises(QuotaError):
        quota.check_rpm(key, now=1.0)
    quota.check_rpm(key, now=61.0)


def test_rpm_windows_are_isolated_per_key() -> None:
    quota = QuotaService(default_rpm=1, default_daily_budget_cents=10000, default_concurrency=4)
    key1, key2 = _key(), _key()
    quota.check_rpm(key1, now=0.0)
    quota.check_rpm(key2, now=0.0)  # 互不影响
    with pytest.raises(QuotaError):
        quota.check_rpm(key1, now=1.0)


def test_concurrency_slots_reject_at_limit() -> None:
    quota = QuotaService(default_rpm=30, default_daily_budget_cents=10000, default_concurrency=2)
    key = _key()

    quota.acquire_slot(key)
    quota.acquire_slot(key)
    with pytest.raises(QuotaError) as exc:
        quota.acquire_slot(key)
    assert exc.value.code == "concurrency_exceeded"

    quota.release_slot(key.id)
    quota.acquire_slot(key)  # 归还后可再获取


def test_concurrency_custom_limit_and_isolation() -> None:
    quota = QuotaService(default_rpm=30, default_daily_budget_cents=10000, default_concurrency=1)
    key = _key(max_concurrency=3)
    for _ in range(3):
        quota.acquire_slot(key)
    with pytest.raises(QuotaError):
        quota.acquire_slot(key)

    # 另一把 Key 有独立槽位
    other = _key()
    quota.acquire_slot(other)


def test_release_unknown_key_is_noop() -> None:
    quota = QuotaService(default_rpm=30, default_daily_budget_cents=10000, default_concurrency=2)
    quota.release_slot(uuid.uuid4())  # 不抛异常


def test_daily_budget_sum_today_only(db_session: Session) -> None:
    quota = QuotaService(default_rpm=30, default_daily_budget_cents=10000, default_concurrency=4)
    key = _key()
    db_session.add(key)
    db_session.flush()

    now = datetime.now(UTC)
    _request_row(db_session, key, "6000.000", now - timedelta(hours=1))
    _request_row(db_session, key, "5000.000", now - timedelta(days=3))
    db_session.commit()

    # 当日累计 6000，预算 10000，放行
    quota.check_daily_budget(db_session, key, now=now)

    _request_row(db_session, key, "5000.000", now - timedelta(minutes=5))
    db_session.commit()
    with pytest.raises(QuotaError) as exc:
        quota.check_daily_budget(db_session, key, now=now)
    assert exc.value.code == "daily_budget_exceeded"


def test_daily_budget_custom_limit(db_session: Session) -> None:
    quota = QuotaService(default_rpm=30, default_daily_budget_cents=10000, default_concurrency=4)
    key = _key(daily_budget_cents=100)
    db_session.add(key)
    db_session.flush()
    _request_row(db_session, key, "100.000", datetime.now(UTC))
    db_session.commit()

    with pytest.raises(QuotaError):
        quota.check_daily_budget(db_session, key)
