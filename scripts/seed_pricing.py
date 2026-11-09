"""定价规则种子：人工执行的一次性脚本，幂等，可反复执行。

用法（在项目根目录）：
    uv run python scripts/seed_pricing.py

设计约束：
- 绝不放进应用 lifespan；迁移与种子数据一律人工在目标环境执行。
- 幂等口径与 billing.pricing.assert_no_active_overlap 一致：
  同 model + 同峰谷窗口 + 同 weekdays_only 的 active 规则已存在即跳过，
  重复执行不会产生重复行，也不会触发 409 冲突。
- 单价单位：人民币分 / 百万 token；搜索为分 / 千次。
- 下表数值按 DeepSeek 历史公开人民币定价的量级分档占位（flash 标准档
  输入 1 元、输出 4 元 / 百万 token；高峰档翻倍；pro 为 flash 的 4 倍），
  模型名与 2026 年实际报价以官方页为准，上线前在控制台核对/修订。
- 搜索次数无公开稳定单价，种子置 0，不伪造报价；reasoning/cache_write
  DeepSeek 口径不单列收费，置 NULL，账单快照相应跳过。
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
# 北京时区高峰窗口 [16, 24)；其余时间回落全时段规则
PEAK_START_HOUR = 16
PEAK_END_HOUR = 24


@dataclass(frozen=True, slots=True)
class SeedRule:
    model: str
    rule_name: str
    peak_start_hour: int | None
    peak_end_hour: int | None
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal
    cache_read_price_per_mtok: Decimal | None


SEED_RULES: tuple[SeedRule, ...] = (
    # ---- deepseek-flash：全时段标准档 ----
    SeedRule(
        model="deepseek-flash",
        rule_name="flash 全时段标准档",
        peak_start_hour=None,
        peak_end_hour=None,
        input_price_per_mtok=Decimal("100.0000"),
        output_price_per_mtok=Decimal("400.0000"),
        cache_read_price_per_mtok=Decimal("50.0000"),
    ),
    # ---- deepseek-flash：北京时区 16:00-24:00 高峰档 ----
    SeedRule(
        model="deepseek-flash",
        rule_name="flash 高峰档(16-24)",
        peak_start_hour=PEAK_START_HOUR,
        peak_end_hour=PEAK_END_HOUR,
        input_price_per_mtok=Decimal("200.0000"),
        output_price_per_mtok=Decimal("800.0000"),
        cache_read_price_per_mtok=Decimal("100.0000"),
    ),
    # ---- deepseek-v4-pro：全时段标准档 ----
    SeedRule(
        model="deepseek-v4-pro",
        rule_name="pro 全时段标准档",
        peak_start_hour=None,
        peak_end_hour=None,
        input_price_per_mtok=Decimal("400.0000"),
        output_price_per_mtok=Decimal("1600.0000"),
        cache_read_price_per_mtok=Decimal("200.0000"),
    ),
    # ---- deepseek-v4-pro：高峰档 ----
    SeedRule(
        model="deepseek-v4-pro",
        rule_name="pro 高峰档(16-24)",
        peak_start_hour=PEAK_START_HOUR,
        peak_end_hour=PEAK_END_HOUR,
        input_price_per_mtok=Decimal("800.0000"),
        output_price_per_mtok=Decimal("3200.0000"),
        cache_read_price_per_mtok=Decimal("400.0000"),
    ),
)


def _exists_active(
    session: Session,
    model: str,
    peak_start_hour: int | None,
    peak_end_hour: int | None,
) -> bool:
    """同 model + 同窗口的 active 规则是否已存在（weekdays_only 种子统一为 False）。"""
    existing = session.scalars(
        select(PricingRule).where(
            PricingRule.model == model,
            PricingRule.is_active.is_(True),
            PricingRule.weekdays_only.is_(False),
        )
    ).all()
    return any(
        rule.peak_start_hour == peak_start_hour and rule.peak_end_hour == peak_end_hour
        for rule in existing
    )


def seed() -> None:
    session_factory = get_session_factory()
    inserted = 0
    skipped = 0
    with session_factory() as session:
        for seed_rule in SEED_RULES:
            if _exists_active(
                session,
                seed_rule.model,
                seed_rule.peak_start_hour,
                seed_rule.peak_end_hour,
            ):
                skipped += 1
                log.info("skip existing rule", model=seed_rule.model, name=seed_rule.rule_name)
                continue
            session.add(
                PricingRule(
                    model=seed_rule.model,
                    rule_name=seed_rule.rule_name,
                    peak_start_hour=seed_rule.peak_start_hour,
                    peak_end_hour=seed_rule.peak_end_hour,
                    weekdays_only=False,
                    is_active=True,
                    effective_from=EFFECTIVE_FROM,
                    input_price_per_mtok=seed_rule.input_price_per_mtok,
                    output_price_per_mtok=seed_rule.output_price_per_mtok,
                    reasoning_price_per_mtok=None,
                    cache_read_price_per_mtok=seed_rule.cache_read_price_per_mtok,
                    cache_write_price_per_mtok=None,
                    search_price_per_k=Decimal("0.0000"),
                )
            )
            inserted += 1
            log.info("insert rule", model=seed_rule.model, name=seed_rule.rule_name)
        session.commit()
    log.info("seed finished", inserted=inserted, skipped=skipped)


if __name__ == "__main__":
    configure_logging(log_json=False, level="INFO")
    seed()
