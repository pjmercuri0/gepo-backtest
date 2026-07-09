#!/usr/bin/env python3
"""
Sweep edge thresholds to find optimal selection rule.

Tests Edge >= [0%, 5%, 10%, 15%, 20%, 25%] and compares:
- Final bankroll
- Total ROI
- Annualized ROI
- Sharpe ratio
- Max drawdown
- Number of trades
- Win rate
"""
import os
import sys
import json

import numpy as np
import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground
import overlay_slippage as ov


def setup_canonical_config(start, end, sizing):
    """Per-variant overrides."""
    config.START_DATE = start
    config.END_DATE   = end
    config.SIZING     = sizing


def setup_filters():
    """Standard filters."""
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


def select_trades_by_edge_threshold(scored: pd.DataFrame, edge_threshold: float,
                                    top_n: int = None) -> pd.DataFrame:
    """Select trades where EDGE >= edge_threshold (using intrinsic GROUND)."""
    if scored.empty:
        return pd.DataFrame()

    # Calculate implied odds return
    scored = scored.copy()
    scored["implied_odds_ret"] = (
        (scored["net_credit"] / scored["max_loss"] - 1.0) * 100
    ).round(2)

    # Compute intrinsic GROUND per row: Γᵢ = (exp(G) - 1) × exp(-k·DKL)
    import math
    def intrinsic_ground(row):
        if pd.isna(row.get("G")) or pd.isna(row.get("DKL")):
            return np.nan
        G = row["G"]
        DKL = row["DKL"]
        kelly_ev = math.exp(G) - 1.0
        return kelly_ev * math.exp(-ground.DKL_K * DKL)

    scored["GROUND"] = scored.apply(intrinsic_ground, axis=1)
    scored["EDGE"] = (scored["implied_odds_ret"] - scored["GROUND"] * 100).round(4)

    # Select best per ticker by EDGE, filtering on threshold
    selected = []
    for (ticker, entry_date), grp in scored.groupby(["ticker", "entry_date"]):
        valid = grp[(grp["EDGE"].notna()) & (grp["EDGE"] >= edge_threshold)]
        if valid.empty:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS", "reason": f"no EDGE >= {edge_threshold:.2f}%"
            })
            continue

        best = valid.loc[valid["EDGE"].idxmax()]
        row = best.to_dict()
        row["decision"] = best["spread_type"]
        selected.append(row)

    out = pd.DataFrame(selected)
    if out.empty or top_n is None:
        return out

    trades  = out[out["decision"] != "PASS"].copy()
    passes  = out[out["decision"] == "PASS"].copy()

    if trades.empty:
        return out

    keep_idx = (
        trades.sort_values("EDGE", ascending=False)
              .groupby("entry_date")
              .head(top_n)
              .index
    )
    demoted = trades.drop(keep_idx).copy()
    if not demoted.empty:
        demoted["decision"]    = "PASS"
        demoted["reason"]      = f"below top {top_n} by EDGE"

    kept = trades.loc[keep_idx]
    return pd.concat([kept, demoted, passes], ignore_index=True)


def run_backtest_edge_threshold(df: pd.DataFrame,
                               expiry_prices: pd.DataFrame,
                               edge_threshold: float,
                               top_n: int = None) -> tuple:
    """Run backtest with edge threshold filter."""
    entry_dates = sorted(df["DataDate"].unique())
    all_trades  = []
    weekly_rows = []
    bankroll    = config.STARTING_BANKROLL

    ep_lookup = (
        expiry_prices
        .set_index(["Symbol", "ExpirationDate"])["ExpiryPrice"]
        .to_dict()
    )

    from tqdm import tqdm
    for entry_date in tqdm(entry_dates, desc="Weeks", leave=False):
        week_df = df[df["DataDate"] == entry_date]
        if week_df.empty:
            continue

        candidates = spreads.build_candidates(week_df)
        if candidates.empty:
            continue

        scored = ground.score_candidates(candidates)
        selected = select_trades_by_edge_threshold(scored, edge_threshold, top_n)
        if selected.empty:
            continue

        trades_this_week = selected[selected["decision"] != "PASS"]
        if trades_this_week.empty:
            weekly_rows.append({
                "entry_date": entry_date, "n_trades": 0, "wins": 0,
                "losses": 0, "partials": 0, "win_rate": None,
                "week_pnl": 0, "bankroll_eow": round(bankroll, 2)
            })
            continue

        trades_this_week = trades_this_week.copy()
        trades_this_week["expiry_price"] = trades_this_week.apply(
            lambda r: ep_lookup.get(
                (r["ticker"], pd.Timestamp(r["expiry_date"])), np.nan
            ),
            axis=1,
        )
        trades_this_week = trades_this_week.dropna(subset=["expiry_price"])
        if trades_this_week.empty:
            weekly_rows.append({
                "entry_date": entry_date, "n_trades": 0, "wins": 0,
                "losses": 0, "partials": 0, "win_rate": None,
                "week_pnl": 0, "bankroll_eow": round(bankroll, 2)
            })
            continue

        trades_this_week["outcome"] = trades_this_week.apply(
            lambda r: spreads.calc_outcome(
                r["expiry_price"], r["short_strike"],
                r["long_strike"], r["decision"]
            ),
            axis=1,
        )

        trades_this_week["pnl_per_contract"] = trades_this_week.apply(
            lambda r: spreads.calc_pnl(
                r["expiry_price"], r["short_strike"], r["long_strike"],
                r["net_credit"], r["max_loss"], r["decision"]
            ),
            axis=1,
        )

        trades_this_week["result"] = trades_this_week["outcome"].apply(
            lambda x: "WIN" if x == 1.0 else ("LOSS" if x == -1.0 else "PARTIAL")
        )

        n = len(trades_this_week)
        slip_per_share = 2.0 * float(spreads.SLIPPAGE_CENTS or 0.0)
        true_max_loss  = trades_this_week["max_loss"] + slip_per_share

        sizing = config.SIZING
        try:
            flat_n = int(sizing)
            if flat_n < 1:
                raise ValueError(f"flat contract count must be >= 1, got {flat_n}")
            trades_this_week["contracts"]      = flat_n
            trades_this_week["bankroll_alloc"] = flat_n * true_max_loss * 100
        except (ValueError, TypeError):
            if sizing == "one":
                trades_this_week["contracts"]      = 1
                trades_this_week["bankroll_alloc"] = true_max_loss * 100
            elif sizing == "dyn10k":
                step_size = max(1, int(bankroll / 10_000.0))
                trades_this_week["contracts"]      = step_size
                trades_this_week["bankroll_alloc"] = step_size * true_max_loss * 100
            else:
                raise ValueError(f"unknown sizing rule: {sizing}")

        dollar_pnl = (
            (trades_this_week["pnl_per_contract"] * 100 *
             trades_this_week["contracts"]).sum()
        )
        bankroll += dollar_pnl

        n_w = (trades_this_week["result"] == "WIN").sum()
        n_p = (trades_this_week["result"] == "PARTIAL").sum()
        n_l = (trades_this_week["result"] == "LOSS").sum()

        all_trades.append(trades_this_week)
        weekly_rows.append({
            "entry_date": entry_date,
            "n_trades": len(trades_this_week),
            "wins": int(n_w),
            "losses": int(n_l),
            "partials": int(n_p),
            "win_rate": n_w / n if n > 0 else None,
            "week_pnl": round(dollar_pnl, 2),
            "bankroll_eow": round(bankroll, 2),
        })

    if not all_trades:
        return pd.DataFrame(), pd.DataFrame()

    trades_df = pd.concat(all_trades, ignore_index=True)
    weekly_df = pd.DataFrame(weekly_rows)
    return trades_df, weekly_df


