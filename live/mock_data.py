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

# Canonical k mirror. Source of truth is DKL_K but mock_data is
# kept self-contained (no import of ground) so it runs on hosts that
# don't have the full backtest dependency tree.
DKL_K = 20.0


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

    import math as _m

    # Probability triple (p win, ro partial, q loss) via the canonical
    # delta-arithmetic decomposition, matching ground.py / historical_probs.py:
    #   p  = 1 − |Δ_short|, q = |Δ_long|, ro = |Δ_short| − |Δ_long|.
    ld = min(long_delta, short_delta)  # guard against quote inversions
    q  = round(ld, 3)
    ro = round(max(0.0, short_delta - ld), 3)
    p  = round(1.0 - q - ro, 3)

    # Per-spread payoffs and Kelly geometry.
    b_mock     = net_credit / max_loss if max_loss > 0 else 0.0
    alpha_mock = (b_mock - 1.0) / (2.0 * b_mock) if 0 < b_mock < 1.0 else 0.0

    # Kelly-optimal sizing w* and log-growth ℓ(w*) from the canonical
    # quadratic, mirroring ground.py exactly. The quadratic Aw² + Bw + C
    # = 0 comes from the FOC d/dw E_p[ln(1 + w·X)] = 0 under outcomes
    # {+b, +αb, -1} with α = α(b). The unique in-(0,1) root is w*.
    w_star = None
    G      = None
    if 0 < b_mock and 0 < p < 1 and 0 < q < 1 and 0 < ro < 1:
        A = -alpha_mock * b_mock * b_mock
        B = (alpha_mock * b_mock * b_mock * (p + ro)
             - b_mock * (p + ro * alpha_mock + q * (1.0 + alpha_mock)))
        C = p * b_mock + ro * alpha_mock * b_mock - q
        if A == 0:
            if b_mock > 0:
                w_candidate = (p * b_mock - q) / b_mock
                if 0 < w_candidate < 1:
                    w_star = w_candidate
        else:
            disc = B * B - 4 * A * C
            if disc >= 0:
                s = _m.sqrt(disc)
                roots = [( -B - s) / (2 * A), (-B + s) / (2 * A)]
                cands = [r for r in roots if 0 < r < 1]
                if cands:
                    w_star = cands[0]
    if w_star is None:
        # Degenerate candidate (no admissible root). Fall back to a small
        # plausible w so the row still has finite display values; the
        # ranker filters it out via G ≤ 0.
        w_star = 0.05
        G = 0.0
    else:
        w_star = max(0.01, min(0.99, w_star))
        # ℓ(w*) = E_p[ln(1 + w*·X)] under the three-outcome payoffs.
        try:
            G = (p * _m.log(1.0 + w_star * b_mock)
                 + ro * _m.log(1.0 + w_star * alpha_mock * b_mock)
                 + q * _m.log(1.0 - w_star))
        except ValueError:
            G = 0.0
    w_star = round(w_star, 4)
    G      = round(max(G, 0.0), 5)

    # DKL from uniform u_3 = (1/3, 1/3, 1/3) in nats. Computed exactly
    # from the displayed (p, ro, q) so the live ticker is internally
    # consistent: a probability triple close to uniform produces a
    # small DKL, a sharply concentrated triple produces a large DKL.
    _u = 1.0 / 3.0
    DKL = round(
        sum(x * _m.log(x / _u) for x in (p, ro, q) if x > 0),
        5,
    )
    k = DKL_K  # canonical k = 20 in nats

    # Canonical GROUND (2026-05-13+): Γᵢ = Kelly EV · exp(−k·DKL) where
    # E := exp(ℓ(w*)) − 1. Under Choice B, g := ln E so Γᵢ = exp(g − k·DKL).
    # Stored directly as positive fractional return; display layer renders
    # as +X.XX%. Filter G > 0 ensures E > 0.
    kelly_ev = _m.exp(G) - 1.0
    GROUND   = round(kelly_ev * _m.exp(-k * DKL), 8)

    # EV per dollar wagered = p·b + r₀·α(b)·b − q  (linear, variance-blind).
    # Display-only diagnostic; not used in canonical ranking.
    EV = round(p * b_mock + alpha_mock * b_mock * ro - q, 5)

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
        "EV":               EV,
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

    # Sort by GROUND descending. Mark qualified vs below-threshold.
    picks.sort(key=lambda r: r["GROUND"], reverse=True)
    threshold = backtest_config.GROUND_THRESHOLD if backtest_config.GROUND_THRESHOLD not in (None, float("-inf")) else 0.0
    for p in picks:
        p["qualified"] = p["GROUND"] >= threshold
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
            "MAX_CREDIT_RATIO":  (
                None if backtest_config.MAX_CREDIT_RATIO == float("inf")
                else backtest_config.MAX_CREDIT_RATIO
            ),
            "MIN_OPEN_INTEREST": backtest_config.MIN_OPEN_INTEREST,
            "MAX_MAX_LOSS":      backtest_config.MAX_MAX_LOSS,
            "GROUND_THRESHOLD":  (
                None if backtest_config.GROUND_THRESHOLD == float("-inf")
                else backtest_config.GROUND_THRESHOLD
            ),
            "TOP_N":             live_config.TOP_N_DISPLAY,
            "DKL_K":             DKL_K,
            "ALPHA":             "(b-1)/(2b)",
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
