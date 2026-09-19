"""Sweep the prior-session SPY SMA window under the current D_ent canon.

This is intentionally separate from ``sma_sweep.py``, which exercises an
older empirical-DKL pipeline.  All candidate scoring, parity, selection,
fill, and P&L conventions here mirror ``report_ent_canon.select``.  The only
experimental variable is the number of SPY sessions in the moving average.

The expensive causal P_real/GROUND frame is computed once.  SMA gates are
then evaluated concurrently because they are independent late-stage filters.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import ent_canon as ec


FRAME = Path("research/dkl_2026_09_13/featATM8.parquet")
GAP_SERIES = Path("output/name_gaps_backtest.parquet")
CHAIN_FEATURES = Path(
    "research/option_direction_2026_09_16/chain_direction_features.parquet"
)
SPY_CSV = Path("data/spy_us_d.csv")
OUTPUT_CSV = Path("output/sma_bull_regime_sweep.csv")
START_BANKROLL = 10_000.0
DEFAULT_WINDOWS = (20, 50, 75, 100, 125, 150, 200)


def prepare_base() -> pd.DataFrame:
    """Recompute the window-invariant part of report_ent_canon.select once."""
    c = pd.read_parquet(FRAME).dropna(subset=["EV"]).copy()
    c["entry_date"] = pd.to_datetime(c["entry_date"]).dt.normalize()
    c["expiry_date"] = pd.to_datetime(c["expiry_date"]).dt.normalize()

    closes = ec.backtest_closes()
    mu = (
        ec.gap_drift(c, closes, pd.read_parquet(GAP_SERIES))
        if ec.GAP_GAMMA
        else None
    )
    p_real = ec.p_real(c, closes, mu=mu)
    c["p"], c["q"], c["ro"] = p_real[:, 0], p_real[:, 1], p_real[:, 2]

    b = c.model_credit.values / (c.width.values - c.model_credit.values)
    _, ell = ec.kelly(
        np.nan_to_num(c.p.values),
        np.nan_to_num(c.q.values),
        np.nan_to_num(c.ro.values),
        b,
    )
    ell[~np.isfinite(c.p.values)] = np.nan
    c["EV"] = np.exp(ell) - 1.0
    c = c.dropna(subset=["EV"]).copy()
    c["GROUND"] = c.EV * np.exp(-ec.K * c.D_ent)

    features = pd.read_parquet(
        CHAIN_FEATURES,
        columns=["ticker", "entry_date", "expiry_date", "cp_iv_gap"],
    )
    features["entry_date"] = pd.to_datetime(features.entry_date).dt.normalize()
    features["expiry_date"] = pd.to_datetime(features.expiry_date).dt.normalize()
    c = c.merge(features, on=["ticker", "entry_date", "expiry_date"], how="left")
    c["parity_bull_raw"] = -c["cp_iv_gap"]
    c = ec.add_parity_percentile(c)

    # These gates are invariant to the SMA experiment.  Daily parity ranks are
    # deliberately formed above, before GROUND and regime filtering.
    return c[
        c.spread_type.eq("bull_put")
        & (c.parity_pct > ec.PARITY_MIN_PCT)
        & (c.GROUND >= ec.THR)
    ].copy()


def prior_spy_bull(
    entry_dates: pd.Series, spy: pd.DataFrame, sma_window: int
) -> np.ndarray:
    """Exact report canon: entry uses the latest strictly prior SPY session."""
    s = spy.copy()
    s["SMA"] = s.Close.rolling(sma_window, min_periods=sma_window).mean()
    s = s.dropna(subset=["SMA"])
    dates = s.Date.to_numpy(dtype="datetime64[ns]")
    bull = (s.Close > s.SMA).to_numpy()
    entries = pd.to_datetime(entry_dates).to_numpy(dtype="datetime64[ns]")
    pos = np.searchsorted(dates, entries, side="left") - 1
    out = np.zeros(len(entries), dtype=bool)  # unknown fails closed
    safe_pos = np.clip(pos, 0, len(dates) - 1)
    stale_days = (entries - dates[safe_pos]).astype("timedelta64[D]").astype(int)
    valid = (pos >= 0) & (stale_days <= 4)
    out[valid] = bull[pos[valid]]
    return out


def realize(pool: pd.DataFrame) -> pd.DataFrame:
    """Apply top-N and the current one-contract realization convention."""
    sel = (
        pool.sort_values(["entry_date", "GROUND"], ascending=[True, False])
        .groupby("entry_date", sort=False)
        .head(ec.TOP_N)
        .copy()
    )
    sel["credit"] = (sel.model_credit * ec.FILL_MULT).round(4)
    sel["max_loss_adj"] = (sel.width - sel.credit).round(4)
    sel = sel[sel.max_loss_adj > 0].copy()

    spot = sel.expiry_close.to_numpy(float)
    short = sel.short_strike.to_numpy(float)
    long = sel.long_strike.to_numpy(float)
    credit = sel.credit.to_numpy(float)
    max_loss = sel.max_loss_adj.to_numpy(float)
    win = spot > short
    loss = spot <= long
    partial = ~(win | loss)
    pnl = np.where(win, credit, np.where(loss, -max_loss, credit - (short - spot)))
    pnl *= 100.0
    pnl[partial & (pnl > 0)] *= 0.5
    sel["pnl_per_contract"] = pnl - ec.COMMISSION
    sel["max_loss_dollar"] = max_loss * 100.0
    sel["_outcome"] = np.where(win, "WIN", np.where(loss, "LOSS", "PARTIAL"))
    return sel.sort_values("entry_date").reset_index(drop=True)


def metrics(
    picks: pd.DataFrame,
    spy: pd.DataFrame,
    period: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    calendar = pd.DatetimeIndex(spy.loc[spy.Date.between(start, end), "Date"])
    by_day = picks.groupby("expiry_date").pnl_per_contract.sum()
    daily_pnl = by_day.reindex(calendar, fill_value=0.0)
    equity = START_BANKROLL + daily_pnl.cumsum()
    daily_returns = equity.pct_change().fillna(0.0)
    daily_sd = daily_returns.std(ddof=0)
    daily_sharpe = (
        float(daily_returns.mean() * np.sqrt(252) / daily_sd)
        if daily_sd > 0
        else 0.0
    )
    weekly_returns = equity.resample("W-FRI").last().ffill().pct_change().dropna()
    weekly_sd = weekly_returns.std(ddof=0)
    weekly_sharpe = (
        float(weekly_returns.mean() * np.sqrt(52) / weekly_sd)
        if weekly_sd > 0
        else 0.0
    )
    peak = equity.cummax()
    max_dd = float(((equity - peak) / peak).min())
    years = (calendar[-1] - calendar[0]).days / 365.25
    final = float(equity.iloc[-1])
    cagr = (final / START_BANKROLL) ** (1.0 / years) - 1.0
    pnl = float(picks.pnl_per_contract.sum())
    wagered = float(picks.max_loss_dollar.sum())
    return {
        "period": period,
        "period_start": calendar[0].date().isoformat(),
        "period_end": calendar[-1].date().isoformat(),
        "trades": len(picks),
        "entry_days": picks.entry_date.nunique(),
        "pnl": pnl,
        "final_bankroll": final,
        "total_return_pct": pnl / START_BANKROLL * 100.0,
        "cagr_pct": cagr * 100.0,
        "daily_sharpe": daily_sharpe,
        "weekly_sharpe": weekly_sharpe,
        "max_drawdown_pct": max_dd * 100.0,
        "full_win_pct": float(picks._outcome.eq("WIN").mean() * 100.0),
        "profitable_pct": float((picks.pnl_per_contract > 0).mean() * 100.0),
        "yield_pct": pnl / wagered * 100.0 if wagered > 0 else 0.0,
    }


def evaluate_window(
    sma_window: int,
    base: pd.DataFrame,
    spy: pd.DataFrame,
    split_bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[list[dict], dict]:
    gated = base[prior_spy_bull(base.entry_date, spy, sma_window)]
    picks = realize(gated)
    rows = []
    for period, (start, end) in split_bounds.items():
        if period == "IS_2020_2025":
            subset = picks[picks.entry_date.dt.year <= 2025]
        else:
            subset = picks[picks.entry_date.dt.year == 2026]
        row = metrics(subset, spy, period, start, end)
        row["sma_window"] = sma_window
        rows.append(row)

    # Regression counts use the repository's published split, not the
    # requested experiment split: frame IS=2020-25 and frame OOT=2026.
    validation = {
        "sma_window": sma_window,
        "published_is_count": int(picks.win.eq("IS").sum()),
        "published_oot_count": int(picks.win.eq("OOT").sum()),
        "published_is_pnl": float(picks.loc[picks.win.eq("IS"), "pnl_per_contract"].sum()),
        "published_oot_pnl": float(picks.loc[picks.win.eq("OOT"), "pnl_per_contract"].sum()),
    }
    return rows, validation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--windows",
        type=int,
        nargs="+",
        default=list(DEFAULT_WINDOWS),
        help="SPY SMA windows in sessions",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=OUTPUT_CSV)
    parser.add_argument(
        "--session-only",
        action="store_true",
        help="Exclude vendor rows stamped on non-SPY sessions (sensitivity check)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    windows = sorted(set(args.windows))
    print("Preparing current-canon score/parity frame once...", flush=True)
    base = prepare_base()
    spy = pd.read_csv(SPY_CSV, parse_dates=["Date"]).sort_values("Date")
    spy["Date"] = spy.Date.dt.normalize()
    if args.session_only:
        trading_days = set(spy.Date)
        before = len(base)
        base = base[base.entry_date.isin(trading_days)].copy()
        print(
            f"Session-only sensitivity: removed {before - len(base)} "
            "invariant-pool rows stamped on closed-market dates.",
            flush=True,
        )
    all_candidates = pd.read_parquet(FRAME, columns=["entry_date", "expiry_date"])
    all_candidates["entry_date"] = pd.to_datetime(all_candidates.entry_date)
    all_candidates["expiry_date"] = pd.to_datetime(all_candidates.expiry_date)
    split_bounds = {
        "IS_2020_2025": (
            all_candidates.loc[all_candidates.entry_date.dt.year <= 2025, "entry_date"].min(),
            all_candidates.loc[all_candidates.entry_date.dt.year <= 2025, "expiry_date"].max(),
        ),
        "OOT_2026": (
            all_candidates.loc[all_candidates.entry_date.dt.year == 2026, "entry_date"].min(),
            all_candidates.loc[all_candidates.entry_date.dt.year == 2026, "expiry_date"].max(),
        ),
    }

    print(
        f"Evaluating {windows} with {min(args.workers, len(windows))} workers...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=min(args.workers, len(windows))) as executor:
        results = list(
            executor.map(
                lambda w: evaluate_window(w, base, spy, split_bounds), windows
            )
        )

    rows = [row for result_rows, _ in results for row in result_rows]
    validations = {v["sma_window"]: v for _, v in results}
    out = pd.DataFrame(rows).sort_values(["period", "sma_window"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)

    control = validations.get(100)
    if control and not args.session_only:
        expected = (4280, 670)
        actual = (control["published_is_count"], control["published_oot_count"])
        if actual != expected:
            raise RuntimeError(
                f"100d control mismatch: got counts {actual}, expected {expected}"
            )
        print(
            "100d control reproduced: "
            f"IS n={actual[0]}, P&L=${control['published_is_pnl']:,.2f}; "
            f"2026 n={actual[1]}, P&L=${control['published_oot_pnl']:,.2f}",
            flush=True,
        )
    elif control:
        print(
            "100d session-only sensitivity: "
            f"IS n={control['published_is_count']}, "
            f"P&L=${control['published_is_pnl']:,.2f}; "
            f"2026 n={control['published_oot_count']}, "
            f"P&L=${control['published_oot_pnl']:,.2f}",
            flush=True,
        )

    display = out[
        [
            "period",
            "sma_window",
            "trades",
            "pnl",
            "weekly_sharpe",
            "cagr_pct",
            "max_drawdown_pct",
            "profitable_pct",
        ]
    ].copy()
    print(display.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
