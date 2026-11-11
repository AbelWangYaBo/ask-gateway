"""定价规则种子：人工执行的一次性脚本，幂等，可反复执行。

用法（在项目根目录）：
    uv run python scripts/seed_pricing.py

设计约束：
- 绝不放进应用 lifespan；迁移与种子数据一律人工在目标环境执行。
- 幂等口径与 billing.pricing.assert_no_active_overlap 一致：
  同 model + 同峰谷窗口 + 同 weekdays_only 的 active 规则已存在即跳过，
  重复执行不会产生重复行，也不会触发 409 冲突。
- 单价单位：人民币分 / 百万 token；搜索为分 / 千次。

价格来源（DeepSeek 官方，2026-11-11 核对）：
- https://api-docs.deepseek.com/zh-cn/quick_start/pricing
- 高峰时段：北京时间周一至周五 9:00-12:00、14:00-18:00（两段，午休 12-14
  为空闲）；空闲价 = 高峰价一半。周末全天空闲。
- 官方价（元/百万token）  flash        v4-pro
    输入·缓存命中      0.02 / 0.04   0.15 / 0.30
    输入·缓存未命中    1    / 2      4.5  / 9
    输出               4.5  / 9      13.5 / 27
  （斜杠前空闲、后高峰；下表换算为分）
- Search：5 元 / 千次成功请求 → 500 分/千次。
- DeepSeek 思考 token 计入输出计费、缓存写入不单列，故 reasoning /
  cache_write 置 NULL，账单快照相应跳过。
- 法定节假日不计高峰（官方排除）：表结构无节假日历，种子按工作日近似，
  节假日多计的部分可在控制台人工停用高峰档规避。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.db.session import get_session_factory
from ask_gateway.logging_conf import configure_logging, get_logger
from ask_gateway.models.usage import PricingRule

log = get_logger(component="seed_pricing")

EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SeedRule:
    model: str
    rule_name: str
    peak_start_hour: int | None
    peak_end_hour: int | None
    weekdays_only: bool
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal
    cache_read_price_per_mtok: Decimal
    search_price_per_k: Decimal


SEED_RULES: tuple[SeedRule, ...] = (
    # ---- deepseek-flash：空闲档（全时段兜底） ----
    SeedRule(
        model="deepseek-flash",
        rule_name="flash 空闲档",
        peak_start_hour=None,
        peak_end_hour=None,
        weekdays_only=False,
        input_price_per_mtok=Decimal("100.0000"),    # 1 元
        output_price_per_mtok=Decimal("450.0000"),   # 4.5 元
        cache_read_price_per_mtok=Decimal("2.0000"), # 0.02 元
        search_price_per_k=Decimal("500.0000"),      # 5 元 / 千次
    ),
    # ---- deepseek-flash：高峰档（工作日 9-12） ----
    SeedRule(
        model="deepseek-flash",
        rule_name="flash 高峰档(工作日9-12)",
        peak_start_hour=9,
        peak_end_hour=12,
        weekdays_only=True,
        input_price_per_mtok=Decimal("200.0000"),
        output_price_per_mtok=Decimal("900.0000"),
        cache_read_price_per_mtok=Decimal("4.0000"),
        search_price_per_k=Decimal("500.0000"),
    ),
    # ---- deepseek-flash：高峰档（工作日 14-18） ----
    SeedRule(
        model="deepseek-flash",
        rule_name="flash 高峰档(工作日14-18)",
        peak_start_hour=14,
        peak_end_hour=18,
        weekdays_only=True,
        input_price_per_mtok=Decimal("200.0000"),
        output_price_per_mtok=Decimal("900.0000"),
        cache_read_price_per_mtok=Decimal("4.0000"),
        search_price_per_k=Decimal("500.0000"),
    ),
    # ---- deepseek-v4-pro：空闲档（全时段兜底） ----
    SeedRule(
        model="deepseek-v4-pro",
        rule_name="pro 空闲档",
        peak_start_hour=None,
        peak_end_hour=None,
        weekdays_only=False,
        input_price_per_mtok=Decimal("450.0000"),    # 4.5 元
        output_price_per_mtok=Decimal("1350.0000"),  # 13.5 元
        cache_read_price_per_mtok=Decimal("15.0000"),# 0.15 元
        search_price_per_k=Decimal("500.0000"),
    ),
    # ---- deepseek-v4-pro：高峰档（工作日 9-12） ----
    SeedRule(
        model="deepseek-v4-pro",
        rule_name="pro 高峰档(工作日9-12)",
        peak_start_hour=9,
        peak_end_hour=12,
        weekdays_only=True,
        input_price_per_mtok=Decimal("900.0000"),
        output_price_per_mtok=Decimal("2700.0000"),
        cache_read_price_per_mtok=Decimal("30.0000"),
        search_price_per_k=Decimal("500.0000"),
    ),
    # ---- deepseek-v4-pro：高峰档（工作日 14-18） ----
    SeedRule(
        model="deepseek-v4-pro",
        rule_name="pro 高峰档(工作日14-18)",
        peak_start_hour=14,
        peak_end_hour=18,
        weekdays_only=True,
        input_price_per_mtok=Decimal("900.0000"),
        output_price_per_mtok=Decimal("2700.0000"),
        cache_read_price_per_mtok=Decimal("30.0000"),
        search_price_per_k=Decimal("500.0000"),
    ),
)


def _exists_active(
    session: Session,
    seed_rule: SeedRule,
) -> bool:
    """同 model + 同窗口 + 同 weekdays_only 的 active 规则是否已存在。"""
    existing = session.scalars(
        select(PricingRule).where(
            PricingRule.model == seed_rule.model,
            PricingRule.is_active.is_(True),
        )
    ).all()
    return any(
        rule.peak_start_hour == seed_rule.peak_start_hour
        and rule.peak_end_hour == seed_rule.peak_end_hour
        and rule.weekdays_only == seed_rule.weekdays_only
        for rule in existing
    )


def seed() -> None:
    session_factory = get_session_factory()
    inserted = 0
    skipped = 0
    with session_factory() as session:
        for seed_rule in SEED_RULES:
            if _exists_active(session, seed_rule):
                skipped += 1
                log.info("skip existing rule", model=seed_rule.model, name=seed_rule.rule_name)
                continue
            session.add(
                PricingRule(
                    model=seed_rule.model,
                    rule_name=seed_rule.rule_name,
                    peak_start_hour=seed_rule.peak_start_hour,
                    peak_end_hour=seed_rule.peak_end_hour,
                    weekdays_only=seed_rule.weekdays_only,
                    is_active=True,
                    effective_from=EFFECTIVE_FROM,
                    input_price_per_mtok=seed_rule.input_price_per_mtok,
                    output_price_per_mtok=seed_rule.output_price_per_mtok,
                    reasoning_price_per_mtok=None,
                    cache_read_price_per_mtok=seed_rule.cache_read_price_per_mtok,
                    cache_write_price_per_mtok=None,
                    search_price_per_k=seed_rule.search_price_per_k,
                )
            )
            inserted += 1
            log.info("insert rule", model=seed_rule.model, name=seed_rule.rule_name)
        session.commit()
    log.info("seed finished", inserted=inserted, skipped=skipped)


if __name__ == "__main__":
    configure_logging(log_json=False, level="INFO")
    seed()
