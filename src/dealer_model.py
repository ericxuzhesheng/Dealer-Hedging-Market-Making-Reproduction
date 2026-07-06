"""Dealer market-making proxy with external hedging and market impact."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DealerParams:
    half_spread: float = 0.004
    inventory_skew: float = 0.0008
    hedge_threshold: int = 8
    hedge_fraction: float = 0.55
    impact_cost: float = 0.0015
    client_trade_prob: float = 0.22
    max_trade_size: int = 3


def quote_adjustment(inventory: int, params: DealerParams) -> tuple[float, float]:
    """Inventory-aware bid/ask depths for client quotes."""
    bid_depth = max(0.001, params.half_spread + params.inventory_skew * inventory)
    ask_depth = max(0.001, params.half_spread - params.inventory_skew * inventory)
    return bid_depth, ask_depth


def hedge_quantity(inventory: int, params: DealerParams) -> int:
    """Externalize inventory only outside the internalization band."""
    excess = abs(inventory) - params.hedge_threshold
    if excess <= 0:
        return 0
    qty = max(1, int(round(params.hedge_fraction * excess)))
    return -qty if inventory > 0 else qty
