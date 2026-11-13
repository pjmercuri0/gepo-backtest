"""TENTATIVE next-session point for the IBKR tab, before that day's statement arrives (user
2026-11-06: 'engineer our ibkr update without the daily csv ... I can upload the true csv
tomorrow for the final update').

Takes the payload build_ibkr_payload.py wrote from the statements on hand, and rolls every
spread still OPEN at the last statement forward one session:
  * closed on the site that day  -> valued at the mark the site locked at the click (width - mark
                                    is the debit received), a close commission estimated at the
                                    last five sessions' average per contract
  * expiring that day            -> intrinsic at the site's expiry close (settle)
NAV = last NAV + sum(value change, USD) x the last statement's USD/CAD rate, no commissions
(user 2026-11-06), or --nav from the IBKR app.
Everything it adds is labelled tentative in config.source and under payload["tentative"];
re-running build_ibkr_payload.py with the real statement replaces it entirely.

Usage: ibkr_tentative.py YYYY-MM-DD [--site debit] [--nav <NLV in CAD from the IBKR app>]
"""
import csv, json, math, sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STMT_DIR = ROOT / "data" / "ibkr_statements"
SITE = sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else "credit"
OUT = ROOT / "live" / "data" / ("ibkr_actual_equity_debit.json" if SITE == "debit" else "ibkr_actual_equity.json")
DAY = next(a for a in sys.argv[1:] if a[:2] == "20")
# --nav <CAD>: the Net Liquidation Value he reads off the IBKR app after the close. It beats the
# roll-forward (his fills are not the site's locked marks: 2026-11-06 the app said 7,032 against a
# rolled 6,853) and becomes the point; the per-spread estimates stay as they are.
NAV_OVERRIDE = float(sys.argv[sys.argv.index("--nav") + 1]) if "--nav" in sys.argv else None
# --worthless T1,T2: spreads on these tickers expiring DAY are valued at 0 (user 2026-11-13: 'assuming
# MDT expires worthless' before the close was known); beats the closes file for those names.
WORTHLESS = set(sys.argv[sys.argv.index("--worthless") + 1].split(",")) if "--worthless" in sys.argv else set()


def _leg(sym):
    u, exp, k, pc = sym.split()
    return u, datetime.strptime(exp, "%d%b%y").date().isoformat(), float(k), pc


def last_statement():
    files = sorted(STMT_DIR.glob("U*_????????.csv"))
    f = files[-1]
    day = datetime.strptime(f.stem[-8:], "%Y%m%d").date().isoformat()
    legs, fx, nav_end = {}, None, None
    comm_cad = n_contracts = 0.0
    recent = files[-5:]          # per-contract rate from the last five sessions: the all-time average
    for g in files:              # is twice the current rate (user 2026-11-06: 'no way commission was that much')
        for r in csv.reader(open(g, encoding="utf-8-sig")):
            if g in recent and r[:2] == ["Change in NAV", "Data"] and r[2] == "Commissions":
                comm_cad += float(r[3])
            elif g in recent and r[:3] == ["Trades", "Data", "Order"] and r[3] == "Equity and Index Options":
                n_contracts += abs(float(r[7]))
            if g != f:
                continue
            if r[:2] == ["Open Positions", "Data"] and r[3] == "Equity and Index Options":
                # Symbol, Quantity, Mult, Cost Price, Cost Basis, Close Price, Value, Unrealized P/L
                legs[r[5]] = {"qty": float(r[6]), "basis": float(r[9]), "close": float(r[10]), "value": float(r[11])}
            elif r[:2] == ["Base Currency Exchange Rate", "Data"] and r[2] == "USD":
                fx = float(r[3])
            elif r[:2] == ["Change in NAV", "Data"] and r[2] == "Ending Value":
                nav_end = float(r[3])
    return day, legs, fx, nav_end, (abs(comm_cad) / n_contracts if n_contracts else 0.0)


