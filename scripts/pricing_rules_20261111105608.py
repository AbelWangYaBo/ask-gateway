"""pricing_rules 数据修正：占位种子 → DeepSeek 官方 2026 报价。人工执行，幂等。

用法（在项目根目录）：
    uv run python scripts/pricing_rules_20261111105608.py

背景：初版种子为量级占位（flash 100/400、高峰 16-24 单段等）。官方实际为
空闲/高峰两档、高峰两段窗口（工作日 9-12、14-18，午休 12-14 空闲）。
本脚本把既有规则改写为官方价并补齐 9-12 段高峰档，口径与
scripts/seed_pricing.py 一致。

幂等口径：按 (model, rule_name) upsert，重复执行收敛到同一结果。
历史账单不受影响：usage 落库时已冻结 pricing_snapshot。

价格来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing（2026-11-11）
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.db.session import get_session_factory
from ask_gateway.logging_conf import configure_logging, get_logger
from ask_gateway.models.usage import PricingRule

log = get_logger(component="fix_pricing_rules")

EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)

# 官方口径目标规则（分/百万token；搜索 分/千次），与 seed_pricing.py 同源
TARGET_RULES: tuple[dict[str, Any], ...] = (
    {
        "model": "deepseek-flash",
        "rule_name": "flash 空闲档",
        "peak_start_hour": None,
        "peak_end_hour": None,
        "weekdays_only": False,
        "input_price_per_mtok": Decimal("100.0000"),
        "output_price_per_mtok": Decimal("450.0000"),
        "reasoning_price_per_mtok": None,
        "cache_read_price_per_mtok": Decimal("2.0000"),
        "cache_write_price_per_mtok": None,
        "search_price_per_k": Decimal("500.0000"),
    },
    {
        "model": "deepseek-flash",
        "rule_name": "flash 高峰档(工作日9-12)",
        "peak_start_hour": 9,
        "peak_end_hour": 12,
        "weekdays_only": True,
        "input_price_per_mtok": Decimal("200.0000"),
        "output_price_per_mtok": Decimal("900.0000"),
        "reasoning_price_per_mtok": None,
        "cache_read_price_per_mtok": Decimal("4.0000"),
        "cache_write_price_per_mtok": None,
        "search_price_per_k": Decimal("500.0000"),
    },
    {
        "model": "deepseek-flash",
        "rule_name": "flash 高峰档(工作日14-18)",
        "peak_start_hour": 14,
        "peak_end_hour": 18,
        "weekdays_only": True,
        "input_price_per_mtok": Decimal("200.0000"),
        "output_price_per_mtok": Decimal("900.0000"),
        "reasoning_price_per_mtok": None,
        "cache_read_price_per_mtok": Decimal("4.0000"),
        "cache_write_price_per_mtok": None,
        "search_price_per_k": Decimal("500.0000"),
    },
    {
        "model": "deepseek-v4-pro",
        "rule_name": "pro 空闲档",
        "peak_start_hour": None,
        "peak_end_hour": None,
        "weekdays_only": False,
        "input_price_per_mtok": Decimal("450.0000"),
        "output_price_per_mtok": Decimal("1350.0000"),
        "reasoning_price_per_mtok": None,
        "cache_read_price_per_mtok": Decimal("15.0000"),
        "cache_write_price_per_mtok": None,
        "search_price_per_k": Decimal("500.0000"),
    },
    {
        "model": "deepseek-v4-pro",
        "rule_name": "pro 高峰档(工作日9-12)",
        "peak_start_hour": 9,
        "peak_end_hour": 12,
        "weekdays_only": True,
        "input_price_per_mtok": Decimal("900.0000"),
        "output_price_per_mtok": Decimal("2700.0000"),
        "reasoning_price_per_mtok": None,
        "cache_read_price_per_mtok": Decimal("30.0000"),
        "cache_write_price_per_mtok": None,
        "search_price_per_k": Decimal("500.0000"),
    },
    {
        "model": "deepseek-v4-pro",
        "rule_name": "pro 高峰档(工作日14-18)",
        "peak_start_hour": 14,
        "peak_end_hour": 18,
        "weekdays_only": True,
        "input_price_per_mtok": Decimal("900.0000"),
        "output_price_per_mtok": Decimal("2700.0000"),
        "reasoning_price_per_mtok": None,
        "cache_read_price_per_mtok": Decimal("30.0000"),
        "cache_write_price_per_mtok": None,
        "search_price_per_k": Decimal("500.0000"),
    },
)

# 旧占位规则名：目标名单未覆盖时停用，避免残留冲突窗口
LEGACY_NAMES = {
    "flash 全时段标准档",
    "flash 高峰档(16-24)",
    "pro 全时段标准档",
    "pro 高峰档(16-24)",
}


def apply(session: Session) -> None:
    existing_by_name = {rule.rule_name: rule for rule in session.scalars(select(PricingRule))}
    target_names = {t["rule_name"] for t in TARGET_RULES}

    # 1) 目标规则 upsert
    for target in TARGET_RULES:
        row = existing_by_name.get(target["rule_name"])
        if row is None:
            session.add(PricingRule(**target, effective_from=EFFECTIVE_FROM, is_active=True))
            log.info("insert", name=target["rule_name"])
        else:
            for field, value in target.items():
                setattr(row, field, value)
            row.is_active = True
            log.info("update", name=target["rule_name"])

    # 2) 其余旧占位规则停用
    for name, row in existing_by_name.items():
        if name in LEGACY_NAMES and name not in target_names:
            row.is_active = False
            log.info("deactivate legacy", name=name)

    session.commit()


if __name__ == "__main__":
    configure_logging(log_json=False, level="INFO")
    session_factory = get_session_factory()
    with session_factory() as s:
        apply(s)
    log.info("done")
