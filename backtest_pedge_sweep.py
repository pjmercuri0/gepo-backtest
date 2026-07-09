#!/usr/bin/env python3
"""
Sweep p_edge thresholds (p_win − breakeven_p) for the qty1 cohort.

EDGE_p = p_win − breakeven_p
where breakeven_p = max_loss / (max_loss + net_credit)

Thresholds tested (in probability points): [0, 1, 2, 3, 5, 7, 10].
"""
import os
import sys
import json

import numpy as np
import pandas as pd

import config
import data_loader
import spreads
import ground


def setup_canonical_config(start, end, sizing):
    config.START_DATE = start
    config.END_DATE   = end
    config.SIZING     = sizing


def setup_filters():
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP          = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER          = True
    spreads.REGIME_PER_TICKER      = False
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}
    spreads.SLIPPAGE_CENTS         = 0.0
    config.MAX_CREDIT_RATIO        = config.BACKTEST_MAX_CREDIT_RATIO


def select_trades_by_p_edge_threshold(scored: pd.DataFrame, p_edge_thr: float):
    """Per (ticker, entry_date): pick best p_edge >= threshold."""
    if scored.empty:
        return pd.DataFrame()

    s = scored.copy()
    s["breakeven_p"] = s["max_loss"] / (s["max_loss"] + s["net_credit"])
    s["p_edge"]      = s["p"] - s["breakeven_p"]

    selected = []
    for (ticker, entry_date), grp in s.groupby(["ticker", "entry_date"]):
        valid = grp[(grp["p_edge"].notna()) & (grp["p_edge"] >= p_edge_thr)]
        if valid.empty:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS", "reason": f"no p_edge >= {p_edge_thr:.4f}"
            })
            continue
        best = valid.loc[valid["p_edge"].idxmax()]
        row  = best.to_dict()
        row["decision"] = best["spread_type"]
        selected.append(row)

    return pd.DataFrame(selected)


def run_backtest(df, expiry_prices, p_edge_thr):
    entry_dates = sorted(df["DataDate"].unique())
    all_trades, weekly_rows = [], []
    bankroll = config.STARTING_BANKROLL

    ep_lookup = (
        expiry_prices
        .set_index(["Symbol", "ExpirationDate"])["ExpiryPrice"]
        .to_dict()
    )

    from tqdm import tqdm
    for entry_date in tqdm(entry_dates, desc=f"thr={p_edge_thr:.3f}", leave=False):
        week_df = df[df["DataDate"] == entry_date]
        if week_df.empty:
            continue
        candidates = spreads.build_candidates(week_df)
        if candidates.empty:
            continue
        scored = ground.score_candidates(candidates)
        selected = select_trades_by_p_edge_threshold(scored, p_edge_thr)
        if selected.empty:
            continue

        tw = selected[selected["decision"] != "PASS"]
        if tw.empty:
            weekly_rows.append({
                "entry_date": entry_date, "n_trades": 0,
                "week_pnl": 0, "bankroll_eow": round(bankroll, 2),
            })
            continue

        tw = tw.copy()
        tw["expiry_price"] = tw.apply(
            lambda r: ep_lookup.get(
                (r["ticker"], pd.Timestamp(r["expiry_date"])), np.nan
            ),
            axis=1,
        )
        tw = tw.dropna(subset=["expiry_price"])
        if tw.empty:
            weekly_rows.append({
                "entry_date": entry_date, "n_trades": 0,
                "week_pnl": 0, "bankroll_eow": round(bankroll, 2),
            })
            continue

        tw["outcome"] = tw.apply(
            lambda r: spreads.calc_outcome(
                r["expiry_price"], r["short_strike"],
                r["long_strike"], r["decision"]
            ),
            axis=1,
        )
        tw["pnl_per_contract"] = tw.apply(
            lambda r: spreads.calc_pnl(
                r["expiry_price"], r["short_strike"], r["long_strike"],
                r["net_credit"], r["max_loss"], r["decision"]
            ),
            axis=1,
        )
        tw["result"] = tw["outcome"].apply(
            lambda x: "WIN" if x == 1.0 else ("LOSS" if x == -1.0 else "PARTIAL")
        )

        flat_n = int(config.SIZING)
        tw["contracts"] = flat_n
        dollar_pnl = (tw["pnl_per_contract"] * 100 * tw["contracts"]).sum()
        bankroll += dollar_pnl

        all_trades.append(tw)
        weekly_rows.append({
            "entry_date":   entry_date,
            "n_trades":     len(tw),
            "week_pnl":     round(dollar_pnl, 2),
            "bankroll_eow": round(bankroll, 2),
        })

    if not all_trades:
        return pd.DataFrame(), pd.DataFrame(weekly_rows)
    return pd.concat(all_trades, ignore_index=True), pd.DataFrame(weekly_rows)


