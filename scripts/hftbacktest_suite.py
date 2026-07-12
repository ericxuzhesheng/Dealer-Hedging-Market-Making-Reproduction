"""Shared hftbacktest engine for the three teacher-facing reproductions.

The event feed is a deterministic L2 simulation calibrated to the scale of the
cached 510300.SH minute series.  It is deliberately labelled ``synthetic_l2``:
minute OHLCV cannot reconstruct historical queues or order-level messages.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from numba import njit
from hftbacktest import (BacktestAsset, HashMapMarketDepthBacktest, Recorder, GTX, LIMIT, BUY,
    DEPTH_EVENT, TRADE_EVENT, EXCH_EVENT, LOCAL_EVENT, BUY_EVENT, SELL_EVENT, event_dtype)

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "processed" / "hftbacktest_feed_synth.npz"
ASK_OFFSET = 1_000_000_000

def ensure_feed() -> None:
    """Create the deterministic synthetic L2 feed when it is not cached."""
    if FEED.exists(): return
    rng=np.random.default_rng(20260608); tick=.01; mid=10000; levels=20; ts=0
    rows=[]
    def emit(ev,t,px,qty): rows.append((ev,t,t+1_000_000,px,qty))
    bd=DEPTH_EVENT|EXCH_EVENT|LOCAL_EVENT|BUY_EVENT; ad=DEPTH_EVENT|EXCH_EVENT|LOCAL_EVENT|SELL_EVENT
    bt=TRADE_EVENT|EXCH_EVENT|LOCAL_EVENT|BUY_EVENT; st=TRADE_EVENT|EXCH_EVENT|LOCAL_EVENT|SELL_EVENT
    for level in range(1,levels+1): emit(bd,ts,(mid-level)*tick,12.); emit(ad,ts,(mid+level)*tick,12.)
    for _ in range(60000):
        ts+=1_000_000
        if rng.random()<.30:
            if rng.random()<.5:
                emit(bd,ts,mid*tick,12.); emit(bd,ts,(mid-levels)*tick,0.); emit(ad,ts,(mid+1)*tick,0.); emit(ad,ts,(mid+levels+1)*tick,12.); mid+=1
            else:
                emit(ad,ts,mid*tick,12.); emit(ad,ts,(mid+levels)*tick,0.); emit(bd,ts,(mid-1)*tick,0.); emit(bd,ts,(mid-levels-1)*tick,12.); mid-=1
        if rng.random()<.55:
            qty=float(rng.integers(1,5)); is_buy=rng.random()<.5; emit(bt if is_buy else st,ts,(mid+1)*tick if is_buy else (mid-1)*tick,qty)
    arr=np.zeros(len(rows),dtype=event_dtype)
    for i,(ev,ex,lo,px,qty) in enumerate(rows): arr[i]["ev"]=ev; arr[i]["exch_ts"]=ex; arr[i]["local_ts"]=lo; arr[i]["px"]=px; arr[i]["qty"]=qty
    FEED.parent.mkdir(parents=True,exist_ok=True); np.savez(FEED,data=arr)


@njit
def strategy(hbt, recorder, mode, variant, requote_ns):
    """Run A-S (mode 0), dealer hedging (1), or directional bets (2)."""
    asset = 0
    previous_mid = 0.0
    alpha = 0.0
    hedge_events = 0
    while hbt.elapse(requote_ns) == 0:
        hbt.clear_inactive_orders(asset)
        depth = hbt.depth(asset)
        bbt, bat = depth.best_bid_tick, depth.best_ask_tick
        if bbt <= 0 or bat <= 0 or bat - bbt > 1_000_000:
            recorder.record(hbt)
            continue
        mid = 0.5 * (bbt + bat)
        pos = hbt.position(asset)
        if previous_mid > 0:
            alpha = 0.92 * alpha + 0.08 * (mid - previous_mid)
        previous_mid = mid

        half_spread = 3.0
        skew = 0.0
        max_pos = 50.0
        if mode == 0:                         # Avellaneda-Stoikov
            skew = 0.50 * pos if variant == 1 else 0.0
        elif mode == 1:                       # dealer hedging proxy
            skew = 0.22 * pos
            max_pos = 45.0
            if variant == 1 and abs(pos) > 4.0:
                # The wider skew represents the external hedge urgency/cost.
                # All resulting fills still pass through hftbacktest matching.
                skew = 1.35 * pos
                half_spread = 1.0
                hedge_events += 1
        else:                                 # Fodra-Labadie directional bets
            skew = 0.35 * pos
            if variant == 1:
                skew -= 2.5 * alpha
            max_pos = 25.0

        centre = mid - skew
        bid_tick = int(round(centre - half_spread))
        ask_tick = int(round(centre + half_spread))
        if bid_tick >= bat:
            bid_tick = bat - 1
        if ask_tick <= bbt:
            ask_tick = bbt + 1

        bid_ok, ask_ok = pos < max_pos, pos > -max_pos
        bid_exists, ask_exists = False, False
        values = hbt.orders(asset).values()
        while values.has_next():
            order = values.get()
            if order.side == BUY:
                if (not bid_ok) or order.price_tick != bid_tick:
                    if order.cancellable:
                        hbt.cancel(asset, order.order_id, False)
                else:
                    bid_exists = True
            else:
                if (not ask_ok) or order.price_tick != ask_tick:
                    if order.cancellable:
                        hbt.cancel(asset, order.order_id, False)
                else:
                    ask_exists = True
        tick = depth.tick_size
        if bid_ok and not bid_exists:
            hbt.submit_buy_order(asset, bid_tick, bid_tick * tick, 1.0, GTX, LIMIT, False)
        if ask_ok and not ask_exists:
            hbt.submit_sell_order(asset, ask_tick + ASK_OFFSET, ask_tick * tick, 1.0, GTX, LIMIT, False)
        recorder.record(hbt)
    return hedge_events


def run_one(mode: int, variant: int) -> tuple[dict, dict[str, np.ndarray]]:
    asset = (BacktestAsset().data([str(FEED)]).linear_asset(1.0)
             .constant_order_latency(10_000_000, 10_000_000)
             .risk_adverse_queue_model().no_partial_fill_exchange()
             .trading_value_fee_model(-0.00002, 0.0007)
             .tick_size(0.01).lot_size(1.0))
    hbt = HashMapMarketDepthBacktest([asset])
    recorder = Recorder(1, 2_000_000)
    hedge_events = strategy(hbt, recorder.recorder, mode, variant, 50_000_000)
    hbt.close()
    rec = recorder.get(0)
    rec = rec[(rec["timestamp"] > 0) & np.isfinite(rec["price"]) & (rec["price"] > 0)]
    equity = rec["balance"] + rec["position"] * rec["price"] - rec["fee"]
    summary = {
        "final_pnl": float(equity[-1]), "equity_std": float(np.std(equity)),
        "position_mean": float(np.mean(rec["position"])),
        "position_std": float(np.std(rec["position"])),
        "position_absmax": float(np.max(np.abs(rec["position"]))),
        "n_trades": int(rec["num_trades"][-1]), "hedge_urgency_events": int(hedge_events),
    }
    return summary, {"timestamp": rec["timestamp"], "position": rec["position"], "equity": equity}


def main() -> None:
    ensure_feed()
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=["as", "dealer", "directional"], required=True)
    args = parser.parse_args()
    specs = {
        "as": (0, "2026-07-avellaneda-stoikov-market-making", ["inventory", "symmetric"]),
        "dealer": (1, "", ["hedging_dealer", "pure_internalizer"]),
        "directional": (2, "2026-07-fodra-labadie-directional-bets", ["directional_inventory", "inventory_only"]),
    }
    mode, folder, names = specs[args.project]
    result, series = {}, {}
    for variant, name in [(1, names[0]), (0, names[1])]:
        result[name], series[name] = run_one(mode, variant)
    payload = {
        "framework": "hftbacktest 2.4.x", "engine_features": ["order latency", "risk-adverse queue model", "post-only limit orders", "maker/taker fees"],
        "feed": {"type": "synthetic_l2", "path": str(FEED.relative_to(ROOT)), "duration_seconds": 60, "boundary": "Framework-valid matching test; not historical 510300 Level2 replay."},
        "strategies": result,
    }
    out = ROOT / folder / "results" / "hftbacktest"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    packed = {}
    for name, values in series.items():
        step = max(1, len(values["timestamp"]) // 3000)
        for key, value in values.items():
            packed[f"{name}_{key}"] = value[::step]
    np.savez(out / "timeseries.npz", **packed)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
