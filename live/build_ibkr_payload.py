"""Site payload for the IBKR tab: the account's real results from IBKR daily Activity Statements
(user 2026-10-23). Same shape as the Plot payload so the page reads like Plot.

  equity curve / stats / week P&L : the account's Net Asset Value, CAD (Change in NAV section)
  trades                          : every option spread, paired from the Trades section, USD;
                                    P&L = IBKR's Realized P/L on the closing fills (commissions
                                    included) plus the stock legs of an assignment; spreads still
                                    held at the last statement show the Open Positions unrealized P/L

Statements: data/ibkr_statements/U25707706_YYYYMMDD.csv (one Daily Activity Statement per session,
gitignored). Writes live/data/ibkr_actual_equity.json.  Usage: build_ibkr_payload.py
"""
import csv, json, math, sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STMT_DIR = ROOT / "data" / "ibkr_statements"
# --site debit (gepo-debit-ticker): only the parity debit spreads (calls with short > long, puts with
# short < long), written to ibkr_actual_equity_debit.json. Default: the credit spreads.
SITE = sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else "credit"
OUT = ROOT / "live" / "data" / ("ibkr_actual_equity_debit.json" if SITE == "debit" else "ibkr_actual_equity.json")
FIELDS = ["sec", "kind", "disc", "cat", "ccy", "sym", "dt", "qty", "tpx", "cpx", "proceeds", "comm",
          "basis", "rpl", "mtm", "code"]


def _codes(r):
    return set(r["code"].split(";"))


def read_statements():
    nav, fills, open_pos = {}, [], []
    files = sorted(STMT_DIR.glob("U*_????????.csv"))
    for f in files:
        day = datetime.strptime(f.stem[-8:], "%Y%m%d").date().isoformat()
        last = f == files[-1]
        for r in csv.reader(open(f, encoding="utf-8-sig")):
            if r[:2] == ["Change in NAV", "Data"] and r[2] in ("Starting Value", "Ending Value"):
                nav.setdefault(day, {})["start" if r[2] == "Starting Value" else "end"] = float(r[3])
            elif r[:3] == ["Trades", "Data", "Order"] and r[3] in ("Equity and Index Options", "Stocks"):
                fills.append(dict(zip(FIELDS, r), day=day))
            elif last and r[:2] == ["Open Positions", "Data"] and r[3] == "Equity and Index Options":
                # Symbol, Quantity, Mult, Cost Price, Cost Basis, Close Price, Value, Unrealized P/L
                open_pos.append({"sym": r[5], "qty": float(r[6]), "upl": float(r[12])})
    fills.sort(key=lambda r: r["dt"])
    return nav, fills, open_pos, (files[-1].stem[-8:] if files else None)


def _leg(sym):
    u, exp, k, pc = sym.split()
    return u, datetime.strptime(exp, "%d%b%y").date().isoformat(), float(k), pc


