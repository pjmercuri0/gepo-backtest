"""
backtest.py
Runs the weekly backtest loop:
  1. For each entry date, build spread candidates
  2. Score with GROUND
  3. Select best side per ticker (or PASS)
  4. Look up expiry price
  5. Calculate outcome and P&L
  6. Track bankroll week by week
"""

import pandas as pd
import numpy as np
from tqdm import tqdm

import config
import spreads
import ground


def run_backtest(df: pd.DataFrame,
                 expiry_prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Run the full backtest over all entry dates.

    Parameters
    ----------
    df            : filtered options DataFrame from data_loader
    expiry_prices : DataFrame with columns [Symbol, ExpirationDate, ExpiryPrice]

    Returns
    -------
    trades_df  : every individual trade with outcome and P&L
    weekly_df  : week-by-week bankroll and summary stats
    """
    entry_dates = sorted(df["DataDate"].unique())
    print(f"\nRunning backtest over {len(entry_dates)} weeks...")

    all_trades  = []
    weekly_rows = []
    bankroll    = config.STARTING_BANKROLL

    # Build expiry price lookup: (Symbol, ExpirationDate) -> price
    ep_lookup = (
        expiry_prices
        .set_index(["Symbol", "ExpirationDate"])["ExpiryPrice"]
        .to_dict()
    )

    for entry_date in tqdm(entry_dates, desc="Weeks"):

        # ── 1. Get this week's options data ───────────────────────────────
        week_df = df[df["DataDate"] == entry_date]
        if week_df.empty:
            continue

        # ── 2. Build candidates ───────────────────────────────────────────
        candidates = spreads.build_candidates(week_df)
        if candidates.empty:
            continue

        # ── 3. Score with GROUND ──────────────────────────────────────────
        scored = ground.score_candidates(candidates)

        # ── 4. Select best trade per ticker ──────────────────────────────
        selected = ground.select_trades(scored)
        if selected.empty:
            continue

        trades_this_week = selected[selected["decision"] != "PASS"]
        passes_this_week = selected[selected["decision"] == "PASS"]

        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
            continue

        # ── 5. Attach expiry prices ───────────────────────────────────────
        trades_this_week = trades_this_week.copy()
        trades_this_week["expiry_price"] = trades_this_week.apply(
            lambda r: ep_lookup.get(
                (r["ticker"], pd.Timestamp(r["expiry_date"])), np.nan
            ),
            axis=1,
        )

        # Drop trades where we don't have expiry price
        trades_this_week = trades_this_week.dropna(subset=["expiry_price"])
        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
            continue

        # ── 6. Calculate outcomes and P&L ────────────────────────────────
        trades_this_week["outcome"] = trades_this_week.apply(
            lambda r: spreads.calc_outcome(
                r["expiry_price"], r["short_strike"],
                r["long_strike"], r["decision"]
            ),
            axis=1,
        )

        trades_this_week["pnl_per_contract"] = trades_this_week.apply(
            lambda r: spreads.calc_pnl(
                r["outcome"], r["net_credit"], r["max_loss"]
            ),
            axis=1,
        )

        trades_this_week["result"] = trades_this_week["outcome"].apply(
            lambda x: "WIN" if x == 1.0 else ("LOSS" if x == -1.0 else "PARTIAL")
        )

        # ── 7. Bankroll allocation ────────────────────────────────────────
        # Allocate w_star % of bankroll to each trade, normalised
        # so total allocation ≤ 100% of bankroll
        n = len(trades_this_week)
        total_w = trades_this_week["w_star"].sum()

        trades_this_week["bankroll_alloc"] = (
            trades_this_week["w_star"] / total_w * bankroll
        )

        # Dollar P&L = (allocation / spread_width) * pnl_per_contract
        # This gives P&L as if we bought floor(allocation/spread_width) contracts
        trades_this_week["contracts"] = (
            trades_this_week["bankroll_alloc"] / trades_this_week["spread_width"]
        ).apply(np.floor)

        trades_this_week["dollar_pnl"] = (
            trades_this_week["contracts"] * trades_this_week["pnl_per_contract"]
        )

        # ── 8. Update bankroll ────────────────────────────────────────────
        week_pnl    = trades_this_week["dollar_pnl"].sum()
        bankroll   += week_pnl
        bankroll    = max(bankroll, 0.01)   # can't go below zero

        # ── 9. Record ─────────────────────────────────────────────────────
        trades_this_week["entry_date"]  = entry_date
        trades_this_week["bankroll_eow"] = bankroll
        all_trades.append(trades_this_week)

        wins    = (trades_this_week["result"] == "WIN").sum()
        losses  = (trades_this_week["result"] == "LOSS").sum()
        partial = (trades_this_week["result"] == "PARTIAL").sum()

        weekly_rows.append(_weekly_summary(
            entry_date, bankroll, week_pnl, wins, losses, partial, n
        ))

    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    weekly_df = pd.DataFrame(weekly_rows)

    return trades_df, weekly_df


def _weekly_summary(entry_date, bankroll, week_pnl,
                    wins, losses, partial, n_trades) -> dict:
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
