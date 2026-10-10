"""Rule (user 2026-10-10): at every scan from START on the chosen weekdays, take the top-GROUND qualifying
spread (model credit >= 0.45 x width, quote >= model, GROUND >= config.FABLE_GROUND_MIN); no name twice on
the same day (a held name is skipped and the next-best name is taken). 1 contract, booked 1.00 x model,
settled by spreads.settle_pnl's rule. Runs on all_times_candidates.csv.
Usage: sim_top1_per_scan.py [days=Wed,Thu] [start=1300] [strict=0]   (strict=1: a held top name means no pick)
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import config as cfg
HERE = Path(__file__).resolve().parent
DAYS = (sys.argv[1] if len(sys.argv) > 1 else "Wed,Thu").split(","); START = sys.argv[2] if len(sys.argv) > 2 else "1300"
STRICT = len(sys.argv) > 3 and sys.argv[3] == "1"; GMIN = float(cfg.FABLE_GROUND_MIN)
R = pd.read_csv(HERE / "all_times_candidates.csv", dtype={"hm": str})
R["dow"] = pd.to_datetime(R.day).dt.day_name().str[:3]
E = R[(R.model_cw >= 0.45) & R.above_min.astype(bool) & (R.model_credit > 0) & (R.G24 >= GMIN) & R.dow.isin(DAYS) & (R.hm >= START) & (R.hm < "1600")]
picks = []
for day, g in E.groupby("day"):
    held = set()
    for hm, s in g.groupby("hm"):
        s = s.sort_values("G24", ascending=False)
        if STRICT: s = s.head(1)
        s = s[~s.ticker.isin(held)]
        if len(s): picks.append(s.iloc[0]); held.add(s.iloc[0].ticker)
P = pd.DataFrame(picks)
P["credit"] = P.model_credit.clip(upper=P.width - 0.01); raw = P.credit - (P.short_strike - P.settle).clip(lower=0, upper=P.width)
part = (P.settle <= P.short_strike) & (P.settle > P.long_strike)
P["pnl"] = np.where(part & (raw > 0), raw * 0.5, raw) * 100; P["risk"] = (P.width - P.credit) * 100
P["out"] = np.where(P.settle >= P.short_strike, "W", np.where(P.settle <= P.long_strike, "L", "P"))
def line(lab, x):
    return (f"{lab:<12} n {len(x):>3}  W/L/P {int((x.out=='W').sum())}/{int((x.out=='L').sum())}/{int((x.out=='P').sum())}  win {100*(x.out=='W').mean():5.1f}%  "
            f"win+posP {100*(x.pnl>0).mean():5.1f}%  P&L ${x.pnl.sum():+8.0f}  $/trade {x.pnl.mean():+6.1f}  risk ${x.risk.sum():7.0f}  on risk {100*x.pnl.sum()/x.risk.sum():+6.1f}%")
scan_days = R[R.dow.isin(DAYS) & (R.hm >= START) & (R.hm < "1600")]
print(f"days {DAYS} from {START}{' STRICT' if STRICT else ''}: {scan_days.day.nunique()} days, {scan_days.groupby(['day','hm']).ngroups} scans, {P.day.nunique()} days with a pick, {P.ticker.nunique()} names")
print(line("ALL", P))
for d in DAYS: print(line(d, P[P.dow == d]))
for e, x in P.groupby("exp"): print(line(e, x))
wk = P.groupby("exp").pnl.sum(); print(f"weeks up {int((wk>0).sum())} of {len(wk)}; worst week ${wk.min():+.0f}; best ${wk.max():+.0f}; picks/day mean {P.groupby('day').size().mean():.1f} (min {P.groupby('day').size().min()}, max {P.groupby('day').size().max()})")
P.to_csv(HERE / f"sim_top1_{'-'.join(DAYS)}_{START}{'_strict' if STRICT else ''}.csv", index=False)