def site_actions(day):
    """Per (ticker, expiry, k_s, k_l): the site's close mark that day, or its expiry close."""
    out = {}
    for fn in ("actuals_debit.json", "actuals.json"):
        p = ROOT / "live" / fn
        if not p.exists():
            continue
        for t in json.load(open(p)).get("trades", []):
            pk = t.get("pick") or {}
            try:
                key = (pk["ticker"], str(pk["expiry_date"])[:10], float(pk["short_strike"]), float(pk["long_strike"]))
            except (KeyError, TypeError, ValueError):
                continue
            cl = t.get("closed") or {}
            if str(cl.get("at", ""))[:10] == day and cl.get("mark") is not None:
                out[key] = {"closed_mark": float(cl["mark"]), "at": cl["at"]}
            elif pk.get("expiry_close") is not None and key[1] == day:
                out.setdefault(key, {})["expiry_close"] = float(pk["expiry_close"])
    return out


def _outcome(t, pc, sp, pnl):
    """The underlying's close against the strikes. A credit spread wins with spot on the safe side of
    the short; its debit twin wins once spot is THROUGH the short (both legs in the money)."""
    if sp is None:
        return "WIN" if pnl > 0 else "LOSS"
    bp = pc == "P"
    if t.get("structure") == "debit":
        if (sp <= t["k_s"]) if bp else (sp >= t["k_s"]):
            return "WIN"
        if (sp >= t["k_l"]) if bp else (sp <= t["k_l"]):
            return "LOSS"
        return "PARTIAL"
    if (sp >= t["k_s"]) if bp else (sp <= t["k_s"]):
        return "WIN"
    if (sp <= t["k_l"]) if bp else (sp >= t["k_l"]):
        return "LOSS"
    return "PARTIAL"


def _sharpe(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs); sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return round(m / sd * math.sqrt(252), 2) if sd > 0 else 0.0


def _max_dd(vals):
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v); dd = min(dd, v / peak - 1)
    return round(100 * dd, 2)


