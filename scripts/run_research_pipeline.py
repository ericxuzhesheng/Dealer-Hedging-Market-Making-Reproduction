"""Run a minute-level dealer hedging proxy on Tushare ETF data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import tushare as ts

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.dealer_model import DealerParams, hedge_quantity, quote_adjustment


def fetch_minute(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    df = ts.pro_bar(ts_code=ts_code, start_date=start_date, end_date=end_date, freq="1min", asset="FD", adj=None)
    if df is None or df.empty:
        raise RuntimeError("No minute data returned from Tushare.")
    df = df.sort_values("trade_time").reset_index(drop=True)
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    return df


def run_strategy(df: pd.DataFrame, params: DealerParams, hedge: bool, seed: int) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    q = 0
    cash = 0.0
    client_fills = 0
    hedge_trades = 0
    rows = []
    closes = df["close"].astype(float).to_numpy()
    for i, mid in enumerate(closes[:-1]):
        bid_depth, ask_depth = quote_adjustment(q, params)
        if rng.random() < params.client_trade_prob:
            size = int(rng.integers(1, params.max_trade_size + 1))
            # positive sign means client sells to dealer, dealer buys and inventory rises
            direction = 1 if rng.random() < 0.5 else -1
            if direction > 0:
                trade_price = mid - bid_depth
                q += size
                cash -= trade_price * size
            else:
                trade_price = mid + ask_depth
                q -= size
                cash += trade_price * size
            client_fills += 1

        hq = hedge_quantity(q, params) if hedge else 0
        if hq:
            hedge_price = mid + np.sign(hq) * params.impact_cost * abs(hq)
            q += hq
            cash -= hedge_price * hq
            hedge_trades += 1

        nav = cash + q * closes[i + 1]
        rows.append(
            {
                "trade_time": df["trade_time"].iloc[i + 1],
                "strategy": "hedging_dealer" if hedge else "pure_internalizer",
                "mid": closes[i + 1],
                "inventory": q,
                "cash": cash,
                "nav": nav,
            }
        )
    path = pd.DataFrame(rows)
    summary = {
        "final_nav": float(path["nav"].iloc[-1]),
        "nav_std": float(path["nav"].std(ddof=1)),
        "inventory_mean": float(path["inventory"].mean()),
        "inventory_std": float(path["inventory"].std(ddof=1)),
        "inventory_abs_mean": float(path["inventory"].abs().mean()),
        "client_fills": int(client_fills),
        "hedge_trades": int(hedge_trades),
    }
    return path, summary


def plot(paths: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for strategy, label, color in [
        ("hedging_dealer", "Dealer with hedging", "#2F73C9"),
        ("pure_internalizer", "Pure internalizer", "#E84D3D"),
    ]:
        part = paths[paths["strategy"] == strategy]
        ax.plot(part["trade_time"], part["inventory"], label=label, color=color, linewidth=1.0)
    ax.set_title("Dealer inventory with and without external hedging")
    ax.set_xlabel("Time")
    ax.set_ylabel("Inventory")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "dealer_hedging_inventory.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.8))
    for strategy, label, color in [
        ("hedging_dealer", "Dealer with hedging", "#2F73C9"),
        ("pure_internalizer", "Pure internalizer", "#E84D3D"),
    ]:
        part = paths[paths["strategy"] == strategy]
        ax.plot(part["trade_time"], part["nav"], label=label, color=color, linewidth=1.0)
    ax.set_title("Dealer NAV path")
    ax.set_xlabel("Time")
    ax.set_ylabel("NAV")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "dealer_hedging_nav.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ts-code", default="510300.SH")
    parser.add_argument("--start-date", default="20260601")
    parser.add_argument("--end-date", default="20260605")
    parser.add_argument("--seed", type=int, default=20260706)
    args = parser.parse_args()

    raw_dir = ROOT / "data" / "raw"
    processed_dir = ROOT / "data" / "processed"
    tables_dir = ROOT / "results" / "tables"
    figures_dir = ROOT / "results" / "figures"
    for d in (raw_dir, processed_dir, tables_dir, figures_dir):
        d.mkdir(parents=True, exist_ok=True)
    df = fetch_minute(args.ts_code, args.start_date, args.end_date)
    slug = args.ts_code.replace(".", "_")
    df.to_csv(raw_dir / f"{slug}_1min_{args.start_date}_{args.end_date}.csv", index=False)
    params = DealerParams()
    hedged, hedged_summary = run_strategy(df, params, hedge=True, seed=args.seed)
    pure, pure_summary = run_strategy(df, params, hedge=False, seed=args.seed)
    paths = pd.concat([hedged, pure], ignore_index=True)
    paths.to_csv(tables_dir / "dealer_paths.csv", index=False)
    summaries = {"hedging_dealer": hedged_summary, "pure_internalizer": pure_summary}
    pd.DataFrame(summaries).T.to_csv(tables_dir / "strategy_summary.csv")
    metadata = {
        "paper": "Barzykin, Bergault, Gueant (2021), Algorithmic market making in dealer markets with hedging and market impact",
        "source": "https://arxiv.org/abs/2106.06974",
        "data": {
            "ts_code": args.ts_code,
            "start_time": df["trade_time"].iloc[0].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": df["trade_time"].iloc[-1].strftime("%Y-%m-%d %H:%M:%S"),
            "observations": int(len(df)),
            "level2_available": False,
            "level2_note": "Tushare minute bars are used; true dealer quotes and inter-dealer hedge executions are proxied.",
        },
        "params": params.__dict__,
        "summaries": summaries,
    }
    with open(processed_dir / "run_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    plot(paths, figures_dir)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
