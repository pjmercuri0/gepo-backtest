"""Watch open positions for early-assignment and pin-zone risk.

Nothing in the pipeline measured the thing that actually causes assignment.

Early exercise happens when a short leg's EXTRINSIC value goes to ~zero: the
holder gives up nothing by exercising now instead of at expiry. Depth in the
money is not the signal — on 2026-09-03 an AMGN short call 12.09 ITM still
carried 0.76 of extrinsic (nobody exercises that), while a DE short call
53.50 ITM carried about 0.04 (anybody would).

Extrinsic is measured, not modelled, via put-call parity: an ITM call's
extrinsic is the price of the SAME-STRIKE PUT, and an ITM put's extrinsic is
the price of the same-strike call. Verified against live quotes on
2026-09-02 — AMGN 0.76 vs 1.02, DIS 0.58 vs 0.62, SBUX 0.91 vs 0.93.

The other exposure is the PIN ZONE: spot finishing between the strikes, where
the short leg is assigned and the long expires worthless, so real shares are
delivered. Both legs ITM is NOT this case — those exercise against each other
and settle to cash.

Why this needs its own IB fetch: the scan fetches a strike band around current
spot, so a position goes INVISIBLE exactly when it moves deep ITM — which is
when assignment risk appears. DE's 647.50 strike sat 7.63% from spot and was
absent from every snapshot on 2026-09-02; the tracker fell back to intrinsic
marking and nothing flagged it. This module fetches each open position's own
strikes regardless of where spot has moved.

Read-only IB connection. It never places or closes an order — it reports.

Usage:  python3 -m live.assignment_risk [--client-id N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from ib_insync import IB, Option, Stock

from live import live_config
from live.fetcher import _connect_with_retry

ALERT_DIR = Path(live_config.NOTIFICATIONS_DIR)


def _open_positions(include_frozen: bool = False) -> list[dict]:
    """Unexpired positions you actually HOLD, from the actuals store.

    Frozen files are daily RECOMMENDATIONS, not holdings — including them made
    the first run flag CAT, CSX, TMO and a second HD spread the account never
    took. Alerting on a position you do not own is worse than not alerting:
    it invites a close order on something that is not there. actuals.json is
    the record of what was actually traded, so that is the default scope.
    --include-frozen restores the old behaviour for auditing a day's picks.

    Deduplicated on (ticker, spread_type, expiry, strikes).
    """
    today = date.today()
    out: dict[tuple, dict] = {}

    def add(pick: dict, source: str) -> None:
        try:
            exp = str(pick["expiry_date"])[:10]
            if datetime.strptime(exp, "%Y-%m-%d").date() < today:
                return
            key = (pick["ticker"], pick["spread_type"], exp,
                   float(pick["short_strike"]), float(pick["long_strike"]))
        except (KeyError, ValueError, TypeError):
            return
        out.setdefault(key, {
            "ticker": key[0], "spread_type": key[1], "expiry": key[2],
            "short_strike": key[3], "long_strike": key[4], "source": source,
        })

    store = Path(live_config.ROOT_DIR) / "actuals.json"
    try:
        for t in (json.loads(store.read_text()).get("trades") or []):
            add(t.get("pick") or {}, "actuals")
    except (OSError, json.JSONDecodeError):
        pass

    frozen = Path(live_config.FROZEN_DIR)
    if include_frozen and frozen.exists():
        for fp in sorted(frozen.glob("*.json")):
            try:
                payload = json.loads(fp.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            for pick in (payload.get("top_picks") or []):
                add(pick, f"frozen:{fp.stem}")

    return sorted(out.values(), key=lambda p: (p["expiry"], p["ticker"]))


def _mid(t) -> float | None:
    bid, ask = t.bid, t.ask
    ok = lambda x: x is not None and x == x and x > 0
    if ok(bid) and ok(ask):
        return (float(bid) + float(ask)) / 2.0
    if ok(getattr(t, "last", None)):
        return float(t.last)
    return None


def assess(ib: IB, positions: list[dict], wait: float = 8.0) -> list[dict]:
    """Quote each position's own strikes and score its risk."""
    if not positions:
        return []

    subs = []
    for p in positions:
        exp = p["expiry"].replace("-", "")
        short_right = "P" if p["spread_type"] == "bull_put" else "C"
        opp_right = "C" if short_right == "P" else "P"
        try:
            stock = ib.qualifyContracts(Stock(p["ticker"], "SMART", "USD"))[0]
            # The short leg, and the SAME-STRIKE opposite side whose price IS
            # the short leg's extrinsic value.
            legs = ib.qualifyContracts(
                Option(p["ticker"], exp, p["short_strike"], short_right,
                       exchange="SMART", currency="USD"),
                Option(p["ticker"], exp, p["short_strike"], opp_right,
                       exchange="SMART", currency="USD"),
            )
        except Exception as e:
            print(f"  [{p['ticker']}] qualify failed: {e}", flush=True)
            continue
        if len(legs) < 2:
            continue
        subs.append((p, ib.reqMktData(stock, "", False, False),
                     ib.reqMktData(legs[0], "", False, False),
                     ib.reqMktData(legs[1], "", False, False),
                     stock, legs))

    ib.sleep(wait)

    results = []
    for p, ts, t_short, t_opp, stock, legs in subs:
        spot = ts.marketPrice()
        if spot != spot:
            spot = ts.close
        extrinsic = _mid(t_opp)          # parity: opposite side at same strike
        short_mid = _mid(t_short)

        ss, ls = p["short_strike"], p["long_strike"]
        lo, hi = min(ss, ls), max(ss, ls)
        in_pin = spot is not None and spot == spot and lo < spot < hi

        if spot is not None and spot == spot:
            itm_by = (ss - spot) if p["spread_type"] == "bull_put" else (spot - ss)
        else:
            itm_by = None

        reasons = []
        # Early exercise is rational only when the BENEFIT of exercising now
        # exceeds the extrinsic the holder forfeits. A near-zero extrinsic makes
        # exercise cheap, not worthwhile — the first version of this file tested
        # extrinsic <= 0.10 alone and would have screamed about a DE short call
        # with 0.04 of extrinsic and NO dividend before expiry, where exercising
        # gains nothing at all (early exercise of an American call on a
        # non-dividend-paying stock is never optimal).
        #   calls: benefit = the dividend, and only if ex-div lands before expiry
        #   puts:  benefit = interest earned on the strike proceeds until expiry
        benefit, basis = _exercise_benefit(p)
        if (extrinsic is not None and itm_by is not None and itm_by > 0
                and benefit is not None and benefit > extrinsic):
            reasons.append(
                f"early exercise pays: {basis} {benefit:.2f} > extrinsic {extrinsic:.2f}")
        # Pin zone only bites at SETTLEMENT. Sitting between the strikes on a
        # Wednesday is just where the stock happens to be — it carries no
        # assignment consequence until expiry, and flagging it early trains you
        # to ignore the flag. Early exercise, by contrast, can happen any day.
        is_expiry_day = p["expiry"] == date.today().isoformat()
        if in_pin and is_expiry_day:
            reasons.append(f"pin zone {lo:.2f}-{hi:.2f}, spot {spot:.2f}")

        results.append({
            **p,
            "spot": None if spot is None or spot != spot else round(float(spot), 4),
            "short_mid": None if short_mid is None else round(short_mid, 4),
            "extrinsic": None if extrinsic is None else round(extrinsic, 4),
            "short_itm_by": None if itm_by is None else round(itm_by, 4),
            "in_pin_zone": bool(in_pin),
            "pin_live": bool(in_pin and is_expiry_day),
            "tag": ("PIN" if (in_pin and is_expiry_day) else
                    ("EXERCISE" if reasons else "")),
            "pin_zone": [lo, hi],
            "exercise_benefit": None if benefit is None else round(benefit, 4),
            "benefit_basis": basis,
            "at_risk": bool(reasons),
            "reasons": reasons,
        })
        for c in (stock, *legs):
            try:
                ib.cancelMktData(c)
            except Exception:
                pass
    return results


