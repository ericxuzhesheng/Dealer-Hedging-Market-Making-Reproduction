from src.dealer_model import DealerParams, hedge_quantity, quote_adjustment


def test_quote_adjustment_penalizes_long_inventory():
    params = DealerParams()
    flat_bid, flat_ask = quote_adjustment(0, params)
    long_bid, long_ask = quote_adjustment(5, params)
    assert long_bid > flat_bid
    assert long_ask < flat_ask


def test_hedge_only_outside_band():
    params = DealerParams(hedge_threshold=8, hedge_fraction=0.5)
    assert hedge_quantity(5, params) == 0
    assert hedge_quantity(12, params) < 0
    assert hedge_quantity(-12, params) > 0
