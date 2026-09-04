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


def _bid(t) -> float | None:
    """Raw bid — what the holder actually RECEIVES for selling the option."""
    b = getattr(t, "bid", None)
    return float(b) if b is not None and b == b and b > 0 else None


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
        short_bid = _bid(t_short)        # what the holder gets for SELLING

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
        # A holder of the long side has THREE choices, and exercise can beat
        # either alternative. The old code only tested one of them.
        #
        #   HOLD     -> keeps intrinsic + extrinsic
        #   SELL     -> receives the BID
        #   EXERCISE -> receives intrinsic, plus dividend/carry going forward
        #
        # CHANNEL 1 — exercise beats HOLDING. Dividend (calls) or carry (puts)
        # exceeds the extrinsic given up. This is the classic textbook test.
        benefit, basis = _exercise_benefit(p)
        if (extrinsic is not None and itm_by is not None and itm_by > 0
                and benefit is not None and benefit > extrinsic):
            reasons.append(
                f"early exercise pays: {basis} {benefit:.2f} > extrinsic {extrinsic:.2f}")

        # CHANNEL 2 — REMOVED 2026-09-03. It tested "bid < intrinsic", which
        # algebraically IS "extrinsic < half the bid-ask spread":
        #     intrinsic > bid  <=>  mid - extrinsic > mid - spread/2
        #                      <=>  extrinsic < spread/2
        # (verified 6/6 against the live book on 2026-09-03). So it was the
        # extrinsic test with spread/2 silently substituted for a threshold —
        # the wider and less reliable the quote, the more eagerly it fired.
        # It flagged DE (threshold 3.45), PEP and SBUX that evening; none were
        # at risk. PEP's own mid (0.83) was BELOW its intrinsic (1.00) — an
        # impossible quote — and channel 2 fired on it. Meanwhile DIS, with
        # 0.08 of extrinsic and a tight 0.41 book, was missed entirely.
        #
        # The premise was wrong too: the holder does not sell at the bid, they
        # sell at the mid, so "exercise beats selling" is not established by a
        # sagging bid. What decides exercise is benefit > extrinsic, which is
        # channel 1. The fields below are still RECORDED for diagnostics; they
        # no longer raise an alert.
        exercise_over_sell = (itm_by - short_bid) if (
            short_bid is not None and itm_by is not None) else None
        below_parity = bool(exercise_over_sell is not None
                            and itm_by > 0 and exercise_over_sell > 0)

        # WATCH — extrinsic has collapsed on an ITM short leg. Not a rational
        # exercise signal on its own (that is channel 1), but holders do
        # exercise sub-optimally, and a near-zero extrinsic is the only
        # observable that precedes it: it makes exercise cheap, so a holder who
        # wants out of the position stops losing anything by exercising.
        # Deliberately a WATCH, not an alert, so it cannot drown out channel 1.
        watch_floor = float(getattr(live_config, "LIVE_ASSIGN_EXTRINSIC_ALERT", 0.10))
        # Recorded, never alerted. A collapsed extrinsic on an ITM short leg is
        # worth SEEING — it is the Ext column on the Actuals page — but it fired
        # on every spread that had run to max loss (DE 43 points through its long
        # strike, DIS, SBUX), which is information the P&L column already gives
        # you. An alert that lights up on positions you cannot act on is one you
        # learn to ignore, so this raises no tag and no row highlight.
        # The real flags stay: channel 1 (benefit > extrinsic) and the pin zone.
        extrinsic_watch = bool(extrinsic is not None and itm_by is not None
                               and itm_by > 0 and extrinsic < watch_floor)
        reasons_watch = None
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
            "short_bid": None if short_bid is None else round(short_bid, 4),
            "extrinsic": None if extrinsic is None else round(extrinsic, 4),
            "short_itm_by": None if itm_by is None else round(itm_by, 4),
            "below_parity": below_parity,
            "exercise_over_sell": (None if exercise_over_sell is None
                                   else round(exercise_over_sell, 4)),
            "in_pin_zone": bool(in_pin),
            "pin_live": bool(in_pin and is_expiry_day),
            # Tag ONLY on a real flag. extrinsic_watch is recorded for the Ext
            # column but never labels a row.
            "tag": ("PIN" if (in_pin and is_expiry_day) else
                    "EXERCISE" if reasons else ""),
            "pin_zone": [lo, hi],
            "exercise_benefit": None if benefit is None else round(benefit, 4),
            "benefit_basis": basis,
            "extrinsic_watch": extrinsic_watch,
            "at_risk": bool(reasons),
            "reasons": reasons + ([reasons_watch] if reasons_watch else []),
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
    if r.get("pin_live"):
        return f"{head} in pin zone {r['pin_zone'][0]:g}-{r['pin_zone'][1]:g}"
    if r.get("extrinsic_watch"):
        return (f"{head} WATCH extrinsic {r['extrinsic']} with "
                f"{r.get('short_itm_by')} ITM")
    return (f"{head} {r.get('benefit_basis')} {r.get('exercise_benefit')} "
            f"> extrinsic {r['extrinsic']}")


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
    # WATCH rows are not alerts, but they must still reach the payload or the
    # webapp cannot render the tag — the page shows only what this file writes.
    watching = [r for r in rows if r.get("extrinsic_watch") and not r["at_risk"]]
    for r in rows:
        if args.quiet and not r["at_risk"] and not r.get("extrinsic_watch"):
            continue
        ext = "  n/a" if r["extrinsic"] is None else f"{r['extrinsic']:5.2f}"
        itm = "  n/a" if r["short_itm_by"] is None else f"{r['short_itm_by']:+6.2f}"
        flag = ("  <<< AT RISK" if r["at_risk"]
                else "  <<< watch" if r.get("extrinsic_watch") else "")
        print(f"  {r['ticker']:<6}{r['spread_type']:<11}"
              f"K={r['short_strike']:<9.2f} spot={r['spot']}  "
              f"short ITM {itm}  extrinsic {ext}{flag}", flush=True)
        for why in r["reasons"]:
            print(f"      ! {why}", flush=True)

    # --- state file: what the WEBAPP reads ------------------------------------
    # One filename per day, rewritten in place every scan, and deliberately
    # CARRYING NO "message" KEY. Mya's notify_watcher.sh de-dups on FILENAME
    # (grep -qxF "$fname" .processed), so a same-named file notifies at most
    # once per day — and since this one is rewritten all day, that one shot
    # would be whatever the 09:30 scan happened to say, almost always "no
    # assignment risk". The watcher skips a messageless file without sending,
    # which is exactly what a state file should do.
    ts = datetime.now()
    state = {
        "type": "assignment-risk",
        "ts": ts.isoformat(timespec="seconds"),
        "host": os.uname().nodename,
        "n_at_risk": len(at_risk),
        "n_watch": len(watching),
        "extrinsic_threshold": float(
            getattr(live_config, "LIVE_ASSIGN_EXTRINSIC_ALERT", 0.10)),
        # EVERY assessed row, not just the flagged ones: the Actuals page shows
        # extrinsic per position, and a column that only populates after a
        # threshold trips is no use as an early warning. Consumers must gate on
        # at_risk / extrinsic_watch, never on mere presence.
        "positions": rows,
    }
    out = ALERT_DIR / f"assignment_risk_{ts:%Y-%m-%d}.json"
    _atomic_write(out, state)

    # --- alert file: what TELEGRAM reads --------------------------------------
    # Uniquely named per firing so Mya's filename de-dup cannot suppress it.
    #
    # Fire ONCE PER POSITION PER WEEK, not once per scan and not on every change
    # to the at-risk set. A position that stays flagged all week is the normal
    # case; re-sending it every 30 minutes is how an alert becomes wallpaper.
    # The ledger is keyed by the position's own expiry, so it prunes itself as
    # each week rolls off and a position that reappears next week alerts again.
    ledger_path = ALERT_DIR / ".assignment_alerted.json"
    try:
        ledger = json.loads(ledger_path.read_text())
        if not isinstance(ledger, dict):
            ledger = {}
    except (OSError, json.JSONDecodeError):
        ledger = {}
    today_iso = date.today().isoformat()
    # Drop anything whose expiry has passed — that is the week rolling over.
    ledger = {k: v for k, v in ledger.items() if str(v) >= today_iso}

    def _key(r):
        return f"{r['ticker']}|{r['spread_type']}|{r['short_strike']:g}|{r['expiry']}"

    fresh = [r for r in at_risk if _key(r) not in ledger]

    if fresh:
        alert = dict(state)
        alert["type"] = "assignment-alert"
        alert["message"] = "⚠️ gepo assignment risk: " + "; ".join(
            _summarise(r) for r in fresh)
        # Only the newly-flagged rows travel in the alert; the full book is in
        # the state file and would bloat a Telegram payload for no benefit.
        alert["positions"] = fresh
        alert["n_at_risk"] = len(fresh)
        apath = ALERT_DIR / f"assignment_alert_{ts:%Y-%m-%d_%H%M%S}.json"
        n = 1
        while apath.exists():          # two firings inside one second
            apath = ALERT_DIR / f"assignment_alert_{ts:%Y-%m-%d_%H%M%S}_{n}.json"
            n += 1
        _atomic_write(apath, alert)
        for r in fresh:
            ledger[_key(r)] = r["expiry"]
        try:
            _atomic_write(ledger_path, ledger)
        except OSError:
            pass
        print(f"\n  ALERT ({len(fresh)} new) -> {apath.name}", flush=True)
    elif at_risk:
        print(f"\n  {len(at_risk)} at risk, all already alerted this week "
              f"— no new Telegram file", flush=True)

    print(f"\n  {len(at_risk)} at risk, {len(watching)} watch "
          f"across {len(rows)} open position(s) -> {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
