"""MTM tracker for frozen daily snapshots, reading from the most recent
parallel-pull parquet (no extra IBKR fetch).

For each frozen file in live/frozen/*.json that is still "active":
  - outcome field is NOT yet set
  - expiry_date is today or in the future

  For each pick in top_picks:
    - Look up short+long legs in today's latest snapshot parquet
      (live/snapshots/YYYY-MM-DD/HHMM.parquet, written by pull_now_parallel.sh)
    - Compute current spread mark = short_mid - long_mid
    - Compute unrealized P&L per contract = (entry_credit - current_mark) * 100
    - Append a timestamped row to payload["tracking"][ticker]

Runs as the last step of cron_parallel.sh — no separate cron entry needed.
If a pick's strikes are no longer in the parquet (underlying moved outside
the ±7% strike band since freeze), that ticker is skipped with a log line.

Usage:
  python3 -m live.track_frozen                  # use today's latest snapshot
  python3 -m live.track_frozen --snapshot PATH  # explicit parquet path
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config


def _is_active(payload: dict) -> bool:
    """Worth tracking iff outcome is unset and expiry is today or future."""
    if payload.get("outcome"):
        return False
    picks = payload.get("top_picks") or []
    if not picks:
        return False
    exp_str = picks[0].get("expiry_date", "")
    try:
        exp_date = datetime.fromisoformat(exp_str).date()
    except (ValueError, TypeError):
        return False
    return exp_date >= date.today()


def _latest_snapshot_parquet(today: date) -> Path | None:
    """Most recent HHMM.parquet in today's snapshots dir, ignoring the
    per-group _gN.parquet intermediates produced by parallel runs."""
    date_dir = Path(live_config.SNAPSHOTS_DIR) / today.isoformat()
    if not date_dir.exists():
        return None
    files = [f for f in date_dir.glob("*.parquet") if "_" not in f.stem]
    if not files:
        return None
    return max(files, key=lambda p: p.stem)


def _lookup_leg(df: pd.DataFrame, ticker: str, expiry: pd.Timestamp,
                strike: float, put_or_call: str) -> dict | None:
    """Return {'bid', 'ask', 'underlying', 'last', 'iv'} or None if the row
    isn't in the snapshot or bid/ask are unusable."""
    rows = df[
        (df["Symbol"] == ticker) &
        (df["ExpirationDate"] == expiry) &
        (df["StrikePrice"].sub(strike).abs() < 1e-6) &
        (df["PutCall"] == put_or_call)
    ]
    if rows.empty:
        return None
    r = rows.iloc[0]
    bid = float(r["BidPrice"]) if r["BidPrice"] and r["BidPrice"] > 0 else None
    ask = float(r["AskPrice"]) if r["AskPrice"] and r["AskPrice"] > 0 else None
    if bid is None or ask is None:
        return None
    under = float(r["UnderlyingPrice"]) if "UnderlyingPrice" in r and r["UnderlyingPrice"] else None
    last = None
    if "LastPrice" in r and r["LastPrice"] and r["LastPrice"] > 0:
        last = float(r["LastPrice"])
    iv = None
    if "ImpliedVolatility" in r and r["ImpliedVolatility"] and 0 < float(r["ImpliedVolatility"]) < 5.0:
        iv = float(r["ImpliedVolatility"])
    return {"bid": bid, "ask": ask, "underlying": under, "last": last, "iv": iv}


def _underlying_from_snapshot(df: pd.DataFrame, ticker: str) -> float | None:
    """Get the underlying spot price for `ticker` from ANY row in the
    snapshot. Used as a fallback when the specific option leg isn't in
    the snapshot (e.g., the pick is past its expiry-fetch window)."""
    rows = df[df["Symbol"] == ticker]
    if rows.empty:
        return None
    val = rows.iloc[0]["UnderlyingPrice"]
    try:
        return float(val) if val and not pd.isna(val) else None
    except (ValueError, TypeError):
        return None


# Limit-order basis (canonical 2026-05-30, tightened 2026-05-30): 0.85 × LAST
# (15% haircut) when both legs have LastPrice > 0, else 0.80 × MID fallback.
# Matches webapp._enrich_pick. Raised the haircut from 5% to 15% because the
# +6.90 Sharpe at 1.00×LAST in backtest was suspiciously high — 0.85 gives a
# more realistic projection until live trades validate the optimistic basis.
LAST_PCT = 0.80          # canonical 2026-06-05: 20% haircut on LAST (was 15%, lowered for fill-quality buffer; backtest break-even at 0.65×LAST)
MID_FALLBACK_PCT = 0.80  # for entry credit when LAST missing
CLOSE_LAST_PCT = 1.15    # for close debit (we'd pay to buy back) — 15% above LAST
CLOSE_MID_PCT  = 1.20    # for close debit when LAST missing — 20% above MID
LIMIT_PCT = MID_FALLBACK_PCT  # kept for any external import; new code uses helper