def _exercise_benefit(p: dict) -> tuple:
    """(benefit_per_share, description) of exercising this short leg early.

    For a CALL the benefit is the dividend captured by owning the stock over
    ex-div, and only when that ex-div falls on or before expiry. For a PUT it is
    the interest earned by receiving the strike proceeds now instead of at
    expiry. Returns (None, reason) when it cannot be determined.
    """
    exp = p["expiry"]
    if p["spread_type"] == "bear_call":
        try:
            import csv as _csv
            path = Path(getattr(live_config, "DATA_DIR",
                                Path(live_config.ROOT_DIR).parent / "data")) / "dividend_calendar.csv"
            best = None
            for row in _csv.DictReader(open(path)):
                if row.get("Symbol") != p["ticker"]:
                    continue
                dt = (row.get("ExDividendDate") or "").strip()
                amt = (row.get("Amount") or "").strip()
                if dt and dt <= exp and amt:
                    v = float(amt)
                    best = v if best is None else max(best, v)
            return (best or 0.0, f"dividend before {exp}")
        except Exception:
            return (None, "dividend unknown")
    # bull_put — carry on the strike until expiry
    try:
        days = max((datetime.strptime(exp, "%Y-%m-%d").date() - date.today()).days, 0)
        r = float(getattr(live_config, "LIVE_ASSIGN_RATE", 0.045))
        return (p["short_strike"] * r * days / 365.0, f"carry on {p['short_strike']:g} for {days}d")
    except Exception:
        return (None, "carry unknown")


