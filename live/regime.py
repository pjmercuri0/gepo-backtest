"""Current SPY regime (bull vs bear) for the live tracker.

Wraps `spreads.build_regime_lookup` and returns today's classification.

The canonical rule:
    bull  ⇔  SPY close > SPY 100-day SMA  ⇒  only bull-put spreads allowed
    bear  ⇔  SPY close < SPY 100-day SMA  ⇒  only bear-call spreads allowed

Source preference (so the chip and subheader stay consistent):
  1. If `live/ranked/spy_intraday.json` exists and is < 60 min old, use its
     live IBKR tick as the current price. SMA still comes from the daily CSV.
  2. Else fall back to the most recent close in `data/spy_us_d.csv`.

The mock generator, the live ranker, and the webapp UI all derive direction
filtering from this single function so they cannot disagree.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
import spreads
from live import live_config


LIVE_TICK_PATH    = Path(live_config.RANKED_DIR) / "spy_intraday.json"
LIVE_TICK_MAX_AGE = timedelta(hours=1)   # treat older ticks as "stale, ignore"


def _spy_csv_path() -> str:
    return os.path.join(backtest_config.DATA_DIR, "spy_us_d.csv")


def _load_live_tick() -> dict | None:
    """Return the IBKR intraday tick if it exists AND is recent enough."""
    if not LIVE_TICK_PATH.exists():
        return None
    try:
        with open(LIVE_TICK_PATH) as f:
            tick = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    ts_str = tick.get("snapshot_ts")
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return None
    if datetime.now() - ts > LIVE_TICK_MAX_AGE:
        return None
    # Need both mark (live price) and sma_100 (regime threshold)
    if tick.get("mark") is None or tick.get("sma_100") is None:
        return None
    return tick


def current_regime() -> dict:
    """Return the current SPY regime plus diagnostic context.

    Schema:
        {
          "regime":             "bull" | "bear",
          "as_of":              "YYYY-MM-DD" or ISO timestamp if live,
          "close":              float,    # live mark if available, else daily close
          "sma":                float,    # 100-day SMA
          "window":             100,
          "allowed_direction":  "bull_put" | "bear_call",
          "stale_days":         int,      # 0 if live tick was used
          "source":             "IBKR live" | "Yahoo daily",
        }
    """
    # Prefer the live IBKR tick if it's fresh.
    tick = _load_live_tick()
    if tick is not None:
        mark = float(tick["mark"])
        sma  = float(tick["sma_100"])
        regime = "bull" if mark > sma else "bear"
        return {
            "regime":            regime,
            "as_of":             tick["snapshot_ts"],
            "close":             round(mark, 2),
            "sma":               round(sma, 2),
            "window":            int(tick.get("sma_window", backtest_config.REGIME_WINDOW)),
            "allowed_direction": "bull_put" if regime == "bull" else "bear_call",
            "stale_days":        0,
            "source":            "IBKR live",
        }

    # Fallback: most recent daily close from the SPY CSV.
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
        "source":            "Yahoo daily",
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