def pair_spreads(fills, open_pos):
    """Chronological: an opening fill pair starts a spread; each later fill on one of its legs closes it."""
    spreads, held = [], defaultdict(list)        # held: option symbol -> spreads holding it, oldest first
    opens = defaultdict(list)
    for r in fills:
        if r["cat"] == "Equity and Index Options" and "O" in _codes(r):
            u, exp, _, _ = _leg(r["sym"])
            opens[(r["dt"], u, exp, _leg(r["sym"])[3])].append(r)
    # Netting (2026-10-27): EOG 162.5/160 was opened while 165/162.5 from the day before was still
    # held, so IBKR booked the 162.5P sale as a CLOSE of that long leg (code C, same order as the
    # 160P open). It is the new spread's short leg: pair it there, and carry the 162.5P on both
    # spreads as a "phantom" leg (long on the old, short on the new) that settles at intrinsic
    # against the close, since no position and no later fill exists for it. The sale's rpl is
    # not booked: its proceeds are the new spread's credit.
    for r in fills:
        if r["cat"] == "Equity and Index Options" and "O" not in _codes(r):
            u, exp, _, _ = _leg(r["sym"])
            if opens.get((r["dt"], u, exp, _leg(r["sym"])[3])):
                opens[(r["dt"], u, exp, _leg(r["sym"])[3])].append(r)
                r["_net"] = True
    by_dt = defaultdict(list)
    for r in fills:
        by_dt[(r["dt"], r["sym"].split()[0])].append(r)
    done_open = set()
    for r in fills:
        if r["cat"] != "Equity and Index Options":
            continue
        u, exp, k, pc = _leg(r["sym"])
        if "O" in _codes(r) or r.get("_net"):
            key = (r["dt"], u, exp, pc)
            if key in done_open:
                continue
            done_open.add(key)
            legs = opens[key]
            sh = [x for x in legs if float(x["qty"]) < 0]; lg = [x for x in legs if float(x["qty"]) > 0]
            s = {"ticker": u, "expiry": exp, "pc": pc, "entry": r["day"], "entry_dt": r["dt"],
                 "k_s": _leg(sh[0]["sym"])[2] if sh else None, "k_l": _leg(lg[0]["sym"])[2] if lg else None,
                 "qty": int(abs(float(sh[0]["qty"]))) if sh else 1,
                 "open_proceeds": sum(float(x["proceeds"]) for x in legs),
                 "close_proceeds": 0.0, "rpl": 0.0, "closed_legs": 0, "assigned": False, "close_day": None,
                 "phantom": [], "leg_cash": {x["sym"]: float(x["proceeds"]) + float(x["comm"]) for x in legs}}
            spreads.append(s)
            for x in legs:
                if x.get("_net"):
                    # The leg the older spread held leaves its real legs; both spreads carry it as a
                    # phantom with its own cash: the old one what it paid, the new one what it received.
                    xk, xpc = _leg(x["sym"])[2], _leg(x["sym"])[3]
                    old = held[x["sym"]].pop(0) if held.get(x["sym"]) else None
                    if old is not None:
                        old.setdefault("phantom", []).append({"k": xk, "pc": xpc, "sign": 1 if float(x["qty"]) < 0 else -1,
                                                             "cash": old.get("leg_cash", {}).get(x["sym"], 0.0)})
                    s["phantom"].append({"k": xk, "pc": xpc, "sign": -1 if float(x["qty"]) < 0 else 1,
                                         "cash": float(x["proceeds"]) + float(x["comm"])})
                else:
                    held[x["sym"]].append(s)
            continue
        # A closing fill (sold-to-close, bought-to-close, expired, assigned, exercised). One fill can
        # close several spreads on the same leg (2026-10-23: XOM 180/177.5 opened Oct 19 and again
        # Oct 21, both bought back in one qty-2 order), so it is split across the spreads it covers,
        # oldest first, pro rata by contracts; the Oct 21 lot used to stay "open" at $0.
        rem = tot = abs(float(r["qty"]))
        while rem > 0:
            s = held[r["sym"]].pop(0) if held.get(r["sym"]) else None
            if s is None:                                  # opened before the first statement
                s = next((x for x in spreads if x.get("pre") and x["ticker"] == u and x["expiry"] == exp
                          and x["closed_legs"] < 2 and k not in (x["k_s"], x["k_l"])), None)
                if s is None:
                    s = {"ticker": u, "expiry": exp, "pc": pc, "entry": None, "pre": True, "k_s": None, "k_l": None,
                         "qty": int(rem), "open_proceeds": None, "close_proceeds": 0.0, "rpl": 0.0,
                         "closed_legs": 0, "assigned": False, "close_day": None}
                    spreads.append(s)
                # the leg bought back (qty > 0) was the short one
                if float(r["qty"]) > 0:
                    s["k_s"] = k
                else:
                    s["k_l"] = k
                take = rem
            else:
                take = min(s["qty"], rem)
            frac = take / tot
            s["close_proceeds"] += float(r["proceeds"]) * frac
            s["rpl"] += float(r["rpl"]) * frac
            s["closed_legs"] += 1
            s["close_day"] = max(s["close_day"] or r["day"], r["day"])
            if _codes(r) & {"A", "Ex"}:
                s["assigned"] = True
                # the stock fill booked at the same moment belongs to this spread
                for st in by_dt[(r["dt"], u)]:
                    if st["cat"] == "Stocks" and not st.get("_used"):
                        st["_used"] = True
                        s["rpl"] += float(st["rpl"])
            rem -= take
    # Still held at the last statement: unrealized P/L of its legs
    upl = {p["sym"]: p["upl"] for p in open_pos}
    for sym, ss in held.items():
        n = sum(x["qty"] for x in ss)
        for x in ss:                                    # a leg held by two spreads splits by contracts
            x["open_upl"] = x.get("open_upl", 0.0) + upl.get(sym, 0.0) * x["qty"] / n
    return spreads