def main():
    pay = json.load(open(OUT))
    stmt_day, legs, fx, nav_end, comm_per = last_statement()
    if pay["summary"]["window_end"] != stmt_day:
        sys.exit(f"payload ends {pay['summary']['window_end']}, statements end {stmt_day}: run build_ibkr_payload.py first")
    if DAY <= stmt_day:
        sys.exit(f"{DAY} is already covered by a statement")
    acts = site_actions(DAY)
    from live.closes import load_closes
    cl = load_closes(); cl = cl[cl.date.astype(str) == DAY]
    closes = {t: float(v) for t, v in zip(cl.ticker, cl.close)}

    # Group the last statement's open legs into spreads the way the payload's trades are keyed
    by_key = defaultdict(list)
    for sym, L in legs.items():
        u, exp, k, pc = _leg(sym)
        by_key[(u, exp)].append((k, pc, L))

    mtm_usd = comm_cad = 0.0
    rolled, unresolved = [], []
    for t in pay["trades"]:
        if t["outcome"] != "OPEN":
            continue
        pair = by_key.get((t["ticker"], t["expiry"]), [])
        # Same right as the trade: a debit bull call (bull_put twin) is calls, a debit bear put is puts,
        # the credit spreads the other way round. JNJ 2026-11-13 held 282.5P/280P and 282.5C/285C
        # together, and the shared 282.5 strike matched three legs ('legs not found').
        _pc = (("C" if t["type"] == "bull_put" else "P") if t["structure"] == "debit"
               else ("P" if t["type"] == "bull_put" else "C"))
        mine = [x for x in pair if x[0] in (t["k_s"], t["k_l"]) and x[1] == _pc]
        if len(mine) != 2:
            unresolved.append(f"{t['ticker']} {t['k_s']}/{t['k_l']}: legs not found in the statement"); continue
        q = t["qty"]; width = abs(t["k_s"] - t["k_l"]); pc = mine[0][1]
        # Two 1-lot trades on one strike pair (BA, WMT, CSX) share the statement's 2-lot legs: each
        # takes its own share of the value and the cost basis.
        share = q / max(abs(mine[0][2]["qty"]), 1)
        v5 = sum(L["value"] for _, _, L in mine) * share
        basis = sum(L["basis"] for _, _, L in mine) * share
        key = (t["ticker"], t["expiry"], float(t["k_s"]), float(t["k_l"]))
        a = acts.get(key) or {}
        sp = closes.get(t["ticker"])
        if "closed_mark" in a:
            # The site locks the credit-scale mark; the debit received is width - mark.
            debit_recv = width - a["closed_mark"] if t["structure"] == "debit" else a["closed_mark"]
            v6 = (debit_recv if t["structure"] == "debit" else -debit_recv) * 100 * q
            cc = 0.0                     # no commission in the tentative (user 2026-11-06); the statement has the real one
            how = f"closed on site {a['at'][11:16]} at mark {a['closed_mark']:.2f}"
            t["close_debit"] = round(-debit_recv if t["structure"] == "debit" else debit_recv, 4)
        elif t["expiry"] == DAY and t["ticker"] in WORTHLESS:
            v6, cc, sp = 0.0, 0.0, None
            how = "expires worthless (his call, close not yet known)"
            t["close_debit"] = 0.0
        elif t["expiry"] == DAY:
            spx = a.get("expiry_close", sp)
            if spx is None:
                unresolved.append(f"{t['ticker']} {t['k_s']}/{t['k_l']}: no expiry close"); continue
            intr = {k: (max(k - spx, 0.0) if pc == "P" else max(spx - k, 0.0)) for k, _, _ in mine}
            # long leg +, short leg - in the statement's own quantities
            v6 = sum((1 if L["qty"] > 0 else -1) * intr[k] * 100 * q for k, _, L in mine)
            cc = 0.0
            how = f"expired at {spx:.2f}"
            t["close_debit"] = round(-v6 / 100 / q, 4) if t["structure"] == "debit" else round(v6 / 100 / q, 4)
            sp = spx
        else:
            unresolved.append(f"{t['ticker']} {t['k_s']}/{t['k_l']}: still open, no site action"); continue
        mtm_usd += v6 - v5
        comm_cad += cc
        # Entry at the fill price (the payload's credit is from IBKR's proceeds, commission-free), not
        # IBKR's cost basis, which has the opening commission in it: at the same locked marks the
        # tentative row then equals the Actuals row (user 2026-11-06: 'even at the locked marks you
        # should match'). The statement's cost basis would read 2-5 USD lower per spread.
        if t.get("credit") is not None:
            basis = ((width - t["credit"]) if t["structure"] == "debit" else -t["credit"]) * 100 * q
        pnl = v6 - basis - cc / fx
        outcome = _outcome(t, pc, sp, pnl)
        t.update({"pnl": round(pnl, 2), "outcome": outcome, "closed": DAY, "spot": sp, "tentative": how})
        rolled.append((t["ticker"], t["k_s"], t["k_l"], round(v5, 2), round(v6, 2), round(pnl, 2), outcome, how))

    nav6_est = nav_end + mtm_usd * fx - comm_cad
    nav6 = NAV_OVERRIDE if NAV_OVERRIDE is not None else nav6_est
    # Points
    pts = pay["points"]
    spy_prev = pts[-1]["spy"]
    from live.closes import load_closes as _lc
    c = _lc(); c = c[c.ticker == "SPY"].sort_values("date")
    spy_px = {str(d)[:10]: float(v) for d, v in zip(c.date, c.close)}
    spy6 = round(spy_prev * spy_px[DAY] / spy_px[stmt_day], 2) if (DAY in spy_px and stmt_day in spy_px and spy_prev) else None
    pts.append({"date": DAY, "strategy": round(nav6, 2), "spy": spy6, "tentative": True})

    # Weeks: rebuild the week this day falls in
    def wk(d):
        x = date.fromisoformat(d); return (x - timedelta(days=x.weekday())).isoformat()
    w = wk(DAY)
    tr = [t for t in pay["trades"] if wk(t["closed"] or DAY) == w]
    tr.sort(key=lambda t: (t["entry"], t["ticker"]))
    pre = next((x["pre_bank"] for x in pay["weeks"] if x["start"] and wk(x["start"]) == w), nav_end)
    week = {
        "label": "Week of " + date.fromisoformat(w).strftime("%b %-d, %Y"), "start": min([DAY] + [x["start"] for x in pay["weeks"] if wk(x["start"]) == w]),
        "n_trades": len(tr), "n_bull_put": sum(t["type"] == "bull_put" for t in tr),
        "n_bear_call": sum(t["type"] == "bear_call" for t in tr),
        "direction": sum(1 if t["type"] == "bull_put" else -1 for t in tr),
        "pnl": round(nav6 - pre, 2), "trade_pnl": round(sum(t["pnl"] for t in tr), 2),
        "credit": round(sum((t["credit"] or 0) * t["qty"] for t in tr) * 100, 2),
        "risk": round(sum((t["max_loss"] or 0) * t["qty"] for t in tr) * 100, 2),
        "pre_bank": round(pre, 2), "post_bank": round(nav6, 2), "trades": tr, "tentative": True,
    }
    pay["weeks"] = [x for x in pay["weeks"] if wk(x["start"]) != w] + [week]

    # Summary, same formulas as the builder
    navs = [p["strategy"] for p in pts]; days = [p["date"] for p in pts[1:]]
    start_nav = navs[0]
    daily = [b - a for a, b in zip(navs, navs[1:])]
    spy_v = [p["spy"] for p in pts if p["spy"] is not None]
    trades = pay["trades"]
    closed = [t for t in trades if t["outcome"] != "OPEN" and t["max_loss"] is not None]
    wag = sum(t["max_loss"] * t["qty"] * 100 for t in closed)
    s = pay["summary"]
    s.update({
        "window_end": DAY, "trading_days": len(days),
        "n_trades": len(trades), "n_open": sum(t["outcome"] == "OPEN" for t in trades),
        "strategy_final": round(navs[-1], 2),
        "strategy_total_return": round(100 * (navs[-1] / start_nav - 1), 2),
        "strategy_pnl": round(navs[-1] - start_nav, 2),
        "strategy_sharpe_dollar": _sharpe(daily), "strategy_max_dd": _max_dd(navs),
        "strategy_yield": round(100 * sum(t["pnl"] for t in closed) / wag, 2) if wag else None,
        "best_day": list(max(zip(daily, days))), "worst_day": list(min(zip(daily, days))),
        "up_days": sum(x > 0 for x in daily), "down_days": sum(x < 0 for x in daily),
        "spy_final": spy_v[-1] if spy_v else None,
        "spy_total_return": round(100 * (spy_v[-1] / spy_v[0] - 1), 2) if spy_v else None,
        "spy_sharpe_dollar": _sharpe([b - a for a, b in zip(spy_v, spy_v[1:])]),
        "spy_max_dd": _max_dd(spy_v) if spy_v else None,
        "trade_pnl_usd": round(sum(t["pnl"] for t in trades), 2),
    })
    pay["config"]["source"] = (pay["config"]["source"].split(" + ")[0]
                               + f" + {DAY} TENTATIVE ("
                               + ("NAV from the IBKR app, spreads at site closes" if NAV_OVERRIDE is not None
                                  else "site closes and expiry closes") + ", no statement yet)")
    pay["tentative"] = {
        "date": DAY, "from_nav": round(nav_end, 2), "mtm_usd": round(mtm_usd, 2), "usdcad": fx,
        "commissions_cad": round(comm_cad, 2), "comm_per_contract_cad_recent": round(comm_per, 4),
        "nav": round(nav6, 2), "nav_rolled": round(nav6_est, 2), "nav_from_app": NAV_OVERRIDE,
        "n_rolled": len(rolled), "unresolved": unresolved,
        "basis": "open spreads at the last statement rolled to the site's close marks / expiry closes; "
                 "last statement's USD/CAD; no commissions (the statement carries them)",
    }
    json.dump(pay, open(OUT, "w"), indent=1)
    print(f"{OUT.name}: {stmt_day} NAV {nav_end:.2f} -> {DAY} TENTATIVE {nav6:.2f} "
          f"(rolled {nav6_est:.2f}: MTM {mtm_usd:+.2f} USD x {fx} = {mtm_usd * fx:+.2f} CAD, comm -{comm_cad:.2f} CAD"
          + (f"; app NLV {NAV_OVERRIDE:.2f} used" if NAV_OVERRIDE is not None else "") + ")")
    for r in rolled:
        print("  %-5s %7.1f/%-7.1f v5 %8.2f -> v6 %8.2f  pnl %8.2f  %-7s %s" % r)
    for u in unresolved:
        print("  UNRESOLVED", u)


if __name__ == "__main__":
    main()