def _summarise(r: dict) -> str:
    """One-line reason for the alert message."""
    head = f"{r['ticker']} {r['spread_type']} K={r['short_strike']:g}"
    if r["in_pin_zone"]:
        return f"{head} in pin zone {r['pin_zone'][0]:g}-{r['pin_zone'][1]:g}"
    return f"{head} extrinsic {r['extrinsic']}"


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", type=int,
                    default=int(getattr(live_config, "LIVE_ASSIGN_CLIENT_ID", 193)))
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--quiet", action="store_true",
                    help="only print positions that are at risk")
    ap.add_argument("--include-frozen", action="store_true",
                    help="also check frozen daily picks, not just held positions")
    args = ap.parse_args()

    positions = _open_positions(include_frozen=args.include_frozen)
    if not positions:
        print("No open positions to check", flush=True)
        return 0

    ib = IB()
    try:
        _connect_with_retry(ib, args.client_id)
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
        rows = assess(ib, positions, args.wait)
    except Exception as e:
        print(f"assignment_risk: IB fetch failed ({e})", flush=True)
        return 0
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    at_risk = [r for r in rows if r["at_risk"]]
    for r in rows:
        if args.quiet and not r["at_risk"]:
            continue
        ext = "  n/a" if r["extrinsic"] is None else f"{r['extrinsic']:5.2f}"
        itm = "  n/a" if r["short_itm_by"] is None else f"{r['short_itm_by']:+6.2f}"
        flag = "  <<< AT RISK" if r["at_risk"] else ""
        print(f"  {r['ticker']:<6}{r['spread_type']:<11}"
              f"K={r['short_strike']:<9.2f} spot={r['spot']}  "
              f"short ITM {itm}  extrinsic {ext}{flag}", flush=True)
        for why in r["reasons"]:
            print(f"      ! {why}", flush=True)

    if at_risk:
        ts = datetime.now()
        payload = {
            "type": "assignment-risk",
            "ts": ts.isoformat(timespec="seconds"),
            "host": os.uname().nodename,
            "n_at_risk": len(at_risk),
            "extrinsic_threshold": float(
                getattr(live_config, "LIVE_ASSIGN_EXTRINSIC_ALERT", 0.10)),
            "positions": at_risk,
            "message": "⚠️ gepo assignment risk: " + "; ".join(
                _summarise(r) for r in at_risk),
        }
        out = ALERT_DIR / f"assignment_risk_{ts:%Y-%m-%d}.json"
        _atomic_write(out, payload)
        print(f"\n  {len(at_risk)} position(s) at risk → wrote {out}", flush=True)
    else:
        # ALWAYS write, even when clear. Writing only on risk left the morning's
        # file on disk after the risk cleared, so the webapp kept flashing rows
        # that were no longer at risk — a warning that does not switch off is a
        # warning you learn to ignore.
        ts = datetime.now()
        out = ALERT_DIR / f"assignment_risk_{ts:%Y-%m-%d}.json"
        _atomic_write(out, {
            "type": "assignment-risk",
            "ts": ts.isoformat(timespec="seconds"),
            "host": os.uname().nodename,
            "n_at_risk": 0,
            "positions": [],
            "message": "no assignment risk",
        })
        print(f"\n  no assignment risk across {len(rows)} open position(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
