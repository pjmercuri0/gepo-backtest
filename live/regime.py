"""Current SPY regime (bull vs bear) for the live tracker.

Wraps `spreads.build_regime_lookup` and returns today's classification (or
the most recent trading day available in the SPY CSV). The canonical rule:

    bull  ⇔  SPY close > SPY 100-day SMA  ⇒  only bull-put spreads allowed
    bear  ⇔  SPY close < SPY 100-day SMA  ⇒  only bear-call spreads allowed

The mock generator, the live ranker, and the webapp UI all derive direction
filtering from this single function so they cannot disagree.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
import spreads


def _spy_csv_path() -> str:
    return os.path.join(backtest_config.DATA_DIR, "spy_us_d.csv")


def current_regime() -> dict:
    """Return the current SPY regime plus diagnostic context.

    Output schema:
        {
          "regime":   "bull" | "bear",
          "as_of":    "YYYY-MM-DD",        # most recent trading day in SPY CSV
          "close":    float,               # SPY close on that day
          "sma":      float,               # 100-day SMA on that day
          "window":   100,                 # SMA window
          "allowed_direction":  "bull_put" | "bear_call",
          "stale_days": int                # gap between today and as_of
        }

    If the SPY CSV is missing or has insufficient history, returns
    `{"regime": None, ...}` with the same keys (None where unknown).
    """
    csv_path = _spy_csv_path()
    series = spreads.build_regime_lookup(csv_path, sma_window=backtest_config.REGIME_WINDOW)
    if series.empty:
        return _empty()

    # Also pull the raw close + SMA for the most recent day, for display.
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["SMA"] = df["Close"].rolling(
        window=backtest_config.REGIME_WINDOW,
        min_periods=backtest_config.REGIME_WINDOW,
    ).mean()
    df = df.dropna(subset=["SMA"])
    if df.empty:
        return _empty()

    latest = df.iloc[-1]
    as_of = latest["Date"].date()
    regime = "bull" if latest["Close"] > latest["SMA"] else "bear"
    today = pd.Timestamp.now().date()
    stale_days = (today - as_of).days

    return {
        "regime":            regime,
        "as_of":             as_of.isoformat(),
        "close":             round(float(latest["Close"]), 2),
        "sma":               round(float(latest["SMA"]), 2),
        "window":            int(backtest_config.REGIME_WINDOW),
        "allowed_direction": "bull_put" if regime == "bull" else "bear_call",
        "stale_days":        int(stale_days),
    }


def _empty() -> dict:
    return {
        "regime":            None,
        "as_of":             None,
        "close":             None,
        "sma":               None,
        "window":            int(backtest_config.REGIME_WINDOW),
        "allowed_direction": None,
        "stale_days":        None,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(current_regime(), indent=2))
