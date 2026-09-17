"""Lagged SPY regime (bull vs bear) for the live tracker.

Wraps `spreads.build_regime_lookup` and returns today's classification.

The canonical rule uses only completed information at the 15:00 ET entry:
    bull  ⇔  prior-session SPY close > its 100-session SMA  ⇒  bull puts
    bear/unknown                                           ⇒  cash

The intraday SPY tick is deliberately not used for this gate.

The mock generator, the live ranker, and the webapp UI all derive direction
filtering from this single function so they cannot disagree.
"""
from __future__ import annotations
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
import spreads
from live import live_config


def _spy_csv_path() -> str:
    return os.path.join(backtest_config.DATA_DIR, "spy_us_d.csv")


def current_regime() -> dict:
    """Return the current SPY regime plus diagnostic context.

    Schema:
        {
          "regime":             "bull" | "bear",
          "as_of":              "YYYY-MM-DD",
          "close":              float,    # prior completed close
          "sma":                float,    # 100-day SMA
          "window":             100,
          "allowed_direction":  "bull_put" | None,
          "stale_days":         int,
          "source":             "Yahoo daily (prior session)",
        }
    """
    # Most recent completed daily close strictly before today.
    csv_path = _spy_csv_path()
    series = spreads.build_regime_lookup(csv_path, sma_window=backtest_config.REGIME_WINDOW)
    if series.empty:
        return _empty()

    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["SMA"] = df["Close"].rolling(
        window=backtest_config.REGIME_WINDOW,
        min_periods=backtest_config.REGIME_WINDOW,
    ).mean()
    today = pd.Timestamp.now().normalize()
    df = df.dropna(subset=["SMA"])
    df = df[df["Date"].dt.normalize() < today]
    if df.empty:
        return _empty()

    latest = df.iloc[-1]
    as_of = latest["Date"].date()
    regime = "bull" if latest["Close"] > latest["SMA"] else "bear"
    stale_days = (today.date() - as_of).days

    max_stale = getattr(backtest_config, "REGIME_MAX_STALE_CALENDAR_DAYS", None)
    if max_stale is not None and stale_days > max_stale:
        return {
            "regime": None,
            "as_of": as_of.isoformat(),
            "close": round(float(latest["Close"]), 2),
            "sma": round(float(latest["SMA"]), 2),
            "window": int(backtest_config.REGIME_WINDOW),
            "allowed_direction": None,
            "stale_days": int(stale_days),
            "source": "Yahoo daily (stale; cash)",
        }

    return {
        "regime":            regime,
        "as_of":             as_of.isoformat(),
        "close":             round(float(latest["Close"]), 2),
        "sma":               round(float(latest["SMA"]), 2),
        "window":            int(backtest_config.REGIME_WINDOW),
        "allowed_direction": "bull_put" if regime == "bull" else None,
        "stale_days":        int(stale_days),
        "source":            "Yahoo daily (prior session)",
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
        "source":            None,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(current_regime(), indent=2))
