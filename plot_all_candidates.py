# -*- coding: utf-8 -*-
"""Backtest with top_n=None to get all per-ticker-per-week picks, then
plot GROUND vs P&L across the full candidate distribution.

Without top-N filtering, the strategy keeps the best spread per (ticker,
date) pair regardless of GROUND rank, giving us ~100 candidates per week
instead of 5 — a much larger sample for the ranker-quality diagnostic.
"""
from __future__ import annotations
import math
import os
import sys

import numpy as np
import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


START = "2020-01-01"
END   = "2026-05-04"


def setup():
    config.START_DATE = START
    config.END_DATE   = END
    config.SIZING     = "1"
    config.GROUND_THRESHOLD = float("-inf")
    config.MAX_CREDIT_RATIO = config.BACKTEST_MAX_CREDIT_RATIO
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP          = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER          = True
    spreads.REGIME_PER_TICKER      = False
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}
    spreads.SLIPPAGE_CENTS         = 0.0


def main() -> int:
    print(f"\n== ALL CANDIDATES backtest (top_n=None), {START}→{END} ==\n")
    setup()
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"  {len(df):,} option rows\n")

    trades_df, weekly_df = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=None, sizing="1",
        use_drift=False, drift_lookup=None,
    )
    print(f"\nGot {len(trades_df):,} candidate trades (all per-ticker-best picks)")

    tr = trades_df[trades_df["GROUND"].notna()].copy()
    # Canonical (2026-05-13+): GROUND column stores Kelly EV · exp(−k·DKL)
    # directly (positive fractional return). No exp() needed for ranking.
    tr["exp_J"]   = tr["GROUND"]
    # Per-trade yield: pnl_per_contract / max_loss = $P&L / $wagered.
    # Dimensionless, scale-invariant across spread widths — matches the
    # sportsbook-yield framing used in the headline tables.
    tr["yield_pct"] = tr["pnl_per_contract"] / tr["max_loss"] * 100

    color_map = {"WIN": "#1D9E75", "LOSS": "#E24B4A", "PARTIAL": "#EF9F27"}
    colors = [color_map.get(r, "#888780") for r in tr["result"]]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9),
                                    gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor("#0f0f0f")

    ax1.set_facecolor("#0f0f0f")
    ax1.scatter(tr["exp_J"], tr["yield_pct"], c=colors, alpha=0.35, s=8)
    ax1.axhline(0, color="#888780", linewidth=0.8, linestyle="--")
    ax1.set_xscale("log")
    ax1.set_xlabel(r"$\Gamma_i = (\exp(g)-1)\cdot\exp(-k\cdot D_{\mathrm{KL}})$  [log scale]", color="#e8e8e8")
    ax1.set_ylabel("Per-trade yield (%)", color="#e8e8e8")
    ax1.set_title(f"Kelly-EV GROUND vs yield — ALL candidates (n={len(tr):,}, 2020-2026)",
                  color="#e8e8e8", fontsize=14)
    ax1.tick_params(colors="#888780")
    for spine in ax1.spines.values():
        spine.set_color("#888780")
    legend = [Patch(color=color_map["WIN"], label="WIN"),
              Patch(color=color_map["PARTIAL"], label="PARTIAL"),
              Patch(color=color_map["LOSS"], label="LOSS")]
    ax1.legend(handles=legend, loc="lower left", facecolor="#1a1a1a",
               edgecolor="#888780", labelcolor="#e8e8e8")

    ax2.set_facecolor("#0f0f0f")
    tr["decile"] = pd.qcut(tr["exp_J"], q=10, labels=False, duplicates="drop")
    decile_stats = tr.groupby("decile").agg(
        mean_yield=("yield_pct", "mean"),
        win_rate=("result", lambda r: (r == "WIN").mean() * 100),
        n=("yield_pct", "count"),
        mean_ground=("exp_J", "mean"),
    )
    print("\nDecile stats:")
    print(decile_stats.round(3))

    # Save decile stats and rank correlations for the paper
    decile_out = os.path.join(config.OUTPUT_DIR, "ground_vs_pnl_decile.csv")
    decile_stats.round(4).to_csv(decile_out)
    print(f"Saved {decile_out}")

    # Split deciles: in-sample (2020-2024) vs holdout (2025-2026)
    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
    is_mask = tr["entry_date"] < pd.Timestamp("2025-01-01")
    for label, mask in [("is", is_mask), ("oos", ~is_mask)]:
        sub = tr[mask].copy()
        if sub.empty:
            continue
        sub["decile"] = pd.qcut(sub["exp_J"], q=10, labels=False, duplicates="drop")
        sub_stats = sub.groupby("decile").agg(
            mean_yield=("yield_pct", "mean"),
            win_rate=("result", lambda r: (r == "WIN").mean() * 100),
            n=("yield_pct", "count"),
            mean_ground=("exp_J", "mean"),
        )
        sp_sub = sub["exp_J"].corr(sub["yield_pct"], method="spearman")
        sub_path = os.path.join(config.OUTPUT_DIR, f"ground_vs_pnl_decile_{label}.csv")
        sub_stats.round(4).to_csv(sub_path)
        print(f"Saved {sub_path}  (n={len(sub):,}, Spearman ρ={sp_sub:+.3f})")

    tr["pnl_dollars"] = tr["pnl_per_contract"] * 100  # qty=1 throughout

    sp_yield = tr["exp_J"].corr(tr["yield_pct"], method="spearman")
    pe_yield = np.log(tr["exp_J"]).corr(tr["yield_pct"], method="pearson")
    sp_pnl   = tr["exp_J"].corr(tr["pnl_dollars"], method="spearman")
    pe_pnl   = np.log(tr["exp_J"]).corr(tr["pnl_dollars"], method="pearson")

    print(f"\nCorrelations (n = {len(tr):,}):")
    print(f"  Spearman Γᵢ  vs Yield% : {sp_yield:+.4f}")
    print(f"  Pearson  J_k vs Yield% : {pe_yield:+.4f}")
    print(f"  Spearman Γᵢ  vs $P&L   : {sp_pnl:+.4f}")
    print(f"  Pearson  J_k vs $P&L   : {pe_pnl:+.4f}")

    # Decile mean $P&L for direct comparison
    decile_pnl = tr.groupby("decile")["pnl_dollars"].agg(["mean", "median"])
    print(f"\nDecile $P&L (mean / median):")
    print(decile_pnl.round(2))
    bar_colors = ["#1D9E75" if v >= 0 else "#E24B4A" for v in decile_stats["mean_yield"]]
    ax2.bar(decile_stats.index + 1, decile_stats["mean_yield"], color=bar_colors, alpha=0.85)
    ax2.axhline(0, color="#888780", linewidth=0.8)
    ax2.set_xlabel(r"$\Gamma_i$ decile (1 = lowest, 10 = highest)", color="#e8e8e8")
    ax2.set_ylabel("Mean per-trade yield (%)", color="#e8e8e8")
    ax2.set_title(r"Mean yield by $\Gamma_i$ decile (all candidates)",
                  color="#e8e8e8", fontsize=12)
    ax2.set_xticks(range(1, 11))
    ax2.tick_params(colors="#888780")
    for spine in ax2.spines.values():
        spine.set_color("#888780")
    for i, row in decile_stats.iterrows():
        offset = max(0.3, 0.05 * abs(decile_stats["mean_yield"]).max())
        y = row["mean_yield"] + (offset if row["mean_yield"] >= 0 else -offset*2)
        ax2.text(i + 1, y, f"{row['win_rate']:.0f}%", ha="center",
                 color="#e8e8e8", fontsize=8)

    plt.tight_layout()
    out_path = "output/ground_vs_pnl_all.png"
    plt.savefig(out_path, dpi=140, facecolor="#0f0f0f", bbox_inches="tight")
    print(f"\nSaved {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
