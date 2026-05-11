"""Generate realistic mock data for the webapp before the IBKR feed is live.

Writes:
  live/ranked/latest.json          — current "live" snapshot, ~30 candidates
  live/frozen/YYYY-MM-DD.json (×7)  — last 7 weekdays of "frozen 15:45" snapshots

Schema matches live/ranker.py exactly, so the real fetcher overwrites these
files in place with no webapp changes.

Run:
  python3 -m live.mock_data            # populate (overwrites any existing mock)
  python3 -m live.mock_data --clear    # delete all mock data
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
from live import live_config
from live.regime import current_regime


# Plausible spot prices for SP100 names (approximate, May 2026 ballpark).
UNIVERSE = [
    ("AAPL",  225), ("MSFT",  445), ("NVDA", 140), ("GOOGL", 175),
    ("AMZN",  210), ("META",  580), ("TSLA", 245), ("JPM",   215),
    ("V",     290), ("MA",    490), ("UNH",  525), ("JNJ",   165),
    ("XOM",   115), ("CVX",   162), ("PG",   170), ("HD",    365),
    ("MRK",   125), ("ABBV",  185), ("PEP",  170), ("KO",     67),
    ("AVGO",  175), ("PFE",    28), ("COST", 845), ("TMO",   555),
    ("WMT",    85), ("MCD",   295), ("ACN",  335), ("ABT",   112),
    ("DHR",   245), ("NEE",    72), ("BMY",    52), ("ORCL", 140),
    ("ADBE",  475), ("CRM",   285), ("NFLX", 700), ("DIS",    92),
    ("PYPL",   68), ("INTC",   28), ("CSCO",   58), ("VZ",     42),
]


def _bs_rng(seed: int) -> random.Random:
    return random.Random(seed)


def _round_strike(spot: float, offset_pct: float, increment: float = 2.5) -> float:
    raw = spot * (1 + offset_pct)
    return round(raw / increment) * increment


def _gen_spread(rng: random.Random, ticker: str, spot: float, entry_date: date,
                allowed_direction: str | None = None) -> dict:
    """Synthesize a plausible spread candidate with internally consistent
    p, q, ro, G, DKL, GROUND values.

    `allowed_direction` enforces the regime filter:
      "bull_put"  → only bull-puts emitted
      "bear_call" → only bear-calls emitted
      None        → 78/22 mix (legacy)
    """
    if allowed_direction == "bull_put":
        spread_type = "bull_put"
    elif allowed_direction == "bear_call":
        spread_type = "bear_call"
    else:
        spread_type = "bull_put" if rng.random() < 0.78 else "bear_call"

    # Strike geometry: short ~0.5 delta side, long one strike further OTM.
    increment = 5.0 if spot > 200 else 2.5 if spot > 50 else 1.0
    if spread_type == "bull_put":
        short_strike = _round_strike(spot, rng.uniform(-0.03, 0.00), increment)
        long_strike  = short_strike - increment
        short_delta  = round(rng.uniform(0.38, 0.55), 3)
        long_delta   = round(short_delta * rng.uniform(0.35, 0.55), 3)
    else:  # bear_call
        short_strike = _round_strike(spot, rng.uniform(0.00, 0.03), increment)
        long_strike  = short_strike + increment
        short_delta  = round(rng.uniform(0.38, 0.55), 3)
        long_delta   = round(short_delta * rng.uniform(0.35, 0.55), 3)

    spread_width = round(abs(long_strike - short_strike), 2)
    # Credit as a fraction of width, in the canonical-realistic range 0.30-0.60.
    credit_frac  = rng.uniform(0.30, 0.55)
    net_credit   = round(spread_width * credit_frac, 2)
    max_loss     = round(spread_width - net_credit, 2)
    # Cap to MAX_MAX_LOSS = $5/share (canonical)
    if max_loss > backtest_config.MAX_MAX_LOSS:
        scale = backtest_config.MAX_MAX_LOSS / max_loss
        max_loss = backtest_config.MAX_MAX_LOSS
        net_credit = round(spread_width - max_loss, 2)

    iv = round(rng.uniform(0.18, 0.40), 4)
    dte = rng.choice([3, 4, 4, 5, 5, 6, 7])
    expiry = entry_date + timedelta(days=dte)

    # Probability triple (p win, ro partial, q loss). Match the canonical
    # LOSS_FACTOR=0.6 split on the ITM mass.
    itm_mass = short_delta            # canonical proxy
    q  = round(itm_mass * backtest_config.LOSS_FACTOR, 3)
    ro = round(itm_mass - q, 3)
    p  = round(1.0 - q - ro, 3)

    # G in trits (base-3). Realistic empirical range under canonical α=-0.5.
    G    = round(rng.uniform(0.005, 0.045), 5)
    # DKL from uniform: 1 - H_3(p). For roughly balanced 3-state distros
    # with small p,q tails, DKL sits in [0.05, 0.30].
    DKL  = round(rng.uniform(0.05, 0.28), 5)
    k    = 20.0
    # GROUND = G * 3^(-k*DKL). The realistic range is ~1e-6 to 0.01.
    GROUND = round(G * (3.0 ** (-k * DKL)), 8)

    w_star = round(rng.uniform(0.20, 0.65), 4)

    return {
        "ticker":           ticker,
        "spread_type":      spread_type,
        "entry_date":       entry_date.isoformat(),
        "expiry_date":      expiry.isoformat(),
        "entry_price":      round(spot, 2),
        "short_strike":     short_strike,
        "long_strike":      long_strike,
        "short_delta":      short_delta,
        "long_delta":       long_delta,
        "net_credit":       net_credit,
        "spread_width":     spread_width,
        "max_loss":         max_loss,
        "credit_ratio":     round(net_credit / max_loss, 4) if max_loss > 0 else None,
        "IV":               iv,
        "DTE":              dte,
        "p":                p,
        "q":                q,
        "ro":               ro,
        "G":                G,
        "DKL":              DKL,
        "GROUND":           GROUND,
        "w_star":           w_star,
    }


def _build_payload(when: datetime, n_candidates: int = 30, seed: int = 0,
                   regime_info: dict | None = None) -> dict:
    rng = _bs_rng(seed)
    spot_jitter = lambda s: round(s * rng.uniform(0.97, 1.03), 2)
    if regime_info is None:
        regime_info = current_regime()
    allowed = regime_info.get("allowed_direction")

    picks = []
    used = set()
    universe = list(UNIVERSE)
    rng.shuffle(universe)
    for ticker, spot in universe:
        if len(picks) >= n_candidates:
            break
        if ticker in used:
            continue
        s = _gen_spread(rng, ticker, spot_jitter(spot), when.date(), allowed_direction=allowed)
        picks.append(s)
        used.add(ticker)

    # Sort by GROUND descending and renumber for top picks.
    picks.sort(key=lambda r: r["GROUND"], reverse=True)
    top_picks = picks[:live_config.TOP_N_DISPLAY]
    ticker_rows = picks[:live_config.TICKER_LIMIT]

    return {
        "snapshot_ts":   when.isoformat(timespec="seconds"),
        "snapshot_file": f"live/snapshots/{when.strftime('%Y-%m-%d')}/{when.strftime('%H%M')}.parquet  [MOCK]",
        "data_date":     when.date().isoformat(),
        "n_candidates":  len(picks),
        "config": {
            "DTE_MIN":           backtest_config.DTE_MIN,
            "DTE_MAX":           backtest_config.DTE_MAX,
            "DELTA_MIN":         backtest_config.DELTA_MIN,
            "DELTA_MAX":         backtest_config.DELTA_MAX,
            "MIN_CREDIT_RATIO":  backtest_config.MIN_CREDIT_RATIO,
            "MIN_OPEN_INTEREST": backtest_config.MIN_OPEN_INTEREST,
            "MAX_MAX_LOSS":      backtest_config.MAX_MAX_LOSS,
            "GROUND_THRESHOLD":  backtest_config.GROUND_THRESHOLD,
            "TOP_N":             live_config.TOP_N_DISPLAY,
            "DKL_K":             20.0,
            "ALPHA":             backtest_config.ALPHA,
        },
        "regime":    regime_info,
        "top_picks": top_picks,
        "ticker":    ticker_rows,
    }


# ── IO ──────────────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _previous_weekdays(today: date, n: int) -> list[date]:
    out = []
    d = today
    while len(out) < n:
        d = d - timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            out.append(d)
    return out


# ── Main ────────────────────────────────────────────────────────────────────

def clear_mock():
    for f in Path(live_config.RANKED_DIR).glob("*.json"):
        f.unlink()
    for f in Path(live_config.FROZEN_DIR).glob("*.json"):
        f.unlink()
    print("cleared live/ranked/*.json and live/frozen/*.json")


def populate_mock():
    today = datetime.now()

    # 1. Latest "live" snapshot — current timestamp.
    latest = _build_payload(today, n_candidates=28, seed=int(today.timestamp()) % 100000)
    _atomic_write_json(Path(live_config.RANKED_DIR) / "latest.json", latest)
    print(f"wrote live/ranked/latest.json ({latest['n_candidates']} candidates)")

    # 2. Frozen daily snapshots for the last 7 weekdays. Each frozen snapshot
    # is timestamped at 15:45 of that day.
    for i, d in enumerate(_previous_weekdays(today.date(), 7)):
        ts = datetime.combine(d, datetime.strptime("15:45", "%H:%M").time())
        payload = _build_payload(ts, n_candidates=15, seed=i * 17 + 9001)
        payload["frozen_at"] = "15:45"
        out = Path(live_config.FROZEN_DIR) / f"{d.isoformat()}.json"
        _atomic_write_json(out, payload)
        print(f"wrote {out.relative_to(ROOT)} ({len(payload['top_picks'])} picks)")

    print("\nmock data ready. start the webapp:")
    print("  pip3 install -r live/requirements.txt   # if not done")
    print("  python3 -m live.webapp                  # http://127.0.0.1:5050")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true",
                        help="Delete all mock JSON instead of generating")
    args = parser.parse_args()
    if args.clear:
        clear_mock()
    else:
        populate_mock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
