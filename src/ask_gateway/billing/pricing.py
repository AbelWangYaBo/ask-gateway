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


def _as_utc(dt: datetime) -> datetime:
    """sqlite 读回可能丢时区；统一按 UTC 解释再比较。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def rule_matches(rule: PricingRule, when: datetime | None = None) -> bool:
    """规则是否覆盖给定时刻：生效周期内 + weekdays_only 周末不匹配；NULL 窗口为全时段。"""
    beijing = to_beijing(when)
    if rule.effective_to is not None and beijing >= _as_utc(rule.effective_to):
        return False
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
    """选生效规则：权重(priority)大者胜；平权重时窗口规则优先于全时段，
    再平取 effective_from 最新（支持提前录入未来调价，到点自动切换）。
    """
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
    matched: list[PricingRule] = []
    for rule in candidates:
        if rule_matches(rule, beijing):
            matched.append(rule)

    if not matched:
        return None
    return max(
        matched,
        key=lambda r: (r.priority, r.peak_start_hour is not None, r.effective_from),
    )


def assert_no_active_overlap(
    session: Session,
    model: str,
    peak_start_hour: int | None,
    peak_end_hour: int | None,
    weekdays_only: bool,
    *,
    exclude_id: uuid.UUID | None = None,
    effective_from: datetime | None = None,
    priority: int = 0,
) -> None:
    """应用层约束：同模型 + 同窗口 + 同权重 + 同生效起点 只允许一条 active。

    其余重叠均放行——胜负可预测即合法叠加：
    - 不同权重分层覆盖（常规价 0 + 临时优惠 1，权重高者在周期内整体生效）；
    - 同权重不同 effective_from 视为版本演进：提前录入未来调价，到点自动
      切换（select_rule 取 from 最新者），无需人工守点改价。
    """
    existing = list(
        session.scalars(
            select(PricingRule).where(
                PricingRule.model == model,
                PricingRule.is_active.is_(True),
            )
        ).all()
    )
    new_from = _as_utc(effective_from or datetime.now(UTC))
    new_all_day = peak_start_hour is None
    for rule in existing:
        if exclude_id is not None and rule.id == exclude_id:
            continue
        same_window = (
            (new_all_day and rule.peak_start_hour is None)
            or (
                not new_all_day
                and rule.peak_start_hour == peak_start_hour
                and rule.peak_end_hour == peak_end_hour
                and rule.weekdays_only == weekdays_only
            )
        )
        if not same_window or rule.priority != priority:
            continue
        if _as_utc(rule.effective_from) != new_from:
            continue
        label = "全时段" if new_all_day else f"{peak_start_hour}-{peak_end_hour}"
        raise PricingConflictError(
            f"model {model} 已存在同时段同权重同生效起点的 active 规则（{label}）：{rule.rule_name}"
        )
