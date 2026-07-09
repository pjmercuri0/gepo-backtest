"""Expiry-day close alert. Fires at 15:01 ET on expiry days (Fridays).

CANONICAL POLICY (2026-05-30): EVERY open pick is MUST_CLOSE — no exceptions.
User policy is "close everything on Friday before market close." The classifier
still computes the cushion and reason fields for diagnostics, but the status
is always MUST_CLOSE so a rec_debit is emitted for every position.

To re-enable SAFE_EXPIRE selectively, pass --safe-cushion <fraction>. Default
of 99 means no pick ever qualifies as safe (any realistic cushion is ≤100%).

rec_debit = min(1.25 × mid_debit, natural_debit) — caps the close cost.

Entry-credit basis used for P&L projection matches the rest of the app:
0.85 × LAST (preferred) else 0.80 × MID fallback.

Usage:
  python3 -m live.close_alert                       # close everything (canon)
  python3 -m live.close_alert --safe-cushion 0.005  # restore old 0.5% policy
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config

try:
    from ib_insync import IB, Option, Stock
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


# Default cushion below which the short leg is in the pin zone. 0.5% of spot
# is roughly $1 on a $200 stock — close to the OCC discretionary-assignment
# uncertainty band on Friday afternoon.
# 99 = effectively infinite; no cushion clears it → every pick is MUST_CLOSE.
# Drop to 0.005 (0.5%) or similar to re-enable selective SAFE_EXPIRE.
DEFAULT_SAFE_CUSHION_PCT = 99.0


def _right_for(spread_type: str) -> str:
    return "P" if spread_type == "bull_put" else "C"


def _fetch_spot(ib: IB, sym: str) -> float | None:
    """Last trade price for the underlying. Falls back to mid if no trade."""
    try:
        stk = Stock(sym, "SMART", "USD")
        qual = ib.qualifyContracts(stk)
        if not qual or not qual[0].conId:
            return None
        tk = ib.reqTickers(stk)[0]
        for cand in (tk.last, tk.close, tk.marketPrice()):
            if cand and cand > 0:
                return float(cand)
        return None
    except Exception as e:
        print(f"    [{sym}] spot fetch failed: {e}", flush=True)
        return None


def _classify(pick: dict, spot: float, safe_cushion_pct: float) -> dict:
    """SAFE_EXPIRE vs MUST_CLOSE based on spot vs short_strike cushion."""
    ss = float(pick["short_strike"])
    if pick["spread_type"] == "bull_put":
        cushion = (spot - ss) / spot
        side = "above"
    else:  # bear_call
        cushion = (ss - spot) / spot
        side = "below"
    if cushion < safe_cushion_pct:
        if cushion <= 0:
            status = "MUST_CLOSE"
            reason = f"short leg ITM (spot ${spot:.2f} {'<' if side=='above' else '>'} strike ${ss:.2f})"
        else:
            status = "MUST_CLOSE"
            reason = f"pin zone ({cushion*100:.2f}% cushion, need ≥{safe_cushion_pct*100:.1f}%)"
    else:
        status = "SAFE_EXPIRE"
        reason = f"{cushion*100:.2f}% cushion {side} short strike — let expire"
    return {
        "status":         status,
        "reason":         reason,
        "spot":           round(spot, 2),
        "cushion_pct":    round(cushion * 100, 3),
    }


def _atomic_write(path: Path, payload: dict) -> None:
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


def _fetch_close_prices(ib: IB, pick: dict) -> dict | None:
    """Fetch current short+long bid/ask for the pick. Returns recommended
    close debit + components, or None if BBO unavailable."""
    sym    = pick["ticker"]
    expiry = pick["expiry_date"][:10].replace("-", "")
    right  = _right_for(pick["spread_type"])

    short = Option(sym, expiry, float(pick["short_strike"]), right,
                   "SMART", currency="USD", multiplier="100")
    long_ = Option(sym, expiry, float(pick["long_strike"]), right,
                   "SMART", currency="USD", multiplier="100")

    try:
        qual = ib.qualifyContracts(short, long_)
        if len(qual) != 2 or not all(c.conId for c in qual):
            return None
        s_tk, l_tk = ib.reqTickers(short, long_)
        s_bid = float(s_tk.bid) if s_tk.bid and s_tk.bid > 0 else None
        s_ask = float(s_tk.ask) if s_tk.ask and s_tk.ask > 0 else None
        l_bid = float(l_tk.bid) if l_tk.bid and l_tk.bid > 0 else None
        l_ask = float(l_tk.ask) if l_tk.ask and l_tk.ask > 0 else None
        if None in (s_bid, s_ask, l_bid, l_ask):
            return None
    except Exception as e:
        print(f"    [{sym}] option fetch failed: {e}", flush=True)
        return None

    short_mid     = (s_bid + s_ask) / 2.0
    long_mid      = (l_bid + l_ask) / 2.0
    mid_debit     = short_mid - long_mid              # best fill, often won't fill
    natural_debit = s_ask - l_bid                     # immediate fill, worst price
    # 1.25 × mid mirrors the 75% × mid entry rule (pay 25% above mid to
    # close, sell 25% below mid to open). Cap at natural so we don't
    # overpay vs the immediate-fill price for tight bid-ask spreads.
    rec_debit     = min(1.25 * mid_debit, natural_debit)

    # Entry-credit basis when no actual_credit recorded: 0.85×LAST else
    # 0.80×MID (matches webapp._enrich_pick + track_frozen + expire_frozen).
    if pick.get("actual_credit") is not None:
        actual_credit = float(pick["actual_credit"])
    else:
        nc = float(pick.get("net_credit") or 0)
        ml = float(pick.get("max_loss") or 0)
        spread_w = nc + ml
        sl = pick.get("short_last"); ll = pick.get("long_last")
        if sl and ll and sl > 0 and ll > 0:
            # Clamp LAST to [BID, ASK] per leg + cap at spread_width.
            psb = pick.get("short_bid"); psa = pick.get("short_ask")
            plb = pick.get("long_bid");  pla = pick.get("long_ask")
            sl_eff = float(sl); ll_eff = float(ll)
            if psb is not None and psa is not None:
                sl_eff = max(float(psb), min(sl_eff, float(psa)))
            if plb is not None and pla is not None:
                ll_eff = max(float(plb), min(ll_eff, float(pla)))
            raw = max(sl_eff - ll_eff, 0.0)
            actual_credit = min(raw, spread_w) * 0.85
        else:
            actual_credit = nc * 0.80
        actual_credit = min(actual_credit, spread_w)
    pnl_now = round((float(actual_credit) - rec_debit) * 100, 2)

    return {
        "short_bid":   round(s_bid, 4),
        "short_ask":   round(s_ask, 4),
        "long_bid":    round(l_bid, 4),
        "long_ask":    round(l_ask, 4),
        "mid_debit":     round(mid_debit, 4),
        "natural_debit": round(natural_debit, 4),
        "rec_debit":     round(rec_debit, 4),
        "pnl_at_rec":  pnl_now,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", type=int, default=270,
                        help="IB Gateway clientId (default 270)")
    parser.add_argument("--safe-cushion", type=float, default=DEFAULT_SAFE_CUSHION_PCT,
                        help=f"Cushion fraction below which a pick is MUST_CLOSE "
                             f"(default {DEFAULT_SAFE_CUSHION_PCT} = 0.5%%)")
    args = parser.parse_args()

    today = date.today()
    frozen_dir = Path(live_config.FROZEN_DIR)
    expiring: list[tuple[Path, dict]] = []
    for fp in sorted(frozen_dir.glob("*.json")):
        try:
            with open(fp) as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if payload.get("outcome"):
            continue
        picks = payload.get("top_picks") or []
        if not picks:
            continue
        exp_str = picks[0].get("expiry_date", "")[:10]
        try:
            if datetime.fromisoformat(exp_str).date() == today:
                expiring.append((fp, payload))
        except (ValueError, TypeError):
            continue

    if not expiring:
        print("No expiring frozen files today — no close alert needed.", flush=True)
        return 0

    print(f"Generating close alerts for {len(expiring)} expiring frozen file(s)...", flush=True)

    ib = IB()
    try:
        ib.connect(live_config.IB_HOST, live_config.IB_PORT, clientId=args.client_id)
    except Exception as e:
        print(f"  ✗ IB connect failed: {e}", flush=True)
        return 1
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

    all_alerts = []
    total_must_close = total_safe = 0
    try:
        for fp, payload in expiring:
            picks = payload.get("top_picks") or []
            print(f"  {fp.name}: {len(picks)} picks", flush=True)
            day_alerts = []
            for pick in picks:
                tk = pick["ticker"]
                spot = _fetch_spot(ib, tk)
                if spot is None:
                    print(f"    [{tk}] no spot — skipping (cannot classify)", flush=True)
                    continue
                cls = _classify(pick, spot, args.safe_cushion)
                alert = {
                    "ticker":        tk,
                    "spread_type":   pick["spread_type"],
                    "short_strike":  pick["short_strike"],
                    "long_strike":   pick["long_strike"],
                    "actual_credit": pick.get("actual_credit"),
                    **cls,
                }
                if cls["status"] == "MUST_CLOSE":
                    total_must_close += 1
                    q = _fetch_close_prices(ib, pick)
                    if q is None:
                        print(f"    [{tk}] MUST_CLOSE but no BBO — flag manually",
                              flush=True)
                        alert["bbo_missing"] = True
                    else:
                        alert.update(q)
                        print(f"    [{tk}] ⚠ MUST_CLOSE — {cls['reason']}  "
                              f"close at \${q['rec_debit']:.2f} → \${q['pnl_at_rec']:+.2f}/ctr",
                              flush=True)
                else:
                    total_safe += 1
                    print(f"    [{tk}] ✓ SAFE_EXPIRE — {cls['reason']}", flush=True)
                day_alerts.append(alert)
            all_alerts.append({
                "date":   payload.get("data_date", fp.stem),
                "picks":  day_alerts,
            })
    finally:
        ib.disconnect()

    print(f"\nSummary: {total_must_close} MUST_CLOSE, {total_safe} SAFE_EXPIRE",
          flush=True)

    out_path = Path(live_config.NOTIFICATIONS_DIR) / f"close_alert_{today.isoformat()}.json"
    payload = {
        "type":             "close_alert",
        "generated_at":     datetime.now().isoformat(timespec="seconds"),
        "expiry_date":      today.isoformat(),
        "safe_cushion_pct": args.safe_cushion,
        "summary":          {"must_close": total_must_close, "safe_expire": total_safe},
        "instructions": (
            "MUST_CLOSE: place buy-to-close at rec_debit on CIBC "
            "(rec_debit = 1.25 × mid, capped at natural). If unfilled after 2 min, "
            "raise limit toward natural_debit. Market order as last resort. "
            "SAFE_EXPIRE: let them expire — both legs OTM by ≥cushion. "
            "Re-run this script later in the afternoon if spot drifts toward a short strike."
        ),
        "days":         all_alerts,
    }
    _atomic_write(out_path, payload)
    print(f"\n✓ Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
