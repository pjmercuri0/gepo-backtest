"""Canon on the live all-times frame, beside the Wed/Thu-after-13:00 rule (sim_top1_per_scan.py).
Canon = the Plot tab's replay rule: 15:30 scan, top 6 by GROUND among spreads clearing the 0.45 credit
floor, the quote gate and config.FABLE_GROUND_MIN; 15:45 tops up to 6. 1 contract, 1.00 x model."""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import config as cfg
HERE = Path(__file__).resolve().parent; GMIN = float(cfg.FABLE_GROUND_MIN); TOP = 6
R = pd.read_csv(HERE / "all_times_candidates.csv", dtype={"hm": str}); R["dow"] = pd.to_datetime(R.day).dt.day_name().str[:3]
E = R[(R.model_cw >= 0.45) & R.above_min.astype(bool) & (R.model_credit > 0) & (R.G24 >= GMIN)].copy()
key = ["ticker", "short_strike", "long_strike", "exp"]; out = []
for day, g in E.groupby("day"):
    a = g[g.hm.isin(["1530", "1531"])].sort_values("G24", ascending=False).head(TOP)
    b = g[g.hm == "1545"].sort_values("G24", ascending=False).head(TOP)
    have = set(map(tuple, a[key].values)); b = b[[tuple(r) not in have for r in b[key].values]]
    out.append(pd.concat([a, b.head(max(0, TOP - len(a)))]))
C = pd.concat(out); W = pd.read_csv(HERE / "sim_top1_Wed-Thu_1300.csv", dtype={"hm": str})
def book(P):
    P = P.copy(); P["credit"] = P.model_credit.clip(upper=P.width - 0.01); raw = P.credit - (P.short_strike - P.settle).clip(lower=0, upper=P.width)
    part = (P.settle <= P.short_strike) & (P.settle > P.long_strike)
    P["pnl"] = np.where(part & (raw > 0), raw * 0.5, raw) * 100; P["risk"] = (P.width - P.credit) * 100
    P["out"] = np.where(P.settle >= P.short_strike, "W", np.where(P.settle <= P.long_strike, "L", "P")); return P
def line(lab, x):
    wk = x.groupby("exp").pnl.sum()
    return (f"{lab:<26} n {len(x):>3}  W/L/P {int((x.out=='W').sum())}/{int((x.out=='L').sum())}/{int((x.out=='P').sum())}  win {100*(x.out=='W').mean():5.1f}%  win+posP {100*(x.pnl>0).mean():5.1f}%  "
            f"P&L ${x.pnl.sum():+7.0f}  $/trade {x.pnl.mean():+6.1f}  on risk {100*x.pnl.sum()/x.risk.sum():+6.1f}%  weeks up {int((wk>0).sum())}/{len(wk)}  worst wk ${wk.min():+.0f}  names {x.ticker.nunique()}  days {x.day.nunique()}")
C, W = book(C), book(W)
print(line("canon, all scan days", C)); print(line("canon, Mon-Thu", C[C.dow != "Fri"])); print(line("canon, Wed+Thu only", C[C.dow.isin(["Wed", "Thu"])]))
print(line("rule: Wed/Thu after 13:00", W))
t = pd.DataFrame({"canon n": C.groupby("exp").size(), "canon $": C.groupby("exp").pnl.sum().round(0), "rule n": W.groupby("exp").size(), "rule $": W.groupby("exp").pnl.sum().round(0)}).fillna(0)
print(t.to_string()); print(C.groupby("dow").agg(n=("pnl", "size"), pnl=("pnl", "sum")).round(0).to_string())
# reconcile against the Plot payload (frame starts 2026-08-20)
pay = json.load(open(ROOT / "live/data/ibkr_replay_equity.json")); s = pay["summary"]; c20 = C[C.day >= "2026-08-20"]
print(f"reconcile from 2026-08-20: sim {len(c20)} trades ${c20.pnl.sum():+.2f}  |  Plot payload {s['n_trades']} trades ${s['qty1_final']-10000:+.2f}")
