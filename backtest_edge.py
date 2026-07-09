#!/usr/bin/env python3
"""
Experimental edge-based ranking backtest.

Compares canonical GROUND-based selection vs EDGE-based selection on qty1
in-sample window (2020-01-01 to 2024-12-30).

EDGE = Implied Odds Return - GROUND
where Implied Odds Return = (credit/risk - 1) * 100
"""
import os
import sys

import numpy as np
import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground
import results
import overlay_slippage as ov


def setup_canonical_config(start, end, sizing):
    """Per-variant overrides. All other knobs from config.py."""
    config.START_DATE = start
    config.END_DATE   = end
    config.SIZING     = sizing


def setup_filters():
    """Standard filters for canonical backtest."""
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


def run_backtest_with_edge_selection(df: pd.DataFrame,
                                     expiry_prices: pd.DataFrame,
                                     top_n: int = None) -> tuple:
    """
    Modified backtest that uses select_trades_by_edge instead of select_trades.
    Returns (trades_df, weekly_df) same as canonical backtest.
    """
    entry_dates = sorted(df["DataDate"].unique())
    print(f"\nRunning edge-based backtest over {len(entry_dates)} weeks...")

    all_trades  = []
    weekly_rows = []
    bankroll    = config.STARTING_BANKROLL

    # Build expiry price lookup
    ep_lookup = (
        expiry_prices
        .set_index(["Symbol", "ExpirationDate"])["ExpiryPrice"]
        .to_dict()
    )

    from tqdm import tqdm
    for entry_date in tqdm(entry_dates, desc="Weeks"):
        week_df = df[df["DataDate"] == entry_date]
        if week_df.empty:
            continue

        # Build candidates and score
        candidates = spreads.build_candidates(week_df)
        if candidates.empty:
            continue

        scored = ground.score_candidates(candidates)

        # KEY DIFFERENCE: use edge-based selection
        selected = ground.select_trades_by_edge(scored, top_n=top_n)
        if selected.empty:
            continue

        trades_this_week = selected[selected["decision"] != "PASS"]
        passes_this_week = selected[selected["decision"] == "PASS"]

        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
            continue

        # Attach expiry prices
        trades_this_week = trades_this_week.copy()
        trades_this_week["expiry_price"] = trades_this_week.apply(
            lambda r: ep_lookup.get(
                (r["ticker"], pd.Timestamp(r["expiry_date"])), np.nan
            ),
            axis=1,
        )
        trades_this_week = trades_this_week.dropna(subset=["expiry_price"])
        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
            continue

        # Calculate outcomes
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

        # Sizing
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

        # Bankroll tracking
        dollar_pnl = (
            (trades_this_week["pnl_per_contract"] * 100 *
             trades_this_week["contracts"]).sum()
        )
        bankroll += dollar_pnl

        n_w = (trades_this_week["result"] == "WIN").sum()
        n_p = (trades_this_week["result"] == "PARTIAL").sum()
        n_l = (trades_this_week["result"] == "LOSS").sum()

        all_trades.append(trades_this_week)
        weekly_rows.append(_weekly_summary(entry_date, bankroll, dollar_pnl,
                                           n_w, n_l, n_p, len(trades_this_week)))

    if not all_trades:
        return pd.DataFrame(), pd.DataFrame()

    trades_df = pd.concat(all_trades, ignore_index=True)
    weekly_df = pd.DataFrame(weekly_rows)
    return trades_df, weekly_df


def _weekly_summary(entry_date, bankroll, week_pnl, wins, losses, partial, n_trades):
    """Match format from backtest.py"""
    return {
        "entry_date":   entry_date,
        "n_trades":     n_trades,
        "wins":         wins,
        "losses":       losses,
        "partials":     partial,
        "win_rate":     wins / n_trades if n_trades > 0 else None,
        "week_pnl":     round(week_pnl, 2),
        "bankroll_eow": round(bankroll, 2),
    }


def main():
    print("Loading data...")
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()

    # qty1: 2020-01-01 to 2024-12-30, sizing=1
    start, end, sizing = "2020-01-01", "2024-12-30", "1"

    print(f"\nQty1 Edge-Based Backtest ({start} → {end}, qty={sizing})")
    print("=" * 70)

    setup_canonical_config(start, end, sizing)
    setup_filters()

    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(start)) &
        (df_full["DataDate"] <= pd.Timestamp(end))
    ]
    print(f"Data rows: {len(df):,}")

    trades_df, weekly_df = run_backtest_with_edge_selection(
        df, expiry_prices, top_n=config.TOP_N
    )

    if trades_df.empty:
        print("No trades generated!")
        return

    # Save results with -edge suffix
    config.TRADES_CSV       = "all_trades-qty1-edge.csv"
    config.RESULTS_CSV      = "results-qty1-edge.csv"
    config.EQUITY_CURVE_PNG = "equity_curve-qty1-edge.png"
    results.save_results(trades_df, weekly_df)

    src_html = os.path.join(config.OUTPUT_DIR, "weekly_report.html")
    dst_html = os.path.join(config.OUTPUT_DIR, "weekly_report-qty1-edge.html")
    if os.path.exists(src_html):
        os.replace(src_html, dst_html)
        print(f"\nWrote {dst_html}")

    print(f"Wrote {config.TRADES_CSV}")
    print(f"Wrote {config.RESULTS_CSV}")

    # Print summary stats
    print("\n" + "=" * 70)
    print("EDGE-BASED SUMMARY")
    print("=" * 70)
    n_trades = len(trades_df)
    n_wins = (trades_df["result"] == "WIN").sum()
    n_loss = (trades_df["result"] == "LOSS").sum()
    n_part = (trades_df["result"] == "PARTIAL").sum()
    total_pnl = trades_df["pnl_per_contract"].sum() * 100 * trades_df["contracts"].mean()
    final_bankroll = weekly_df["bankroll"].iloc[-1] if not weekly_df.empty else 0

    print(f"Total trades:    {n_trades}")
    print(f"  WIN:           {n_wins} ({100*n_wins/n_trades:.1f}%)")
    print(f"  PARTIAL:       {n_part} ({100*n_part/n_trades:.1f}%)")
    print(f"  LOSS:          {n_loss} ({100*n_loss/n_trades:.1f}%)")
    print(f"Final bankroll:  ${final_bankroll:,.0f}")
    print(f"Total P&L:       ${total_pnl:,.0f}")
    print(f"Return:          {100*(final_bankroll - config.STARTING_BANKROLL)/config.STARTING_BANKROLL:.2f}%")
    print("\nCompare to canonical GROUND results in output/")


if __name__ == "__main__":
    main()
