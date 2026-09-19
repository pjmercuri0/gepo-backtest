"""Research a complementary bear-call sleeve for the current D_ent canon.

The production bull sleeve is frozen.  Candidate bear calls are considered
only in causally classified SPY bear regimes, scored with the same current
P_real/D_ent machinery, and selected on 2020-2025 only.  The 2026 results are
attached after the IS grid has been ranked.

This script is research-only and does not modify production configuration.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ent_canon as ec
from sma_bull_regime_sweep import (
    CHAIN_FEATURES,
    FRAME,
    GAP_SERIES,
    SPY_CSV,
    metrics,
    prior_spy_bull,
)


OUT = ROOT / "output/bear_regime_sweep.csv"
DIVIDENDS = ROOT / "output/yahoo_dividend_history.csv"
EARNINGS = ROOT / "output/nasdaq_earnings_history.csv"
START = 10_000.0
REGIMES = (
    "below_100",
    "below_100_falling",
    "below_100_and_below_20",
    "below_100_with_20_below_100",
    "below_200_with_50_below_200",
)
PARITY_CUTS = (0.0, 0.12, 0.25, 0.40, 0.50, 0.60, 0.75)
GROUND_CUTS = (0.001, 0.003, 0.005, 0.010, 0.020)
CAPS = (1, 2, 3, 5)


def prepare() -> tuple[pd.DataFrame, pd.DataFrame]:
    c = pd.read_parquet(FRAME).dropna(subset=["EV"]).copy()
    c["entry_date"] = pd.to_datetime(c.entry_date).dt.normalize()
    c["expiry_date"] = pd.to_datetime(c.expiry_date).dt.normalize()
    closes = ec.backtest_closes()
    mu = ec.gap_drift(c, closes, pd.read_parquet(GAP_SERIES))
    p = ec.p_real(c, closes, mu=mu)
    c["p"], c["q"], c["ro"] = p[:, 0], p[:, 1], p[:, 2]
    b = c.model_credit.to_numpy(float) / (
        c.width.to_numpy(float) - c.model_credit.to_numpy(float)
    )
    _, ell = ec.kelly(
        np.nan_to_num(c.p.to_numpy(float)),
        np.nan_to_num(c.q.to_numpy(float)),
        np.nan_to_num(c.ro.to_numpy(float)),
        b,
    )
    ell[~np.isfinite(c.p.to_numpy(float))] = np.nan
    c["EV"] = np.exp(ell) - 1.0
    c = c.dropna(subset=["EV"]).copy()
    c["GROUND"] = c.EV * np.exp(-ec.K * c.D_ent)

    f = pd.read_parquet(
        CHAIN_FEATURES,
        columns=["ticker", "entry_date", "expiry_date", "cp_iv_gap"],
    )
    f["entry_date"] = pd.to_datetime(f.entry_date).dt.normalize()
    f["expiry_date"] = pd.to_datetime(f.expiry_date).dt.normalize()
    c = c.merge(f, on=["ticker", "entry_date", "expiry_date"], how="left")

    # Current bull parity, used only to reproduce the frozen bull sleeve.
    c["parity_bull_raw"] = -c.cp_iv_gap
    c = ec.add_parity_percentile(c)

    # Mirror the causally fitted sign for bear calls.  High = more bearish.
    signs = c.entry_date.dt.year.map(ec.parity_sign)
    c["bear_parity_raw"] = pd.to_numeric(c.cp_iv_gap, errors="coerce") * signs
    c["bear_parity_pct"] = 0.5
    active = (
        c.spread_type.eq("bear_call")
        & signs.ne(0)
        & c.bear_parity_raw.notna()
    )
    c.loc[active, "bear_parity_pct"] = (
        c.loc[active]
        .groupby("entry_date", sort=False)
        .bear_parity_raw.rank(method="average", pct=True)
    )

    spy = pd.read_csv(SPY_CSV, parse_dates=["Date"]).sort_values("Date")
    spy["Date"] = spy.Date.dt.normalize()
    c = c[c.entry_date.isin(set(spy.Date))].copy()
    return c, spy


def add_regimes(c: pd.DataFrame, spy: pd.DataFrame) -> pd.DataFrame:
    s = spy.copy()
    for n in (20, 50, 100, 200):
        s[f"sma{n}"] = s.Close.rolling(n, min_periods=n).mean()
    s["sma100_lag20"] = s.sma100.shift(20)

    dates = s.Date.to_numpy(dtype="datetime64[ns]")
    entries = c.entry_date.to_numpy(dtype="datetime64[ns]")
    pos = np.searchsorted(dates, entries, side="left") - 1
    safe = np.clip(pos, 0, len(s) - 1)
    stale = (entries - dates[safe]).astype("timedelta64[D]").astype(int)
    valid = (pos >= 0) & (stale <= ec_config_stale_days())

    def prior(col: str) -> np.ndarray:
        a = s[col].to_numpy(float)[safe]
        a[~valid] = np.nan
        return a

    close = prior("Close")
    sma20, sma50, sma100, sma200 = (
        prior("sma20"),
        prior("sma50"),
        prior("sma100"),
        prior("sma200"),
    )
    sma100_lag20 = prior("sma100_lag20")
    c = c.copy()
    c["below_100"] = close < sma100
    c["below_100_falling"] = (close < sma100) & (sma100 < sma100_lag20)
    c["below_100_and_below_20"] = (close < sma100) & (close < sma20)
    c["below_100_with_20_below_100"] = (close < sma100) & (sma20 < sma100)
    c["below_200_with_50_below_200"] = (close < sma200) & (sma50 < sma200)
    return c


def add_exdiv_gate(c: pd.DataFrame) -> pd.DataFrame:
    """Mark ANY spread exposed to an ex-date from entry through expiry+1.

    2026-09-19 (user): applies to bull puts as well as bear calls.  The reasons
    differ -- a short call risks early exercise into the dividend, a short put
    just eats the ex-date price drop as a directional headwind -- but both are
    real and the gate is now symmetric.

    MMC is failed closed because Yahoo returned 404 for that ticker during the
    historical-calendar build.  Other symbols with no rows were fetched
    successfully and therefore represent non-payers over the requested range.
    """
    if not DIVIDENDS.exists():
        raise FileNotFoundError(
            f"{DIVIDENDS} missing; run research/fetch_yahoo_dividend_history.py"
        )
    d = pd.read_csv(DIVIDENDS, parse_dates=["ExDividendDate"])
    by_symbol = {
        symbol: dates.to_numpy(dtype="datetime64[D]")
        for symbol, dates in d.groupby("Symbol").ExDividendDate
    }
    hit = np.zeros(len(c), dtype=bool)
    for i, row in enumerate(c.itertuples(index=False)):
        if row.ticker == "MMC":
            hit[i] = True
            continue
        dates = by_symbol.get(row.ticker)
        if dates is None:
            continue
        start = np.datetime64(pd.Timestamp(row.entry_date).date(), "D")
        end = np.datetime64((pd.Timestamp(row.expiry_date) + pd.Timedelta(days=1)).date(), "D")
        left = np.searchsorted(dates, start, side="left")
        hit[i] = left < len(dates) and dates[left] <= end
    out = c.copy()
    out["exdiv_hit"] = hit
    return out


def add_earnings_gate(c: pd.DataFrame) -> pd.DataFrame:
    """Apply the live entry-through-expiry earnings exclusion historically.

    2026-09-19 (user): applies to bull puts as well as bear calls.
    """
    if not EARNINGS.exists():
        raise FileNotFoundError(
            f"{EARNINGS} missing; run research/fetch_nasdaq_earnings_history.py"
        )
    e = pd.read_csv(EARNINGS, parse_dates=["EarningsDate"])
    by_symbol = {
        symbol: dates.to_numpy(dtype="datetime64[D]")
        for symbol, dates in e.groupby("Symbol").EarningsDate
    }
    hit = np.zeros(len(c), dtype=bool)
    for i, row in enumerate(c.itertuples(index=False)):
        # MMC was the only operating-company symbol absent from the complete
        # NASDAQ pull; ETFs and indices correctly have no earnings events.
        if row.ticker == "MMC":
            hit[i] = True
            continue
        dates = by_symbol.get(row.ticker)
        if dates is None:
            continue
        start = np.datetime64(pd.Timestamp(row.entry_date).date(), "D")
        end = np.datetime64(pd.Timestamp(row.expiry_date).date(), "D")
        left = np.searchsorted(dates, start, side="left")
        hit[i] = left < len(dates) and dates[left] <= end
    out = c.copy()
    out["earnings_hit"] = hit
    return out


def ec_config_stale_days() -> int:
    # Kept local so the research script remains tied to the production rule
    # without importing generic run.py side effects.
    import config

    return int(config.REGIME_MAX_STALE_CALENDAR_DAYS)


def realize(pool: pd.DataFrame, cap: int, fill: float = ec.FILL_MULT) -> pd.DataFrame:
    sel = (
        pool.sort_values(["entry_date", "GROUND"], ascending=[True, False])
        .groupby("entry_date", sort=False)
        .head(cap)
        .copy()
    )
    sel["credit"] = (sel.model_credit * fill).round(4)
    sel["max_loss_adj"] = (sel.width - sel.credit).round(4)
    sel = sel[sel.max_loss_adj > 0].copy()
    spot = sel.expiry_close.to_numpy(float)
    short = sel.short_strike.to_numpy(float)
    long = sel.long_strike.to_numpy(float)
    credit = sel.credit.to_numpy(float)
    max_loss = sel.max_loss_adj.to_numpy(float)
    bull_put = sel.spread_type.eq("bull_put").to_numpy()
    win = np.where(bull_put, spot > short, spot < short)
    loss = np.where(bull_put, spot <= long, spot >= long)
    partial = ~(win | loss)
    partial_pnl = np.where(
        bull_put,
        credit - (short - spot),
        credit - (spot - short),
    )
    pnl = np.where(win, credit, np.where(loss, -max_loss, partial_pnl)) * 100.0
    pnl[partial & (pnl > 0)] *= 0.5
    sel["pnl_per_contract"] = pnl - ec.COMMISSION
    sel["max_loss_dollar"] = max_loss * 100.0
    sel["_outcome"] = np.where(win, "WIN", np.where(loss, "LOSS", "PARTIAL"))
    return sel


def period_bounds() -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    d = pd.read_parquet(FRAME, columns=["entry_date", "expiry_date"])
    d["entry_date"] = pd.to_datetime(d.entry_date)
    d["expiry_date"] = pd.to_datetime(d.expiry_date)
    return {
        "IS": (
            d.loc[d.entry_date.dt.year <= 2025, "entry_date"].min(),
            d.loc[d.entry_date.dt.year <= 2025, "expiry_date"].max(),
        ),
        "OOT": (
            d.loc[d.entry_date.dt.year == 2026, "entry_date"].min(),
            d.loc[d.entry_date.dt.year == 2026, "expiry_date"].max(),
        ),
    }


def summarize(
    picks: pd.DataFrame,
    spy: pd.DataFrame,
    period: str,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> dict:
    if period == "IS":
        x = picks[picks.entry_date.dt.year <= 2025]
    else:
        x = picks[picks.entry_date.dt.year == 2026]
    return metrics(x, spy, period, *bounds[period])


def bootstrap_positive_probability(
    picks: pd.DataFrame, spy: pd.DataFrame, n_boot: int = 10_000
) -> tuple[float, float, float]:
    """IID weekly bootstrap of IS incremental P&L; descriptive, not FWER-adjusted."""
    end = pd.Timestamp("2025-12-31")
    start = pd.Timestamp("2020-07-14")
    cal = pd.DatetimeIndex(spy.loc[spy.Date.between(start, end), "Date"])
    pnl = picks[picks.entry_date.dt.year <= 2025].groupby("expiry_date").pnl_per_contract.sum()
    weekly = pnl.reindex(cal, fill_value=0.0).resample("W-FRI").sum().to_numpy(float)
    rng = np.random.default_rng(20260917)
    means = rng.choice(weekly, size=(n_boot, len(weekly)), replace=True).mean(axis=1)
    return float((means > 0).mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def main() -> None:
    print("Preparing current-canon candidate frame...", flush=True)
    c, spy = prepare()
    c = add_regimes(c, spy)
    c = add_exdiv_gate(c)
    c = add_earnings_gate(c)
    bounds = period_bounds()

    bull_mask = prior_spy_bull(c.entry_date, spy, 100)
    bull_pool = c[
        c.spread_type.eq("bull_put")
        & bull_mask
        & (c.parity_pct > ec.PARITY_MIN_PCT)
        & (c.GROUND >= ec.THR)
    ]
    bull = realize(bull_pool, ec.TOP_N)
    base_is = summarize(bull, spy, "IS", bounds)
    base_oot = summarize(bull, spy, "OOT", bounds)
    print(
        f"Frozen bull sleeve: IS ${base_is['pnl']:,.0f} Sh {base_is['weekly_sharpe']:.2f} "
        f"DD {base_is['max_drawdown_pct']:.1f}%; "
        f"OOT ${base_oot['pnl']:,.0f} Sh {base_oot['weekly_sharpe']:.2f}",
        flush=True,
    )

    dropped_exdiv = int((c.spread_type.eq("bear_call") & c.exdiv_hit).sum())
    print(f"Historical ex-div gate: excluded {dropped_exdiv:,} bear-call candidates")
    dropped_earnings = int(
        (c.spread_type.eq("bear_call") & ~c.exdiv_hit & c.earnings_hit).sum()
    )
    print(
        f"Historical earnings gate: excluded {dropped_earnings:,} additional "
        "bear-call candidates"
    )
    bear = c[
        c.spread_type.eq("bear_call") & ~c.exdiv_hit & ~c.earnings_hit
    ].copy()
    rows: list[dict] = []
    for regime, parity, ground, cap in itertools.product(
        REGIMES, PARITY_CUTS, GROUND_CUTS, CAPS
    ):
        pool = bear[
            bear[regime]
            & (bear.bear_parity_pct > parity)
            & (bear.GROUND >= ground)
        ]
        picks = realize(pool, cap)
        bear_is = summarize(picks, spy, "IS", bounds)
        combined_is = summarize(pd.concat([bull, picks]), spy, "IS", bounds)
        stress_104 = realize(pool, cap, 1.04)
        stress_100 = realize(pool, cap, 1.00)
        year_pnl = (
            picks[picks.entry_date.dt.year <= 2025]
            .assign(year=lambda x: x.entry_date.dt.year)
            .groupby("year")
            .pnl_per_contract.sum()
        )
        rows.append(
            {
                "regime": regime,
                "parity_cut": parity,
                "ground_cut": ground,
                "cap": cap,
                "bear_is_n": bear_is["trades"],
                "bear_is_pnl": bear_is["pnl"],
                "bear_is_sharpe": bear_is["weekly_sharpe"],
                "bear_is_dd": bear_is["max_drawdown_pct"],
                "bear_2022_pnl": year_pnl.get(2022, 0.0),
                "bear_2023_pnl": year_pnl.get(2023, 0.0),
                "bear_is_pnl_fill104": stress_104.loc[
                    stress_104.entry_date.dt.year <= 2025, "pnl_per_contract"
                ].sum(),
                "bear_is_pnl_fill100": stress_100.loc[
                    stress_100.entry_date.dt.year <= 2025, "pnl_per_contract"
                ].sum(),
                "combined_is_pnl": combined_is["pnl"],
                "combined_is_sharpe": combined_is["weekly_sharpe"],
                "combined_is_dd": combined_is["max_drawdown_pct"],
            }
        )

    result = pd.DataFrame(rows)
    result["improves_is"] = (
        (result.combined_is_pnl > base_is["pnl"])
        & (result.combined_is_sharpe > base_is["weekly_sharpe"])
        & (result.combined_is_dd > base_is["max_drawdown_pct"])
        & (result.bear_2022_pnl > 0)
        & (result.bear_2023_pnl > 0)
        & (result.bear_is_pnl_fill104 > 0)
    )
    result["is_rank"] = (
        result.combined_is_sharpe.rank(ascending=False, method="min")
        + result.combined_is_pnl.rank(ascending=False, method="min")
        + result.combined_is_dd.rank(ascending=False, method="min")
    )

    # Freeze ordering on IS, then reveal 2026 for every row without reranking.
    result = result.sort_values(
        ["improves_is", "is_rank"], ascending=[False, True]
    ).reset_index(drop=True)
    oot_cols = []
    for r in result.itertuples(index=False):
        pool = bear[
            bear[r.regime]
            & (bear.bear_parity_pct > r.parity_cut)
            & (bear.GROUND >= r.ground_cut)
        ]
        picks = realize(pool, int(r.cap))
        bear_oot = summarize(picks, spy, "OOT", bounds)
        combined_oot = summarize(pd.concat([bull, picks]), spy, "OOT", bounds)
        oot_cols.append(
            {
                "bear_oot_n": bear_oot["trades"],
                "bear_oot_pnl": bear_oot["pnl"],
                "bear_oot_sharpe": bear_oot["weekly_sharpe"],
                "combined_oot_pnl": combined_oot["pnl"],
                "combined_oot_sharpe": combined_oot["weekly_sharpe"],
                "combined_oot_dd": combined_oot["max_drawdown_pct"],
            }
        )
    result = pd.concat([result, pd.DataFrame(oot_cols)], axis=1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT, index=False)

    viable = result[result.improves_is]
    print(f"IS-improving/stress-positive cells: {len(viable)} / {len(result)}")
    show = result.head(25)[
        [
            "regime", "parity_cut", "ground_cut", "cap",
            "bear_is_n", "bear_is_pnl", "bear_2022_pnl", "bear_2023_pnl",
            "combined_is_sharpe", "combined_is_dd",
            "bear_oot_n", "bear_oot_pnl", "combined_oot_sharpe", "combined_oot_dd",
        ]
    ]
    print(show.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    if not viable.empty:
        best = viable.iloc[0]
        pool = bear[
            bear[str(best.regime)]
            & (bear.bear_parity_pct > best.parity_cut)
            & (bear.GROUND >= best.ground_cut)
        ]
        picks = realize(pool, int(best.cap))
        prob, lo, hi = bootstrap_positive_probability(picks, spy)
        print(
            "Top IS-ranked cell weekly-bootstrap mean P&L: "
            f"P(positive)={prob:.3f}, 95% interval [{lo:.2f}, {hi:.2f}] dollars/week"
        )
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
