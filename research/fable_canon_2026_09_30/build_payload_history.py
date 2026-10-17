"""Site payload for the PLOT tab: the History tab's own settled picks (user 2026-10-17: "make plot match").

Every settled History row from SINCE on, exactly as /history renders it (webapp._frozen_history): the same
picks, the same booked credit (his typed fill where he entered one, else fill_targets[0] = 1.04 x model)
and the same per-contract P&L (actual_pnl_per_contract when present, else pnl_per_contract). Open rows
and mock days are left out. Same builder as the Backtest/OOT tabs (report_mid_canon.build_payload +
report_bear_regime.patch_config). Writes live/data/ibkr_replay_equity.json.
Usage: build_payload_history.py [since=2026-08-24]
"""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
import config as cfg
from spreads import settle_pnl
from live import webapp
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-24"

rows, skipped = [], []
for day in webapp._frozen_history(limit=10_000):
    if day.get("mock") or not day.get("date") or day["date"] < SINCE:
        continue
    out = day.get("outcome") or {}
    rbp, res = out.get("results_by_pick"), out.get("results") or {}
    for i, p in enumerate(day.get("top_picks") or []):
        # Same row lookup as history.html: index-aligned results_by_pick, else by ticker.
        o = rbp[i] if rbp and len(rbp) > i else res.get(p.get("ticker"))
        if not o or o.get("pnl_per_contract") is None:
            skipped.append((day["date"], p.get("ticker"), "open" if not o else o.get("error", "no pnl")))
            continue
        pnl = o["actual_pnl_per_contract"] if o.get("actual_pnl_per_contract") is not None else o["pnl_per_contract"]
        credit = p.get("actual_credit")
        if credit is None:
            credit = ((p.get("fill_targets") or [{}])[0]).get("credit")
        # Picks before 2026-09-14 carry no model_credit / D_ent: Kelly sizing falls back to the booked credit.
        w = abs(float(p["short_strike"]) - float(p["long_strike"]))
        rows.append(dict(day=day["date"], ticker=p["ticker"], spread_type=p["spread_type"],
                         short_strike=float(p["short_strike"]), long_strike=float(p["long_strike"]), width=w,
                         exp=str(p["expiry_date"])[:10], DTE=p.get("DTE"), entry_price=p.get("entry_price"),
                         model_credit=float(p.get("model_credit") or credit), credit=float(credit), p=p.get("p"), q=p.get("q"),
                         ro=p.get("ro"), D_ent=p.get("D_ent"), GROUND=p.get("GROUND"),
                         settle=float(o["underlying_price"]), result=o.get("result"), pnl_per_contract=float(pnl)))
P = pd.DataFrame(rows)
# History settled each day under the booking rule in force at the time (0.80 x mid before October, the
# typed fill, 1.04 x model now), and its P&L cell shows that recorded result. Book each row at the credit
# that reproduces the recorded P&L, so the curve, risk and yield all sit on History's numbers.
def _implied(s, ks, kl, w, t, target):
    lo, hi = 0.0, w
    for _ in range(60):
        mid = (lo + hi) / 2
        if settle_pnl(s, ks, kl, mid, w - mid, t) * 100 < target: lo = mid
        else: hi = mid
    return round((lo + hi) / 2, 4)
P["credit"] = [_implied(s, ks, kl, w, t, v) for s, ks, kl, w, t, v in
               zip(P.settle, P.short_strike, P.long_strike, P.width, P.spread_type, P.pnl_per_contract)]
# The credit must reproduce the P&L History shows; a row that does not is reported, never silently kept.
chk = np.array([settle_pnl(s, ks, kl, c, w - c, t) * 100 for s, ks, kl, c, w, t in
                zip(P.settle, P.short_strike, P.long_strike, P.credit, P.width, P.spread_type)])
bad = P[np.abs(chk - P.pnl_per_contract) > 0.5]
if len(bad):
    print(f"WARNING {len(bad)} row(s) whose credit does not reproduce History's P&L:")
    print(bad[["day", "ticker", "short_strike", "long_strike", "credit", "settle", "pnl_per_contract"]].assign(recomputed=chk[bad.index]).to_string())
if skipped:
    print(f"left out {len(skipped)} unsettled row(s): " + ", ".join(f"{d} {t} ({why})" for d, t, why in skipped))

P["entry_date"] = pd.to_datetime(P.day); P["entry_date_dt"] = P.entry_date; P["expiry_date"] = pd.to_datetime(P.exp); P["realize_date"] = P.expiry_date
P["max_loss_adj"] = (P.width - P.credit).round(4); P["max_loss_dollar"] = P.max_loss_adj * 100
P["_outcome"] = P.result; P["expiry_close"] = P.settle
b = (P.model_credit / (P.width - P.model_credit)).values
P["w_star"], P["G"] = ec.kelly(P.p.values.astype(float), P.q.values.astype(float), P.ro.values.astype(float), b)
P["w_star_carry"] = ec.kelly_carry(P.p.values.astype(float), P.q.values.astype(float), P.ro.values.astype(float), b,
                                   (cfg.CARRY_RATE * np.clip(P.DTE.astype(float), 1, None) / 365.0).values)
P["DKL"] = P.D_ent

with contextlib.redirect_stdout(io.StringIO()):
    pay = rbr.patch_config(rmc.build_payload(P, 2026, "History tab picks"), True)
k = pay["config"]
k["universe"] = "SP100 + SPY/QQQ/IWM, IBKR live"
k["selection"] = "the History tab's settled picks: 15:15 freeze, topped up at 15:30 and 15:45"
k["fill_basis"] = "your fill where entered, else 1.04× smile-fit model credit; partial-WIN at 50% intrinsic; no commission"
k["days"] = f"every History day from {SINCE}"
(ROOT / "live/data/ibkr_replay_equity.json").write_text(json.dumps(pay, indent=2))
s = pay["summary"]
print(f"{s['n_trades']} trades, qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, DD {s['strategy_max_dd']}%, "
      f"qty1 ${s['qty1_final']:,.0f}, yield {s['strategy_yield']}%, window {s['window_start']}..{s['window_end']}, weeks {len(pay['weeks'])}")
