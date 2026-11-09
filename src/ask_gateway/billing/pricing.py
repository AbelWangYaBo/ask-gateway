"""峰谷定价规则匹配与冲突校验。时间口径：北京时区 Asia/Shanghai。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.models.usage import PricingRule
from ask_gateway.services.errors import PricingConflictError

BJ_TZ = ZoneInfo("Asia/Shanghai")


def to_beijing(when: datetime | None = None) -> datetime:
    moment = when or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(BJ_TZ)


def rule_matches(rule: PricingRule, when: datetime | None = None) -> bool:
    """规则是否覆盖给定时刻：weekdays_only 周末不匹配；NULL 窗口为全时段。"""
    beijing = to_beijing(when)
    if rule.weekdays_only and beijing.weekday() >= 5:  # 5=周六 6=周日
        return False
    if rule.peak_start_hour is None or rule.peak_end_hour is None:
        return True
    # 区间 [peak_start_hour, peak_end_hour)，不支持跨午夜窗口
    return rule.peak_start_hour <= beijing.hour < rule.peak_end_hour


def select_rule(
    session: Session,
    model: str,
    when: datetime | None = None,
) -> PricingRule | None:
    """选生效规则：窗口规则优先于全时段规则，同类取 effective_from 最新者。"""
    beijing = to_beijing(when)
    candidates = list(
        session.scalars(
            select(PricingRule).where(
                PricingRule.model == model,
                PricingRule.is_active.is_(True),
                PricingRule.effective_from <= beijing,
            )
        ).all()
    )
    windowed: list[PricingRule] = []
    all_day: list[PricingRule] = []
    for rule in candidates:
        if not rule_matches(rule, beijing):
            continue
        if rule.peak_start_hour is None:
            all_day.append(rule)
        else:
            windowed.append(rule)

    pool = windowed or all_day
    if not pool:
        return None
    return max(pool, key=lambda r: r.effective_from)


def assert_no_active_overlap(
    session: Session,
    model: str,
    peak_start_hour: int | None,
    peak_end_hour: int | None,
    weekdays_only: bool,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """应用层唯一约束：同模型同时段只允许一条 active 规则。"""
    existing = list(
        session.scalars(
            select(PricingRule).where(
                PricingRule.model == model,
                PricingRule.is_active.is_(True),
            )
        ).all()
    )
    new_all_day = peak_start_hour is None
    for rule in existing:
        if exclude_id is not None and rule.id == exclude_id:
            continue
        if new_all_day:
            if rule.peak_start_hour is None:
                raise PricingConflictError(
                    f"model {model} 已存在全时段 active 规则：{rule.rule_name}"
                )
        elif (
            rule.peak_start_hour == peak_start_hour
            and rule.peak_end_hour == peak_end_hour
            and rule.weekdays_only == weekdays_only
        ):
            raise PricingConflictError(
                f"model {model} 已存在同时段 active 规则：{rule.rule_name}"
            )
