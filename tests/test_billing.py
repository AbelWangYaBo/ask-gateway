"""峰谷规则匹配、冲突校验与 usage 计费。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from ask_gateway.billing.cost import compute_cost, snapshot_rule
from ask_gateway.billing.pricing import (
    PricingConflictError,
    assert_no_active_overlap,
    rule_matches,
    select_rule,
)
from ask_gateway.models.usage import PricingRule

BJ = ZoneInfo("Asia/Shanghai")


def _rule(
    model: str = "deepseek-flash",
    *,
    rule_name: str = "全时段",
    peak_start: int | None = None,
    peak_end: int | None = None,
    weekdays_only: bool = False,
    is_active: bool = True,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    priority: int = 0,
    input_price: str = "200.0000",
    output_price: str = "800.0000",
    reasoning_price: str | None = None,
    cache_read_price: str | None = None,
    cache_write_price: str | None = None,
    search_price: str = "0",
) -> PricingRule:
    return PricingRule(
        model=model,
        rule_name=rule_name,
        peak_start_hour=peak_start,
        peak_end_hour=peak_end,
        weekdays_only=weekdays_only,
        is_active=is_active,
        effective_from=effective_from or datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=effective_to,
        priority=priority,
        input_price_per_mtok=Decimal(input_price),
        output_price_per_mtok=Decimal(output_price),
        reasoning_price_per_mtok=Decimal(reasoning_price) if reasoning_price else None,
        cache_read_price_per_mtok=Decimal(cache_read_price) if cache_read_price else None,
        cache_write_price_per_mtok=Decimal(cache_write_price) if cache_write_price else None,
        search_price_per_k=Decimal(search_price),
    )


def _bj(day: str, hour: int) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=BJ)


# ---------- 规则匹配 ----------

def test_all_day_rule_matches_any_hour() -> None:
    rule = _rule()
    assert rule_matches(rule, _bj("2026-11-09", 3))
    assert rule_matches(rule, _bj("2026-11-09", 23))


def test_peak_window_boundaries() -> None:
    # 2026-11-09 是周一；高峰 [10, 12)
    rule = _rule(rule_name="高峰", peak_start=10, peak_end=12)
    assert rule_matches(rule, _bj("2026-11-09", 10))
    assert rule_matches(rule, _bj("2026-11-09", 11))
    assert not rule_matches(rule, _bj("2026-11-09", 9))
    assert not rule_matches(rule, _bj("2026-11-09", 12))


def test_weekdays_only_skips_weekend() -> None:
    # 2026-11-07 是周六
    rule = _rule(rule_name="工作日高峰", peak_start=9, peak_end=18, weekdays_only=True)
    assert not rule_matches(rule, _bj("2026-11-07", 10))
    assert rule_matches(rule, _bj("2026-11-09", 10))


def test_select_rule_prefers_peak_over_all_day(db_session: Session) -> None:
    all_day = _rule(rule_name="全时段")
    peak = _rule(rule_name="晚高峰", peak_start=19, peak_end=23)
    db_session.add_all([all_day, peak])
    db_session.flush()

    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-11-09", 20))
    assert chosen is not None and chosen.rule_name == "晚高峰"
    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-11-09", 10))
    assert chosen is not None and chosen.rule_name == "全时段"


def test_select_rule_weekend_falls_back_to_all_day(db_session: Session) -> None:
    db_session.add_all(
        [
            _rule(rule_name="全时段"),
            _rule(rule_name="工作日高峰", peak_start=9, peak_end=18, weekdays_only=True),
        ]
    )
    db_session.flush()
    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-11-07", 10))
    assert chosen is not None and chosen.rule_name == "全时段"


def test_select_rule_respects_effective_from_and_model(db_session: Session) -> None:
    future = _rule(effective_from=datetime(2030, 1, 1, tzinfo=UTC))
    db_session.add(future)
    db_session.flush()
    assert select_rule(db_session, "deepseek-flash", _bj("2026-11-09", 10)) is None
    assert select_rule(db_session, "other-model", _bj("2026-11-09", 10)) is None


def test_select_rule_priority_overrides_lower_weight(db_session: Session) -> None:
    """临时优惠（权重 1）在生效周期内整体覆盖常规价（权重 0），到期回落。"""
    regular = _rule(rule_name="常规价")
    promo = _rule(
        rule_name="双11优惠",
        effective_from=datetime(2026, 11, 11, tzinfo=UTC),
        effective_to=datetime(2026, 11, 12, tzinfo=UTC),
        priority=1,
        input_price="50.0000",
    )
    db_session.add_all([regular, promo])
    db_session.flush()

    # 优惠期内：优惠覆盖常规（也覆盖窗口规则）
    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-11-11", 15))
    assert chosen is not None and chosen.rule_name == "双11优惠"
    # 优惠期外：回落常规
    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-11-13", 15))
    assert chosen is not None and chosen.rule_name == "常规价"


def test_select_rule_future_price_switches_at_from(db_session: Session) -> None:
    """提前录入未来调价：effective_from 到点自动切换，无需人工改库。"""
    old = _rule(rule_name="现行价", effective_from=datetime(2026, 1, 1, tzinfo=UTC))
    new = _rule(
        rule_name="12月新价",
        effective_from=datetime(2026, 12, 1, tzinfo=UTC),
        input_price="300.0000",
    )
    db_session.add_all([old, new])
    db_session.flush()

    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-11-30", 23))
    assert chosen is not None and chosen.rule_name == "现行价"
    chosen = select_rule(db_session, "deepseek-flash", _bj("2026-12-01", 0))
    assert chosen is not None and chosen.rule_name == "12月新价"


def test_select_rule_ignores_inactive(db_session: Session) -> None:
    db_session.add(_rule(is_active=False))
    db_session.flush()
    assert select_rule(db_session, "deepseek-flash") is None


def test_overlap_validation(db_session: Session) -> None:
    seed_from = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.add(_rule(effective_from=seed_from))
    db_session.flush()

    # 同窗口同权重同生效起点 → 冲突
    with pytest.raises(PricingConflictError):
        assert_no_active_overlap(
            db_session, "deepseek-flash", None, None, False, effective_from=seed_from
        )

    # 同窗口同权重但生效起点不同 → 版本演进（提前录入未来调价），允许
    assert_no_active_overlap(
        db_session,
        "deepseek-flash",
        None,
        None,
        False,
        effective_from=datetime(2026, 12, 1, tzinfo=UTC),
    )

    # 同窗口同起点但权重不同 → 临时优惠叠加，允许
    assert_no_active_overlap(
        db_session, "deepseek-flash", None, None, False, effective_from=seed_from, priority=1
    )

    # 不同时段窗口不冲突
    db_session.add(_rule(rule_name="晚高峰", peak_start=19, peak_end=23))
    db_session.flush()
    assert_no_active_overlap(db_session, "deepseek-flash", 10, 12, False)
    # 同窗口同起点冲突
    with pytest.raises(PricingConflictError):
        assert_no_active_overlap(
            db_session,
            "deepseek-flash",
            19,
            23,
            False,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    # 不同模型不冲突
    assert_no_active_overlap(db_session, "other-model", None, None, False)


# ---------- 计费 ----------

def test_snapshot_carries_prices_as_strings() -> None:
    rule = _rule(
        reasoning_price="1200.0000",
        cache_read_price="20.0000",
        cache_write_price="100.0000",
        search_price="50.0000",
    )
    snapshot = snapshot_rule(rule)
    assert snapshot["model"] == "deepseek-flash"
    assert snapshot["input_price_per_mtok"] == "200.0000"
    assert snapshot["reasoning_price_per_mtok"] == "1200.0000"
    assert snapshot["search_price_per_k"] == "50.0000"
    price_fields = [
        "input_price_per_mtok",
        "output_price_per_mtok",
        "reasoning_price_per_mtok",
        "cache_read_price_per_mtok",
        "cache_write_price_per_mtok",
        "search_price_per_k",
    ]
    assert all(isinstance(snapshot[name], str) for name in price_fields)


def test_compute_cost_basic_tokens() -> None:
    snapshot = snapshot_rule(_rule(input_price="200.0000", output_price="800.0000"))
    cost = compute_cost(
        complete=True,
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=None,
        search_count=0,
        snapshot=snapshot,
    )
    # 200 + 400 = 600 分
    assert cost == Decimal("600.000")


def test_compute_cost_reasoning_only_charged_when_priced() -> None:
    snapshot = snapshot_rule(_rule(input_price="200", output_price="800", reasoning_price="1200"))
    cost = compute_cost(
        complete=True,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=1_000_000,
        search_count=0,
        snapshot=snapshot,
    )
    assert cost == Decimal("1200.000")

    snapshot_no_reasoning = snapshot_rule(_rule(input_price="200", output_price="800"))
    cost = compute_cost(
        complete=True,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=1_000_000,
        search_count=0,
        snapshot=snapshot_no_reasoning,
    )
    assert cost == Decimal("0.000")


def test_compute_cost_cache_and_search() -> None:
    snapshot = snapshot_rule(
        _rule(
            input_price="200",
            output_price="800",
            cache_read_price="20",
            cache_write_price="100",
            search_price="50",
        )
    )
    cost = compute_cost(
        complete=True,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=2_000_000,
        cache_write_tokens=1_000_000,
        reasoning_tokens=None,
        search_count=2500,
        snapshot=snapshot,
    )
    # 40 + 100 + 2.5*50=125 → 265
    assert cost == Decimal("265.000")


def test_incomplete_usage_is_not_billed() -> None:
    snapshot = snapshot_rule(_rule())
    cost = compute_cost(
        complete=False,
        input_tokens=10_000_000,
        output_tokens=10_000_000,
        cache_read_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=None,
        search_count=99,
        snapshot=snapshot,
    )
    assert cost == Decimal("0.000")
