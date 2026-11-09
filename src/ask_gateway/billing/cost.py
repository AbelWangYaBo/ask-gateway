"""usage → 费用折算与定价快照。

单价口径：每百万 token 的人民币分；搜索按每千次计。
complete=FALSE 的 usage 沿用 DSH 口径直接计 0，不伪造合计。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ask_gateway.models.usage import PricingRule

_CENTS_QUANTUM = Decimal("0.001")
_TOKENS_PER_MILLION = Decimal("1000000")
_SEARCHES_PER_THOUSAND = Decimal("1000")


def snapshot_rule(rule: PricingRule) -> dict:
    """计费时把生效规则冻结进 usage 记录，规则后改不影响历史账单。"""
    return {
        "pricing_rule_id": str(rule.id),
        "model": rule.model,
        "rule_name": rule.rule_name,
        "peak_start_hour": rule.peak_start_hour,
        "peak_end_hour": rule.peak_end_hour,
        "weekdays_only": rule.weekdays_only,
        "effective_from": rule.effective_from.isoformat(),
        "input_price_per_mtok": str(rule.input_price_per_mtok),
        "output_price_per_mtok": str(rule.output_price_per_mtok),
        "reasoning_price_per_mtok": (
            str(rule.reasoning_price_per_mtok)
            if rule.reasoning_price_per_mtok is not None
            else None
        ),
        "cache_read_price_per_mtok": (
            str(rule.cache_read_price_per_mtok)
            if rule.cache_read_price_per_mtok is not None
            else None
        ),
        "cache_write_price_per_mtok": (
            str(rule.cache_write_price_per_mtok)
            if rule.cache_write_price_per_mtok is not None
            else None
        ),
        "search_price_per_k": str(rule.search_price_per_k),
    }


def compute_cost(
    *,
    complete: bool,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    reasoning_tokens: int | None,
    search_count: int,
    snapshot: dict,
) -> Decimal:
    if not complete:
        return Decimal("0.000")

    total = Decimal("0")
    total += _token_charge(input_tokens, snapshot.get("input_price_per_mtok"))
    total += _token_charge(output_tokens, snapshot.get("output_price_per_mtok"))
    # reasoning/cache 只有规则显式给价才计，避免与 output 重复收费
    total += _token_charge(reasoning_tokens, snapshot.get("reasoning_price_per_mtok"))
    total += _token_charge(cache_read_tokens, snapshot.get("cache_read_price_per_mtok"))
    total += _token_charge(cache_write_tokens, snapshot.get("cache_write_price_per_mtok"))

    search_price = snapshot.get("search_price_per_k")
    if search_price is not None and search_count:
        total += Decimal(search_count) / _SEARCHES_PER_THOUSAND * Decimal(search_price)

    return total.quantize(_CENTS_QUANTUM, rounding=ROUND_HALF_UP)


def _token_charge(tokens: int | None, price_per_mtok: str | None) -> Decimal:
    if not tokens or price_per_mtok is None:
        return Decimal("0")
    return Decimal(tokens) / _TOKENS_PER_MILLION * Decimal(price_per_mtok)