def _track_pick(df: pd.DataFrame, pick: dict, existing_rows: list = None) -> dict | None:
    ticker = pick["ticker"]
    expiry = pd.Timestamp(pick["expiry_date"])
    put_or_call = "put" if pick["spread_type"] == "bull_put" else "call"

    short = _lookup_leg(df, ticker, expiry, float(pick["short_strike"]), put_or_call)
    long_ = _lookup_leg(df, ticker, expiry, float(pick["long_strike"]),  put_or_call)
    under = _underlying_from_snapshot(df, ticker)

    ts = datetime.now().isoformat(timespec="seconds")

    if short is None or long_ is None:
        # Option legs not in today's snapshot — typically because the fetcher
        # has rolled to next week's expiry (this week's options already settled
        # or are mid-expiry-day). Mark to INTRINSIC at current spot: exact at
        # expiry, near-correct intra-day on expiration day since extrinsic value
        # collapses fast in the final hours.
        if under is None:
            return None
        short_strike = float(pick["short_strike"])
        long_strike  = float(pick["long_strike"])
        if pick["spread_type"] == "bull_put":
            short_intr = max(0.0, short_strike - under)
            long_intr  = max(0.0, long_strike  - under)
        else:  # bear_call
            short_intr = max(0.0, under - short_strike)
            long_intr  = max(0.0, under - long_strike)
        mid_credit = float(pick.get("net_credit") or 0)
        mid_ml     = float(pick.get("max_loss") or 0)
        spread_w   = mid_credit + mid_ml
        current_mark = min(max(0.0, short_intr - long_intr), spread_w)

        # Entry credit (same priority as the main path: actual_credit > 0.85×freeze-LAST > 0.80×MID)
        psl = pick.get("short_last"); pll = pick.get("long_last")
        if psl and pll and psl > 0 and pll > 0:
            psb = pick.get("short_bid"); psa = pick.get("short_ask")
            plb = pick.get("long_bid");  pla = pick.get("long_ask")
            psl_eff = float(psl); pll_eff = float(pll)
            if psb is not None and psa is not None:
                psl_eff = max(float(psb), min(psl_eff, float(psa)))
            if plb is not None and pla is not None:
                pll_eff = max(float(plb), min(pll_eff, float(pla)))
            entry_credit = round(min(max(psl_eff - pll_eff, 0.0), spread_w) * LAST_PCT, 4)
        else:
            entry_credit = round(mid_credit * MID_FALLBACK_PCT, 4)
        max_loss = round(spread_w - entry_credit, 4)
        pnl_per_contract = round((entry_credit - current_mark) * 100, 2)
        pct_realized = round(((entry_credit - current_mark) / entry_credit) * 100, 2) \
            if entry_credit > 0 else 0.0
        return {
            "ts":                          ts,
            "underlying_price":            round(under, 2),
            "current_mark":                round(current_mark, 4),
            "mark_basis":                  "intrinsic (legs not in snapshot)",
            "entry_credit":                entry_credit,
            "max_loss":                    max_loss,
            "unrealized_pnl_per_contract": pnl_per_contract,
            "pct_max_win_realized":        pct_realized,
        }

    # MTM mark (canonical 2026-06-04): Black-Scholes theoretical close debit.
    # Rationale: deep-OTM weekly bid/ask is wide-and-fake (illiquid placeholder
    # quotes), LAST is often stale by hours/days. BS gives a deterministic,
    # IV-and-spot-driven number that always respects intrinsic. BBO/LAST are
    # still recorded in the tick payload as diagnostic.
    from .bs_pricing import bs_spread_debit
    sl_now, ll_now = short.get("last"), long_.get("last")
    mid_credit = float(pick.get("net_credit") or 0)
    mid_ml     = float(pick.get("max_loss") or 0)
    spread_w   = mid_credit + mid_ml

    days_to_expiry = max((expiry.date() - date.today()).days, 0)
    iv_short = short.get("iv")
    iv_long  = long_.get("iv")
    spot_for_bs = under if under is not None else (
        (short["bid"] + short["ask"] + long_["bid"] + long_["ask"]) / 4.0)
    bs_debit = bs_spread_debit(
        spot=spot_for_bs,
        short_strike=float(pick["short_strike"]),
        long_strike=float(pick["long_strike"]),
        short_iv=iv_short or iv_long or 0.25,  # last-resort 25% if both missing
        long_iv =iv_long  or iv_short or 0.25,
        dte_days=days_to_expiry,
        spread_type=pick["spread_type"],
    )
    current_mark = round(min(bs_debit, spread_w), 4)
    mark_basis = "BS_theo"

    # Entry basis: prefer frozen-pick short_last/long_last (clamped to that
    # row's bid/ask from freeze-time), fall back to 0.80×MID.
    psl = pick.get("short_last"); pll = pick.get("long_last")
    if psl and pll and psl > 0 and pll > 0:
        psb = pick.get("short_bid"); psa = pick.get("short_ask")
        plb = pick.get("long_bid");  pla = pick.get("long_ask")
        psl_eff = float(psl); pll_eff = float(pll)
        if psb is not None and psa is not None:
            psl_eff = max(float(psb), min(psl_eff, float(psa)))
        if plb is not None and pla is not None:
            pll_eff = max(float(plb), min(pll_eff, float(pla)))
        raw_e = max(psl_eff - pll_eff, 0.0)
        entry_credit = round(min(raw_e, spread_w) * LAST_PCT, 4)
    else:
        entry_credit = round(mid_credit * MID_FALLBACK_PCT, 4)
    entry_credit = min(entry_credit, round(spread_w, 4))
    max_loss     = round(spread_w - entry_credit, 4)
    pnl_per_contract = round((entry_credit - current_mark) * 100, 2)
    pct_realized = round(((entry_credit - current_mark) / entry_credit) * 100, 2) \
        if entry_credit > 0 else 0.0

    return {
        "ts":                          ts,
        "short_bid":                   round(short["bid"], 4),
        "short_ask":                   round(short["ask"], 4),
        "long_bid":                    round(long_["bid"], 4),
        "long_ask":                    round(long_["ask"], 4),
        "short_last":                  round(sl_now, 4) if sl_now else None,
        "long_last":                   round(ll_now, 4) if ll_now else None,
        "underlying_price":            round(under, 2) if under is not None else None,
        "current_mark":                current_mark,
        "mark_basis":                  mark_basis,
        "entry_credit":                entry_credit,
        "max_loss":                    max_loss,
        "unrealized_pnl_per_contract": pnl_per_contract,
        "pct_max_win_realized":        pct_realized,
    }


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=str, default=None,
                        help="Explicit parquet path (default: most recent in today's dir)")
    args = parser.parse_args()

    today = date.today()
    snap_path = Path(args.snapshot) if args.snapshot else _latest_snapshot_parquet(today)
    if snap_path is None or not snap_path.exists():
        print("No snapshot parquet available for tracking", flush=True)
        return 0

    print(f"Reading snapshot: {snap_path}", flush=True)
    df = pd.read_parquet(snap_path)
    print(f"  {len(df)} option rows loaded", flush=True)

    frozen_dir = Path(live_config.FROZEN_DIR)
    active: list[tuple[Path, dict]] = []
    for fp in sorted(frozen_dir.glob("*.json")):
        try:
            with open(fp) as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if _is_active(payload):
            active.append((fp, payload))

    if not active:
        print("No active frozen files", flush=True)
        return 0

    print(f"Tracking {len(active)} active frozen file(s)...", flush=True)
    for fp, payload in active:
        picks = payload.get("top_picks") or []
        print(f"  {fp.name}: {len(picks)} picks", flush=True)
        tracking = payload.setdefault("tracking", {})
        for pick in picks:
            tk  = pick["ticker"]
            row = _track_pick(df, pick, existing_rows=tracking.get(tk))
            if row is None:
                print(f"    [{tk}] not in snapshot (skip)", flush=True)
                continue
            tracking.setdefault(tk, []).append(row)
            if "current_mark" in row:
                print(f"    [{tk}] mark=${row['current_mark']}  "
                      f"P&L=${row['unrealized_pnl_per_contract']:+.2f}  "
                      f"({row['pct_max_win_realized']:+.1f}% of max win)", flush=True)
            else:
                print(f"    [{tk}] spot=${row.get('underlying_price')}  "
                      f"(option legs not in today's snapshot)", flush=True)
        _atomic_write(fp, payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
