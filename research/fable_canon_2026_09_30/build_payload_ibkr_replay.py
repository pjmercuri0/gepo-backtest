"""Site payload for the PLOT tab: the IBKR fair replay under the current canon (mini).

Picks: every archived 15:30/15:45 IBKR snapshot re-ranked with the current canon
(ibkr_replay_candidates_1530_1545.csv), strategy C + quote gate: model credit >= 0.45 x width,
quoted mid >= model, pooled top 6 by GROUND at k=24, 15:30 scan then 15:45 top-up to 6.
Same builder as the Backtest/OOT tabs (report_mid_canon.build_payload + report_bear_regime.patch_config).
Writes live/data/ibkr_replay_equity.json.  Usage: build_payload_ibkr_replay.py [fill=1.00]
"""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from spreads import settle_pnl
HERE = Path(__file__).resolve().parent
FILL = float(sys.argv[1]) if len(sys.argv) > 1 else 1.00; K = 24.0; FLOOR = 0.45; TOP = 6

R = pd.read_csv(HERE / "ibkr_replay_candidates_1530_1545.csv", dtype={"hm": str})
R = R[R.settle.notna() & (R.model_credit > 0) & R.net_credit.notna() & R.p.notna()].copy()
R["slot"] = R.hm.map(lambda h: "15:30" if h in ("1530", "1531") else "15:45")
E = R[(R.model_cw >= FLOOR) & (R.quote_over_model >= 1.0)].copy()
E["r"] = E.groupby(["day", "hm"]).G24.rank(ascending=False, method="first"); T = E[E.r <= TOP]
key = ["day", "ticker", "short_strike", "long_strike", "exp"]; out = []
for day, g in T.groupby("day"):
    ga = g[g.slot == "15:30"]; gb = g[g.slot == "15:45"]; have = set(map(tuple, ga[key].values))
    add = gb[np.array([tuple(r) not in have for r in gb[key].values], dtype=bool)]
    out.append(pd.concat([ga, add.head(max(0, TOP - len(ga)))]))
P = pd.concat(out).reset_index(drop=True)
P["entry_date"] = pd.to_datetime(P.day); P["entry_date_dt"] = P.entry_date; P["expiry_date"] = pd.to_datetime(P.exp); P["realize_date"] = P.expiry_date
P["credit"] = (FILL * P.model_credit).clip(upper=P.width - 0.01).round(4)
P["max_loss_adj"] = (P.width - P.credit).round(4); P["max_loss_dollar"] = P.max_loss_adj * 100
P["pnl_per_contract"] = [settle_pnl(s, ks, kl, c, w - c, t) * 100 for s, ks, kl, c, w, t in
                         zip(P.settle, P.short_strike, P.long_strike, P.credit, P.width, P.spread_type)]
bp = P.spread_type.eq("bull_put")
win = np.where(bp, P.settle >= P.short_strike, P.settle <= P.short_strike); loss = np.where(bp, P.settle <= P.long_strike, P.settle >= P.long_strike)
P["_outcome"] = np.where(win, "WIN", np.where(loss, "LOSS", "PARTIAL")); P["expiry_close"] = P.settle
b = (P.model_credit / (P.width - P.model_credit)).values
P["w_star"], P["G"] = ec.kelly(P.p.values, P.q.values, P.ro.values, b)
import config as cfg
P["w_star_carry"] = ec.kelly_carry(P.p.values, P.q.values, P.ro.values, b, (cfg.CARRY_RATE * np.clip(P.DTE.astype(float), 1, None) / 365.0).values)
P["DKL"] = P.D_ent; P["GROUND"] = P.G24

with contextlib.redirect_stdout(io.StringIO()):
    pay = rbr.patch_config(rmc.build_payload(P, 2026, "IBKR fair replay (current canon)"), True)
k = pay["config"]
k["universe"] = "SP100 + SPY/QQQ/IWM, IBKR live snapshots"
k["selection"] = (f"current canon on IBKR 15:30/15:45 snapshots: model credit >= {FLOOR:.2f}x width, quoted mid >= model, "
                  f"pooled top-{TOP} by GROUND at k={K:g}; 15:30 scan, 15:45 tops up to {TOP}")
k["regime"] = "bull puts every day; bear calls only below the prior-session SPY 100d SMA"
k["bear_gates"] = "bear calls: IV < 0.35, same credit floor, share the pooled top-6. Earnings and ex-dividend gates apply to both sleeves."
k["parity"] = "no bull parity veto; no bear parity veto"
k["fill_basis"] = f"{FILL:.2f}× smile-fit model credit; partial-WIN at 50% intrinsic; no commission"
k["scoring"] = f"G = Kelly log-growth on P_real at the smile-fit model credit; GROUND = (e^G−1)·e^(−k·D_ent), k = {K:g}"
k["days"] = "every scan day in the archive"
(ROOT / "live/data/ibkr_replay_equity.json").write_text(json.dumps(pay, indent=2))
s = pay["summary"]
print(f"{s['n_trades']} trades, qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, DD {s['strategy_max_dd']}%, "
      f"qty1 ${s['qty1_final']:,.0f}, yield {s['strategy_yield']}%, window {s['window_start']}..{s['window_end']}, weeks {len(pay['weeks'])}")
