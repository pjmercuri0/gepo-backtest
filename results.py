"""
results.py
Saves results to CSV and plots the equity curve.
"""

import os
import math
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import config


def save_results(trades_df: pd.DataFrame,
                 weekly_df: pd.DataFrame) -> None:
    """Save all output files."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    # ── CSVs ──────────────────────────────────────────────────────────────
    trades_path = os.path.join(config.OUTPUT_DIR, config.TRADES_CSV)
    weekly_path = os.path.join(config.OUTPUT_DIR, config.RESULTS_CSV)

    trades_df.to_csv(trades_path, index=False)
    weekly_df.to_csv(weekly_path, index=False)

    print(f"\nSaved: {trades_path}")
    print(f"Saved: {weekly_path}")

    # ── Equity curve ──────────────────────────────────────────────────────
    _plot_equity_curve(weekly_df)

    # ── Print summary ─────────────────────────────────────────────────────
    print_summary(trades_df, weekly_df)


def print_summary(trades_df: pd.DataFrame,
                  weekly_df: pd.DataFrame) -> None:
    """Print a clean backtest summary to the console."""
    if weekly_df.empty or trades_df.empty:
        print("No results to summarise.")
        return

    start_br  = config.STARTING_BANKROLL
    final_br  = weekly_df["bankroll_eow"].iloc[-1]
    total_pnl = final_br - start_br
    total_roi = total_pnl / start_br * 100

    n_weeks   = len(weekly_df)
    n_trades  = len(trades_df)
    wins      = (trades_df["result"] == "WIN").sum()
    losses    = (trades_df["result"] == "LOSS").sum()
    partials  = (trades_df["result"] == "PARTIAL").sum()
    win_rate  = wins / n_trades * 100 if n_trades > 0 else 0

    # Sharpe-style: weekly returns
    weekly_returns = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    avg_ret  = weekly_returns.mean()
    std_ret  = weekly_returns.std()
    sharpe   = (avg_ret / std_ret * math.sqrt(52)) if std_ret > 0 else 0

    # Max drawdown
    running_max = weekly_df["bankroll_eow"].cummax()
    drawdown    = (weekly_df["bankroll_eow"] - running_max) / running_max
    max_dd      = drawdown.min() * 100

    direction = trades_df["decision"].value_counts()

    print("\n" + "=" * 60)
    print("  GEPO CREDIT SPREAD BACKTEST — SUMMARY")
    print("=" * 60)
    print(f"  Period:          {weekly_df['entry_date'].min().date()} "
          f"to {weekly_df['entry_date'].max().date()}")
    print(f"  Weeks traded:    {n_weeks}")
    print(f"  Total trades:    {n_trades:,}")
    print(f"  Starting:        ${start_br:>12,.2f}")
    print(f"  Final:           ${final_br:>12,.2f}")
    print(f"  Total P&L:       ${total_pnl:>12,.2f}")
    print(f"  Total ROI:       {total_roi:>11.2f}%")
    print(f"  Annualised ROI:  {total_roi / (n_weeks / 52):>10.2f}%")
    print(f"  Sharpe ratio:    {sharpe:>11.2f}")
    print(f"  Max drawdown:    {max_dd:>11.2f}%")
    print("-" * 60)
    print(f"  Win rate:        {win_rate:>11.1f}%")
    print(f"  Wins:            {wins:>11,}")
    print(f"  Losses:          {losses:>11,}")
    print(f"  Partials:        {partials:>11,}")
    print("-" * 60)
    print("  Direction split:")
    for k, v in direction.items():
        print(f"    {k:<20} {v:>6,} ({v/n_trades*100:.1f}%)")
    print("=" * 60)


def _plot_equity_curve(weekly_df: pd.DataFrame) -> None:
    """Plot and save the equity curve."""
    if weekly_df.empty:
        return

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True
    )
    fig.suptitle("GEPO Credit Spread Backtest — Equity Curve",
                 fontsize=14, fontweight="bold", y=0.98)

    dates     = pd.to_datetime(weekly_df["entry_date"])
    bankroll  = weekly_df["bankroll_eow"]
    week_pnl  = weekly_df["week_pnl"]

    # ── Top: equity curve ─────────────────────────────────────────────────
    ax1.plot(dates, bankroll, linewidth=1.5, color="#1D9E75", label="Bankroll")
    ax1.axhline(config.STARTING_BANKROLL, linestyle="--",
                color="#888780", linewidth=0.8, label=f"Start ${config.STARTING_BANKROLL:,.0f}")
    ax1.fill_between(dates, config.STARTING_BANKROLL, bankroll,
                     where=bankroll >= config.STARTING_BANKROLL,
                     alpha=0.15, color="#1D9E75")
    ax1.fill_between(dates, config.STARTING_BANKROLL, bankroll,
                     where=bankroll < config.STARTING_BANKROLL,
                     alpha=0.15, color="#E24B4A")
    ax1.set_ylabel("Portfolio Value ($)")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"${x:,.0f}"
    ))
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(axis="y", alpha=0.3)

    # ── Bottom: weekly P&L bars ───────────────────────────────────────────
    colors = ["#1D9E75" if v >= 0 else "#E24B4A" for v in week_pnl]
    ax2.bar(dates, week_pnl, color=colors, width=5, alpha=0.8)
    ax2.axhline(0, color="#888780", linewidth=0.8)
    ax2.set_ylabel("Weekly P&L ($)")
    ax2.set_xlabel("Date")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"${x:,.0f}"
    ))
    ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(config.OUTPUT_DIR, config.EQUITY_CURVE_PNG)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")