def _actual_credit_lookup():
    """Fill credits typed on Actuals, for spreads opened before the first statement."""
    try:
        store = json.loads((ROOT / "live" / ("actuals_debit.json" if SITE == "debit" else "actuals.json")).read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for t in store.get("trades") or []:
        p = t.get("pick") or {}
        try:
            key = (p["ticker"], str(p["expiry_date"])[:10], float(p["short_strike"]), float(p["long_strike"]))
        except (KeyError, TypeError, ValueError):
            continue
        c = t.get("actual_credit") if t.get("actual_credit") is not None else p.get("actual_credit")
        if c is not None:
            out[key] = (float(c), str((t.get("source") or {}).get("date") or "")[:10] or None)
    return out


def _spy(dates):
    from live.closes import load_closes
    c = load_closes()
    c = c[c.ticker == "SPY"].set_index("date")["close"]
    return {d: float(c.loc[d]) for d in dates if d in c.index}


def _closes_on(pairs):
    from live.closes import load_closes
    c = load_closes()
    idx = {(t, d): v for t, d, v in zip(c.ticker, c.date.astype(str), c.close)}
    return {p: idx.get(p) for p in pairs}


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


def build():
    nav, fills, open_pos, last_stmt = read_statements()
    days = sorted(nav)
    if not days:
        sys.exit(f"no statements in {STMT_DIR}")
    # NAV chain: each day must start where the previous ended
    for a, b in zip(days, days[1:]):
        if abs(nav[a]["end"] - nav[b]["start"]) > 0.01:
            print(f"WARNING: NAV gap {a} end {nav[a]['end']:.2f} -> {b} start {nav[b]['start']:.2f}")
    d0 = (date.fromisoformat(days[0]) - timedelta(days=1)).isoformat()
    start_nav = nav[days[0]]["start"]

    spreads = pair_spreads(fills, open_pos)
    # Structure from the legs alone: a credit vertical sells the strike nearer the money (puts short >
    # long, calls short < long); its parity twin, the debit, is the reverse in the other right.
    for s_ in spreads:
        ks, kl = s_.get("k_s"), s_.get("k_l")
        s_["structure"] = ("debit" if (ks is not None and kl is not None and
                                       ((s_["pc"] == "C" and ks > kl) or (s_["pc"] == "P" and ks < kl))) else "credit")
    spreads = [s_ for s_ in spreads if s_["structure"] == SITE]
    credits = _actual_credit_lookup()
    spot_pairs = _closes_on([(s["ticker"], s["expiry"]) for s in spreads]
                            + [(s["ticker"], days[-1]) for s in spreads if s.get("phantom")])

    def _phantom(s, spot):
        """P&L of the netted leg(s): the cash paid or received for it, plus its intrinsic value
        against the close (+ for a long leg, - for a short one). Sums to IBKR's own P&L across the
        two spreads."""
        if not s.get("phantom"):
            return 0.0
        v = 0.0
        for ph in s["phantom"]:
            intr = 0.0 if spot is None else (max(ph["k"] - spot, 0.0) if ph["pc"] == "P" else max(spot - ph["k"], 0.0))
            v += ph["cash"] + ph["sign"] * intr * 100 * max(int(s["qty"] or 1), 1)
        return v
    trades = []
    for s in spreads:
        q = max(int(s["qty"] or 1), 1)
        width = abs((s["k_s"] or 0) - (s["k_l"] or 0))
        is_open = "open_upl" in s and s["closed_legs"] < 2
        credit = (s["open_proceeds"] / 100 / q) if s["open_proceeds"] is not None else None
        if credit is not None and s.get("structure") == "debit":
            credit = width + credit                  # debit paid -> credit equivalent (width - debit)
        entry = s["entry"]
        if credit is None:
            hit = credits.get((s["ticker"], s["expiry"], s["k_s"], s["k_l"]))
            if hit:
                credit, entry = hit
        debit = -s["close_proceeds"] / 100 / q
        # Result, Plot's rule: the underlying's close on expiry day against the strikes
        sp = spot_pairs.get((s["ticker"], s["expiry"])) if s["expiry"] <= days[-1] else None
        bp = s["pc"] == "P"
        if is_open:
            outcome, pnl = "OPEN", s["open_upl"] + s["rpl"] + _phantom(s, spot_pairs.get((s["ticker"], days[-1])))
        else:
            pnl = s["rpl"] + _phantom(s, sp)
            if sp is None:
                outcome = "WIN" if pnl > 0 else "LOSS"
            elif (sp >= s["k_s"]) if bp else (sp <= s["k_s"]):
                outcome = "WIN"
            elif (sp <= s["k_l"]) if bp else (sp >= s["k_l"]):
                outcome = "LOSS"
            else:
                outcome = "PARTIAL"
        e = entry or s["close_day"]
        trades.append({
            "entry": entry or "before " + days[0], "dow": date.fromisoformat(e).strftime("%a") if entry else "",
            "ticker": s["ticker"], "structure": s.get("structure", "credit"),
            # the bet, not the right: a bull call debit is the bull put's twin
            "type": ("bull_put" if s["pc"] == "P" else "bear_call") if s.get("structure") != "debit"
                    else ("bull_put" if s["pc"] == "C" else "bear_call"),
            "k_s": s["k_s"], "k_l": s["k_l"], "expiry": s["expiry"],
            "credit": round(credit, 4) if credit is not None else None,
            "max_loss": round(width - credit, 4) if credit is not None else None,
            "close_debit": None if is_open else round(debit, 4),
            "qty": q, "pnl": round(pnl, 2), "outcome": outcome, "assigned": s["assigned"],
            "spot": sp,
            "closed": None if is_open else s["close_day"],
        })

    # Points: one per session, NAV at the close (CAD); SPY scaled to the same start
    pts_dates = [d0] + days
    spy = _spy(pts_dates)
    navs = [start_nav] + [nav[d]["end"] for d in days]
    spy0 = spy.get(d0)
    points = [{"date": d, "strategy": round(v, 2),
               "spy": round(start_nav * spy[d] / spy0, 2) if spy0 and d in spy else None}
              for d, v in zip(pts_dates, navs)]

    # Weeks: Monday-start; P&L = NAV change across the week (CAD); trades listed by the day they closed
    def wk(d):
        x = date.fromisoformat(d); return (x - timedelta(days=x.weekday())).isoformat()
    weeks = []
    for w in sorted({wk(d) for d in days}):
        wd = [d for d in days if wk(d) == w]
        tr = [t for t in trades if wk(t["closed"] or days[-1]) == w]
        tr.sort(key=lambda t: (t["entry"], t["ticker"]))
        pre, post = nav[wd[0]]["start"], nav[wd[-1]]["end"]
        weeks.append({
            "label": "Week of " + date.fromisoformat(w).strftime("%b %-d, %Y"), "start": wd[0],
            "n_trades": len(tr), "n_bull_put": sum(t["type"] == "bull_put" for t in tr),
            "n_bear_call": sum(t["type"] == "bear_call" for t in tr),
            "direction": sum(1 if t["type"] == "bull_put" else -1 for t in tr),
            "pnl": round(post - pre, 2), "trade_pnl": round(sum(t["pnl"] for t in tr), 2),
            "credit": round(sum((t["credit"] or 0) * t["qty"] for t in tr) * 100, 2),
            "risk": round(sum((t["max_loss"] or 0) * t["qty"] for t in tr) * 100, 2),
            "pre_bank": round(pre, 2), "post_bank": round(post, 2), "trades": tr,
        })

    daily = [b - a for a, b in zip(navs, navs[1:])]
    spy_v = [p["spy"] for p in points if p["spy"] is not None]
    closed = [t for t in trades if t["outcome"] != "OPEN" and t["max_loss"] is not None]
    wag = sum(t["max_loss"] * t["qty"] * 100 for t in closed)
    summary = {
        "window_start": days[0], "window_end": days[-1], "trading_days": len(days),
        "n_trades": len(trades), "n_open": sum(t["outcome"] == "OPEN" for t in trades),
        "strategy_final": round(navs[-1], 2),
        "strategy_total_return": round(100 * (navs[-1] / start_nav - 1), 2),
        "strategy_pnl": round(navs[-1] - start_nav, 2),
        "strategy_sharpe_dollar": _sharpe(daily), "strategy_max_dd": _max_dd(navs),
        "strategy_yield": round(100 * sum(t["pnl"] for t in closed) / wag, 2) if wag else None,
        "best_day": max(zip(daily, days)), "worst_day": min(zip(daily, days)),
        "up_days": sum(x > 0 for x in daily), "down_days": sum(x < 0 for x in daily),
        "spy_final": spy_v[-1] if spy_v else None,
        "spy_total_return": round(100 * (spy_v[-1] / spy_v[0] - 1), 2) if spy_v else None,
        "spy_sharpe_dollar": _sharpe([b - a for a, b in zip(spy_v, spy_v[1:])]),
        "spy_max_dd": _max_dd(spy_v) if spy_v else None,
        "trade_pnl_usd": round(sum(t["pnl"] for t in trades), 2),
        "commission_cad": None,
    }
    config = {
        "account": "U25707706", "base_currency": "CAD", "starting_bankroll": round(start_nav, 2),
        "source": f"IBKR Daily Activity Statements, {days[0]} → {days[-1]} ({len(days)} sessions)",
        "equity_basis": "Net Asset Value at each close, CAD, after commissions, fees and interest",
        "trade_basis": "per spread, USD: IBKR Realized P/L on the closing fills incl. commissions; "
                       "assignments include the stock legs; open spreads at the last statement's unrealized P/L",
    }
    pay = {"config": config, "summary": summary, "points": points, "trades": trades, "weeks": weeks,
           "built_from": last_stmt}
    OUT.write_text(json.dumps(pay, indent=2))
    print(f"{len(days)} sessions {days[0]}..{days[-1]}, NAV {start_nav:,.2f} -> {navs[-1]:,.2f} "
          f"({summary['strategy_total_return']:+}%), {len(trades)} spreads ({summary['n_open']} open), "
          f"trade P&L ${summary['trade_pnl_usd']:,.2f} USD, weeks {len(weeks)}")
    return pay


if __name__ == "__main__":
    build()