def compute_stats(weekly_df, trades_df, threshold):
    if weekly_df.empty:
        return {
            "threshold_pp": threshold * 100,
            "final": 0, "roi": 0, "ann": 0, "sharpe": 0,
            "dd": 0, "calmar": 0, "n_trades": 0, "win_rate": 0,
        }
    final = float(weekly_df["bankroll_eow"].iloc[-1])
    roi = (final - config.STARTING_BANKROLL) / config.STARTING_BANKROLL * 100
    n_weeks = len(weekly_df)
    years = n_weeks / 52.0
    ann = (((final / config.STARTING_BANKROLL) ** (1 / years)) - 1) * 100 if years > 0 else 0
    ret = weekly_df["week_pnl"].values
    sharpe = (np.mean(ret) / (np.std(ret) + 1e-8) * np.sqrt(52)
              if len(ret) > 1 else 0)
    equity = weekly_df["bankroll_eow"].values
    rmax = np.maximum.accumulate(equity)
    dd = ((equity - rmax) / rmax * 100).min() if len(equity) > 0 else 0
    calmar = ann / abs(dd) if dd != 0 else 0
    n_trades = len(trades_df) if not trades_df.empty else 0
    win_rate = ((trades_df["result"] == "WIN").sum() / n_trades * 100
                if n_trades > 0 else 0)
    return {
        "threshold_pp": threshold * 100,
        "final": final, "roi": roi, "ann": ann,
        "sharpe": float(sharpe), "dd": float(dd),
        "calmar": float(calmar),
        "n_trades": n_trades, "win_rate": float(win_rate),
    }


def main():
    print("Loading data...")
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()

    start, end, sizing = "2020-01-01", "2024-12-30", "1"
    setup_canonical_config(start, end, sizing)
    setup_filters()

    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(start)) &
        (df_full["DataDate"] <= pd.Timestamp(end))
    ]
    print(f"Data rows: {len(df):,}\n")

    thresholds_pp = [0, 1, 2, 3, 5, 7, 10]   # probability points
    results = []

    print("=" * 80)
    print("P-EDGE THRESHOLD SWEEP (qty1)")
    print("=" * 80)
    for pp in thresholds_pp:
        thr = pp / 100.0
        print(f"\np_edge >= {pp}pp...", end=" ", flush=True)
        trades_df, weekly_df = run_backtest(df, expiry_prices, thr)
        stats = compute_stats(weekly_df, trades_df, thr)
        results.append(stats)
        print(f"done. Final: ${stats['final']:,.0f}, Sharpe {stats['sharpe']:.2f}, Calmar {stats['calmar']:.2f}")

    print("\n" + "=" * 86)
    print(f"{'Thr pp':>8}{'Trades':>9}{'Win %':>8}{'Final $':>12}{'Ann %':>8}"
          f"{'Sharpe':>8}{'DD %':>8}{'Calmar':>8}")
    print("-" * 86)
    for r in results:
        print(f"{r['threshold_pp']:>8.1f}{r['n_trades']:>9d}{r['win_rate']:>8.1f}"
              f"{r['final']:>12,.0f}{r['ann']:>8.1f}"
              f"{r['sharpe']:>8.2f}{r['dd']:>8.1f}{r['calmar']:>8.2f}")

    with open("output/pedge_sweep_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote output/pedge_sweep_results.json")

    best_sharpe = max(results, key=lambda x: x["sharpe"])
    best_calmar = max(results, key=lambda x: x["calmar"])
    print(f"\nBest Sharpe:  thr={best_sharpe['threshold_pp']:.1f}pp  (Sharpe {best_sharpe['sharpe']:.2f})")
    print(f"Best Calmar:  thr={best_calmar['threshold_pp']:.1f}pp  (Calmar {best_calmar['calmar']:.2f})")


if __name__ == "__main__":
    main()
