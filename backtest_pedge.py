#!/usr/bin/env python3
"""
Backtest with probability-edge ranker.

EDGE_p = p_win - breakeven_p
where breakeven_p = max_loss / (max_loss + net_credit)

Per (ticker, entry_date): pick the spread with the highest EDGE_p, only if
EDGE_p > 0. No per-week top-N cap (matches canonical None setting).

Same qty1 cohort: 2020-01-01 to 2024-12-30, sizing=1, canonical regime filter.
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


def select_trades_by_p_edge(scored: pd.DataFrame, top_n=None) -> pd.DataFrame:
    """Per (ticker, entry_date): pick highest p_edge > 0 spread."""
    if scored.empty:
        return pd.DataFrame()

    s = scored.copy()
    s["breakeven_p"] = (s["max_loss"] / (s["max_loss"] + s["net_credit"])).round(6)
    s["p_edge"]      = (s["p"] - s["breakeven_p"]).round(6)

    selected = []
    for (ticker, entry_date), grp in s.groupby(["ticker", "entry_date"]):
        valid = grp[(grp["p_edge"].notna()) & (grp["p_edge"] > 0)]
        if valid.empty:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS", "reason": "no p_edge > 0"
            })
            continue
        best = valid.loc[valid["p_edge"].idxmax()]
        row  = best.to_dict()
        row["decision"] = best["spread_type"]
        selected.append(row)

    out = pd.DataFrame(selected)
    if out.empty or top_n is None:
        return out

    trades = out[out["decision"] != "PASS"].copy()
    passes = out[out["decision"] == "PASS"].copy()
    if trades.empty:
        return out

    keep_idx = (
        trades.sort_values("p_edge", ascending=False)
              .groupby("entry_date")
              .head(top_n)
              .index
    )
    demoted = trades.drop(keep_idx).copy()
    if not demoted.empty:
        demoted["decision"] = "PASS"
        demoted["reason"]   = f"below top {top_n} by p_edge"
    kept = trades.loc[keep_idx]
    return pd.concat([kept, demoted, passes], ignore_index=True)


def _weekly_summary(entry_date, bankroll, week_pnl, wins, losses, partial, n_trades):
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


def run_pedge_backtest(df, expiry_prices, top_n=None):
    entry_dates = sorted(df["DataDate"].unique())
    all_trades, weekly_rows = [], []
    bankroll = config.STARTING_BANKROLL

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
        candidates = spreads.build_candidates(week_df)
        if candidates.empty:
            continue
        scored = ground.score_candidates(candidates)
        selected = select_trades_by_p_edge(scored, top_n=top_n)
        if selected.empty:
            continue

        trades_this_week = selected[selected["decision"] != "PASS"]
        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
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
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
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
        weekly_rows.append(_weekly_summary(entry_date, bankroll, dollar_pnl,
                                           n_w, n_l, n_p, len(trades_this_week)))

    if not all_trades:
        return pd.DataFrame(), pd.DataFrame()
    return pd.concat(all_trades, ignore_index=True), pd.DataFrame(weekly_rows)


def compute_stats(weekly_df, trades_df):
    if weekly_df.empty or trades_df.empty:
        return {}
    final = weekly_df["bankroll_eow"].iloc[-1]
    roi = (final - config.STARTING_BANKROLL) / config.STARTING_BANKROLL * 100
    n_weeks = len(weekly_df)
    years = n_weeks / 52.0
    ann = (((final / config.STARTING_BANKROLL) ** (1 / years)) - 1) * 100 if years > 0 else 0
    weekly_returns = weekly_df["week_pnl"].values
    sharpe = (np.mean(weekly_returns) / (np.std(weekly_returns) + 1e-8) * np.sqrt(52)
              if len(weekly_returns) > 1 else 0)
    equity = weekly_df["bankroll_eow"].values
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max * 100
    dd = drawdown.min()
    n_trades = len(trades_df)
    win_rate = (trades_df["result"] == "WIN").sum() / n_trades * 100 if n_trades > 0 else 0
    return {
        "final": float(final), "roi": float(roi), "ann": float(ann),
        "sharpe": float(sharpe), "dd": float(dd),
        "calmar": float(ann / abs(dd)) if dd != 0 else 0,
        "n_trades": int(n_trades), "win_rate": float(win_rate),
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

    trades_df, weekly_df = run_pedge_backtest(df, expiry_prices, top_n=None)

    if trades_df.empty:
        print("No trades generated.")
        return

    config.TRADES_CSV       = "all_trades-qty1-pedge.csv"
    config.RESULTS_CSV      = "results-qty1-pedge.csv"
    config.EQUITY_CURVE_PNG = "equity_curve-qty1-pedge.png"
    results.save_results(trades_df, weekly_df)

    src_html = os.path.join(config.OUTPUT_DIR, "weekly_report.html")
    dst_html = os.path.join(config.OUTPUT_DIR, "weekly_report-qty1-pedge.html")
    if os.path.exists(src_html):
        os.replace(src_html, dst_html)

    stats = compute_stats(weekly_df, trades_df)

    print("\n" + "=" * 72)
    print("P-EDGE BACKTEST SUMMARY (qty1, 2020-01-01 → 2024-12-30, sizing=1)")
    print("=" * 72)
    print(f"Total trades:    {stats['n_trades']}")
    print(f"Win rate:        {stats['win_rate']:.2f}%")
    print(f"Final bankroll:  ${stats['final']:,.2f}")
    print(f"ROI:             {stats['roi']:.2f}%")
    print(f"Annualized:      {stats['ann']:.2f}%")
    print(f"Sharpe:          {stats['sharpe']:.3f}")
    print(f"Max DD:          {stats['dd']:.2f}%")
    print(f"Calmar:          {stats['calmar']:.3f}")

    import json
    with open("output/pedge_qty1_results.json", "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\nWrote output/pedge_qty1_results.json")


if __name__ == "__main__":
    main()
