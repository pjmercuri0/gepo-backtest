"""
Shared helpers for the bucket analysis scripts.
"""
import math
import os

import numpy as np
import pandas as pd
import requests

HERE          = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.normpath(os.path.join(HERE, ".."))
DATA_DIR      = os.path.join(PROJECT_ROOT, "data")
OUTPUT_DIR    = os.path.join(HERE, "output")
TRADES_CSV    = os.path.join(PROJECT_ROOT, "output", "all_trades.csv")
OPTS_PARQUET  = os.path.join(PROJECT_ROOT, "output", "options_filtered.parquet")
SPY_CSV       = os.path.join(DATA_DIR, "spy_us_d.csv")
VIX_CACHE     = os.path.join(HERE, "vix_daily.parquet")


# ── Wilson score interval ────────────────────────────────────────────────
def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p_hat  = successes / n
    denom  = 1.0 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    half   = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# ── Trade log loader (with derived columns) ──────────────────────────────
def load_trades() -> pd.DataFrame:
    df = pd.read_csv(TRADES_CSV)
    for col in ("n_samples", "reason", "best_ground"):
        if col in df.columns:
            df = df.drop(columns=col)
    df["entry_date"]   = pd.to_datetime(df["entry_date"])
    df["expiry_date"]  = pd.to_datetime(df["expiry_date"])
    df["credit_ratio"] = df["net_credit"] / df["max_loss"]
    df["is_win"]       = df["result"] == "WIN"
    df["is_loss"]      = df["result"] == "LOSS"
    df["is_partial"]   = df["result"] == "PARTIAL"
    return df


# ── SPY daily ─────────────────────────────────────────────────────────────
def load_spy_daily() -> pd.DataFrame:
    """Load SPY daily OHLC from data/spy_us_d.csv."""
    df = pd.read_csv(SPY_CSV)
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


# ── VIX daily (with disk cache) ──────────────────────────────────────────
def fetch_vix_cboe() -> pd.DataFrame:
    """Fetch official VIX daily history from CBOE."""
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    r = requests.get(url, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip().capitalize() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def load_vix_daily() -> pd.DataFrame:
    if os.path.exists(VIX_CACHE):
        return pd.read_parquet(VIX_CACHE)
    print("[helpers] fetching VIX from CBOE (one-time, will cache)...")
    df = fetch_vix_cboe()
    df.to_parquet(VIX_CACHE, index=False)
    print(f"[helpers] cached {len(df):,} VIX rows → {VIX_CACHE}")
    return df


# ── VIX percentile rank vs trailing 252 trading days ─────────────────────
def vix_with_pctile(window_days: int = 252) -> pd.DataFrame:
    df = load_vix_daily().copy()
    # Use closing VIX
    df["pctile_252"] = (
        df["Close"]
        .rolling(window=window_days, min_periods=60)
        .apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False)
    )
    return df


# ── Friday→Monday SPY gap ────────────────────────────────────────────────
def spy_monday_gap_pct(entry_dates: pd.Series) -> pd.Series:
    """
    For each Monday entry_date, compute (open - prev_close) / prev_close
    where prev_close = previous trading day's close (Friday or last trading
    day before entry_date).

    The Stooq SPY CSV has Date,Open,High,Low,Close,Volume. We use Open of
    the entry date and Close of the previous trading day.
    """
    spy = load_spy_daily()
    spy = spy[["Date", "Open", "Close"]].rename(
        columns={"Date": "spy_date", "Open": "spy_open", "Close": "spy_close"}
    )
    spy["prev_close"] = spy["spy_close"].shift(1)

    out = pd.DataFrame({"entry_date": pd.to_datetime(entry_dates).unique()})
    out = out.sort_values("entry_date").reset_index(drop=True)

    merged = pd.merge_asof(
        out,
        spy[["spy_date", "spy_open", "prev_close"]],
        left_on="entry_date",
        right_on="spy_date",
        direction="backward",
        tolerance=pd.Timedelta(days=4),
    )
    merged["gap_pct"] = (merged["spy_open"] - merged["prev_close"]) / merged["prev_close"]

    # Map back to a Series indexed by entry_date
    return merged.set_index("entry_date")["gap_pct"]


# ── Per-ticker IV percentile rank vs trailing window ─────────────────────
def per_ticker_iv_pctile(window_weeks: int = 52,
                         winsor_q: float = 0.99) -> pd.DataFrame:
    """
    Build a (Symbol, DataDate, iv_pctile) lookup using the options parquet.

    For each (Symbol, DataDate), use the median IV across the strikes
    available that day as the day's representative IV. Then for each
    point, compute its rank within that ticker's trailing window_weeks
    samples. The IV column is winsorized at the global winsor_q quantile
    before ranking.
    """
    df = pd.read_parquet(OPTS_PARQUET, columns=["Symbol", "DataDate", "ImpliedVolatility"])
    # Winsorize: cap the global IV column at the winsor_q quantile
    cap = df["ImpliedVolatility"].quantile(winsor_q)
    df["IV_w"] = df["ImpliedVolatility"].clip(upper=cap)

    # One representative IV per (Symbol, DataDate) via median
    daily = (
        df.groupby(["Symbol", "DataDate"])["IV_w"]
          .median()
          .rename("iv_repr")
          .reset_index()
          .sort_values(["Symbol", "DataDate"])
          .reset_index(drop=True)
    )

    # Trailing rank within each ticker (rank within trailing window).
    # Use a rolling apply that returns the percentile rank of the LAST
    # observation within its trailing window (inclusive).
    def trailing_rank(s: pd.Series) -> pd.Series:
        return s.rolling(window=window_weeks,
                         min_periods=max(8, window_weeks // 4)).apply(
            lambda x: (x.iloc[-1] >= x).mean(), raw=False
        )

    daily["iv_pctile"] = (
        daily.groupby("Symbol")["iv_repr"].transform(trailing_rank)
    )

    return daily[["Symbol", "DataDate", "iv_repr", "iv_pctile"]]