def compute_stats(weekly_df, trades_df):
    """Compute summary stats from weekly and trade DataFrames."""
    if weekly_df.empty or trades_df.empty:
        return {
            "final": 0, "roi": 0, "ann": 0, "sharpe": 0, "dd": 0,
            "n_trades": 0, "win_rate": 0
        }

    final = weekly_df["bankroll_eow"].iloc[-1]
    roi = (final - config.STARTING_BANKROLL) / config.STARTING_BANKROLL * 100

    # Annualized return (52 weeks per year)
    n_weeks = len(weekly_df)
    years = n_weeks / 52.0
    ann = (((final / config.STARTING_BANKROLL) ** (1 / years)) - 1) * 100 if years > 0 else 0

    # Sharpe ratio
    weekly_returns = weekly_df["week_pnl"].values
    if len(weekly_returns) > 1:
        sharpe = np.mean(weekly_returns) / (np.std(weekly_returns) + 1e-8) * np.sqrt(52)
    else:
        sharpe = 0

    # Max drawdown
    equity = weekly_df["bankroll_eow"].values
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max * 100
    dd = drawdown.min()

    # Trade stats
    n_trades = len(trades_df)
    win_rate = (trades_df["result"] == "WIN").sum() / n_trades * 100 if n_trades > 0 else 0

    return {
        "final": final,
        "roi": roi,
        "ann": ann,
        "sharpe": sharpe,
        "dd": dd,
        "n_trades": n_trades,
        "win_rate": win_rate,
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

    edge_thresholds = [0, 5, 10, 15, 20, 25]
    results = []

    print("=" * 80)
    print("EDGE THRESHOLD SWEEP")
    print("=" * 80)

    for threshold in edge_thresholds:
        print(f"\nEdge >= {threshold}%...", end=" ", flush=True)
        trades_df, weekly_df = run_backtest_edge_threshold(df, expiry_prices, threshold)
        stats = compute_stats(weekly_df, trades_df)
        stats["threshold"] = threshold
        results.append(stats)
        print(f"done. Final: ${stats['final']:,.0f}")

    # Display results table
    print("\n" + "=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)
    print(f"{'Edge %':>8} {'Final':>12} {'ROI %':>8} {'Ann %':>8} {'Sharpe':>8} {'DD %':>8} {'Trades':>8} {'Win %':>8}")
    print("-" * 80)

    for r in results:
        print(f"{r['threshold']:>8.0f} ${r['final']:>11,.0f} {r['roi']:>8.1f} {r['ann']:>8.1f} {r['sharpe']:>8.2f} {r['dd']:>8.1f} {r['n_trades']:>8.0f} {r['win_rate']:>8.1f}")

    # Find best by Sharpe ratio
    best_sharpe = max(results, key=lambda x: x['sharpe'])
    best_roi = max(results, key=lambda x: x['roi'])

    print("\n" + "=" * 80)
    print("BEST BY METRIC")
    print("=" * 80)
    print(f"Best Sharpe:    Edge >= {best_sharpe['threshold']:.0f}% (Sharpe {best_sharpe['sharpe']:.2f})")
    print(f"Best ROI:       Edge >= {best_roi['threshold']:.0f}% (ROI {best_roi['roi']:.1f}%)")

    # Save to JSON
    import json
    results_json = json.dumps([{k: float(v) if isinstance(v, (int, float, np.number)) else v
                               for k, v in r.items()} for r in results], indent=2)
    with open("output/edge_sweep_results.json", "w") as f:
        f.write(results_json)
    print(f"\nResults saved to output/edge_sweep_results.json")


if __name__ == "__main__":
    main()
