"""Reconcile vendor (EOD, featATM8 + leg quotes) vs IBKR 15:30 snapshot replay for strategy C, 2026-08-20..09-24, bull puts.
Writes reconcile_vendor_ibkr.csv (joined pairs) and prints the summary stats asked for in the task."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
K = 24.0; W0, W1 = "2026-08-20", "2026-09-24"

# ---- vendor side: same pipeline as build_payloads_C (prepare, gates, quotes, 252 gate), window, bull puts
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(HERE / "frame_quotes_oi1.parquet")[KEY + ["vendor_mid", "IV", "s_bid", "s_ask", "l_bid", "l_ask"]], on=KEY, how="left")
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"], how="left")
v = c[(c.entry_date >= W0) & (c.entry_date <= W1) & c.spread_type.eq("bull_put")].copy()
v["G24"] = v.EV * np.exp(-K * v.D_ent)
v["hist_ok"] = v.n_before >= 252
v["gated_out"] = v.exdiv_hit | v.earnings_hit
v["floor_pass"] = (v.model_credit / v.width) >= 0.45
v["gate_pass"] = v.vendor_mid >= v.model_credit
v["elig"] = v.hist_ok & ~v.gated_out & v.floor_pass & v.gate_pass
v = v.sort_values(["entry_date", "G24"], ascending=[True, False])
v["rank_v"] = v[v.elig].groupby("entry_date").cumcount() + 1
v["pick_v"] = v.rank_v <= 6
v["day"] = v.entry_date.dt.strftime("%Y-%m-%d")
V = v[["day", "ticker", "short_strike", "long_strike", "width", "vendor_mid", "model_credit", "IV", "EV", "D_ent", "G24", "p", "q", "ro",
       "expiry_close", "hist_ok", "gated_out", "floor_pass", "gate_pass", "elig", "rank_v", "pick_v"]].rename(
       columns={"short_strike": "ss_v", "long_strike": "ls_v", "width": "w_v", "vendor_mid": "quote_v", "model_credit": "model_v", "IV": "IV_v",
                "EV": "EV_v", "D_ent": "Dent_v", "G24": "G24_v", "p": "p_v", "q": "q_v", "ro": "ro_v", "expiry_close": "settle_v"})
V.to_csv(HERE / "vendor_candidates_aug20_sep24.csv", index=False)

# ---- IBKR side
I = pd.read_csv(HERE / "ibkr_replay_candidates_1530_1545.csv")
I["floor_pass"] = I.model_cw >= 0.45; I["gate_pass"] = I.net_credit >= I.model_credit; I["elig"] = I.floor_pass & I.gate_pass
I1530 = I[I.hm.isin([1530, 1531])].sort_values(["day", "hm"]).drop_duplicates(["day", "ticker"], keep="first").copy()  # 1530 preferred over 1531
I1530 = I1530.sort_values(["day", "G24"], ascending=[True, False]); I1530["rank_i"] = I1530[I1530.elig].groupby("day").cumcount() + 1
I1530["pick_1530"] = I1530.rank_i <= 6
# 15:45 top-up to 6 total with names 15:30 did not pick
I1545 = I[I.hm.eq(1545)].sort_values(["day", "G24"], ascending=[True, False])
picks_i = []
for day, g in I1530.groupby("day"):
    base = g[g.pick_1530]; names = set(base.ticker); rows = [base]
    need = 6 - len(base)
    if need > 0:
        top = I1545[(I1545.day == day) & I1545.elig & ~I1545.ticker.isin(names)].head(need); rows.append(top)
    picks_i.append(pd.concat(rows))
PI = pd.concat(picks_i) if picks_i else I1530.iloc[0:0]
PI["pnl_i"] = np.where(PI.settle > PI.short_strike, 1.04 * PI.model_credit,
                       np.where(PI.settle <= PI.long_strike, -(PI.width - 1.04 * PI.model_credit),
                                np.where(1.04 * PI.model_credit - (PI.short_strike - PI.settle) > 0, 0.5 * (1.04 * PI.model_credit - (PI.short_strike - PI.settle)),
                                         1.04 * PI.model_credit - (PI.short_strike - PI.settle)))) * 100
PV = V[V.pick_v].copy()
PV["pnl_v"] = np.where(PV.settle_v > PV.ss_v, 1.04 * PV.model_v,
                       np.where(PV.settle_v <= PV.ls_v, -(PV.w_v - 1.04 * PV.model_v),
                                np.where(1.04 * PV.model_v - (PV.ss_v - PV.settle_v) > 0, 0.5 * (1.04 * PV.model_v - (PV.ss_v - PV.settle_v)),
                                         1.04 * PV.model_v - (PV.ss_v - PV.settle_v)))) * 100

# ---- join on (day, ticker), IBKR 15:30 row
J = V.merge(I1530[["day", "ticker", "short_strike", "long_strike", "width", "net_credit", "model_credit", "IV", "EV", "D_ent", "G24", "p", "q", "ro", "settle",
                   "floor_pass", "gate_pass", "elig", "rank_i", "pick_1530", "entry_price"]].rename(
            columns={"short_strike": "ss_i", "long_strike": "ls_i", "width": "w_i", "net_credit": "quote_i", "model_credit": "model_i", "IV": "IV_i",
                     "EV": "EV_i", "D_ent": "Dent_i", "G24": "G24_i", "p": "p_i", "q": "q_i", "ro": "ro_i", "settle": "settle_i",
                     "floor_pass": "floor_i", "gate_pass": "gate_i", "elig": "elig_i"}),
            on=["day", "ticker"], how="outer", indicator=True)
J["pick_i"] = J.set_index(["day", "ticker"]).index.isin(PI.set_index(["day", "ticker"]).index)
J.to_csv(HERE / "reconcile_vendor_ibkr.csv", index=False)

vdays, idays = set(V.day), set(I1530.day); both = sorted(vdays & idays)
print(f"days: vendor {len(vdays)}, IBKR 15:30 {len(idays)}, both {len(both)}; vendor-only {sorted(vdays - idays)}; IBKR-only {sorted(idays - vdays)}")
B = J[J._merge.eq("both") & J.day.isin(both)].copy()
for col in ("hist_ok", "gated_out", "floor_pass", "gate_pass", "elig", "pick_v", "floor_i", "gate_i", "elig_i", "pick_1530", "pick_i"):
    B[col] = B[col].astype(bool)
print(f"(day,ticker): vendor {int((J._merge != 'right_only').sum())}, IBKR {int((J._merge != 'left_only').sum())}, joined {len(B)}, "
      f"vendor-only {int(J._merge.eq('left_only').sum())}, IBKR-only {int(J._merge.eq('right_only').sum())}")
same = (B.ss_v == B.ss_i) & (B.ls_v == B.ls_i)
print(f"a. same strikes: {int(same.sum())}/{len(B)} = {100 * same.mean():.0f}%; same short strike {100 * (B.ss_v == B.ss_i).mean():.0f}%; same width {100 * (B.w_v == B.w_i).mean():.0f}%")
def q(s): s = s.dropna(); return f"median {s.median():.3f}, p10 {s.quantile(.1):.3f}, p90 {s.quantile(.9):.3f}"
print(f"b. model_credit vendor/IBKR: {q(B.model_v / B.model_i)}  | same-strike pairs: {q((B.model_v / B.model_i)[same])}")
print(f"   IV vendor/IBKR:           {q(B.IV_v / B.IV_i)}")
print(f"   quote vendor/IBKR:        {q(B.quote_v / B.quote_i)}  | same-strike pairs: {q((B.quote_v / B.quote_i)[same])}")
print(f"   quote/model vendor {q(B.quote_v / B.model_v)} ; IBKR {q(B.quote_i / B.model_i)}")
print(f"   EV vendor-IBKR: {q(B.EV_v - B.EV_i)} ; D_ent vendor/IBKR: {q(B.Dent_v / B.Dent_i)} ; settle equal {100 * (np.isclose(B.settle_v, B.settle_i)).mean():.0f}%")
print(f"c. floor agree {100 * (B.floor_pass == B.floor_i).mean():.0f}% (vendor pass {100 * B.floor_pass.mean():.0f}%, IBKR {100 * B.floor_i.mean():.0f}%); "
      f"gate agree {100 * (B.gate_pass == B.gate_i).mean():.0f}% (vendor pass {100 * B.gate_pass.mean():.0f}%, IBKR {100 * B.gate_i.mean():.0f}%); "
      f"elig agree {100 * ((B.elig) == B.elig_i).mean():.0f}%")
rc = B.groupby("day").apply(lambda g: g.G24_v.rank().corr(g.G24_i.rank()) if len(g) > 4 else np.nan).dropna()
print(f"d. within-day Spearman(G24 vendor, G24 IBKR): median {rc.median():.2f}, mean {rc.mean():.2f}, n days {len(rc)}; EV rank corr median "
      f"{B.groupby('day').apply(lambda g: g.EV_v.rank().corr(g.EV_i.rank()) if len(g) > 4 else np.nan).median():.2f}; D_ent rank corr median "
      f"{B.groupby('day').apply(lambda g: g.Dent_v.rank().corr(g.Dent_i.rank()) if len(g) > 4 else np.nan).median():.2f}")
# e. one-sided drops
e = {}
e["not built on IBKR side (vendor has it)"] = int((J._merge.eq("left_only") & J.day.isin(both)).sum())
e["not built on vendor side (IBKR has it)"] = int((J._merge.eq("right_only") & J.day.isin(both)).sum())
e["vendor hist<252 or earnings/exdiv gate (no IBKR equivalent in CSV)"] = int((B.gated_out | ~B.hist_ok).sum())
e["floor: vendor fails, IBKR passes"] = int((~B.floor_pass & B.floor_i).sum()); e["floor: IBKR fails, vendor passes"] = int((B.floor_pass & ~B.floor_i).sum())
e["gate: vendor fails, IBKR passes"] = int((~B.gate_pass & B.gate_i).sum()); e["gate: IBKR fails, vendor passes"] = int((B.gate_pass & ~B.gate_i).sum())
print("e. one-sided drops (joined days):"); [print(f"   {k}: {n}") for k, n in e.items()]
# 4. pick sets
pv = set(PV[PV.day.isin(both)].set_index(["day", "ticker"]).index); pi = set(PI[PI.day.isin(both)].set_index(["day", "ticker"]).index)
print(f"\n4. picks on joined days: vendor {len(pv)}, IBKR {len(pi)}, overlap {len(pv & pi)} ({100 * len(pv & pi) / max(1, len(pi)):.0f}% of IBKR, {100 * len(pv & pi) / max(1, len(pv)):.0f}% of vendor)")
print(f"   P&L qty1 booked 1.04x model: vendor picks ${PV.pnl_v.sum():,.0f} ({len(PV)} picks, all days), IBKR picks ${PI.pnl_i.sum():,.0f} ({len(PI)} picks); "
      f"joined days only: vendor ${PV[PV.day.isin(both)].pnl_v.sum():,.0f}, IBKR ${PI[PI.day.isin(both)].pnl_i.sum():,.0f}")
# where did IBKR picks go on the vendor side?
ip = B[B.pick_i]; print(f"   IBKR picks present in vendor frame: {len(ip)}/{len(pi)}; of those vendor-eligible {int(ip.elig.sum())}, vendor-picked {int(ip.pick_v.sum())}; "
      f"fail vendor floor {int((~ip.floor_pass).sum())}, fail vendor gate {int((~ip.gate_pass).sum())}, hist/earn gate {int((ip.gated_out | ~ip.hist_ok).sum())}, "
      f"eligible but ranked out {int((ip.elig & ~ip.pick_v).sum())}")
vp = B[B.pick_v]; print(f"   vendor picks present in IBKR frame: {len(vp)}/{len(pv)}; of those IBKR-eligible {int(vp.elig_i.sum())}, IBKR-picked {int(vp.pick_i.sum())}; "
      f"fail IBKR floor {int((~vp.floor_i).sum())}, fail IBKR gate {int((~vp.gate_i).sum())}, eligible but ranked out {int((vp.elig_i & ~vp.pick_i).sum())}")
# hybrid selections on the joined universe: swap one component at a time, measure overlap with IBKR picks and P&L
def sel(df, elig_col, key_col):
    d = df[df[elig_col]].sort_values(["day", key_col], ascending=[True, False]).groupby("day").head(6)
    return set(d.set_index(["day", "ticker"]).index), d
B["elig_vgate_ifloor"] = B.hist_ok & ~B.gated_out & B.floor_i & B.gate_pass
B["elig_vfloor_igate"] = B.hist_ok & ~B.gated_out & B.floor_pass & B.gate_i
B["elig_i_only"] = B.elig_i; B["elig_v_only"] = B.elig
ref, _ = sel(B, "elig_i_only", "G24_i")
print("\n   hybrids on the joined universe (overlap with IBKR-rule picks on the same universe):")
for name, ec_, kc in (("vendor elig + vendor G24 (vendor rule)", "elig_v_only", "G24_v"), ("vendor elig + IBKR G24", "elig_v_only", "G24_i"),
                      ("IBKR elig + vendor G24", "elig_i_only", "G24_v"), ("vendor floor+hist, IBKR gate, vendor G24", "elig_vfloor_igate", "G24_v"),
                      ("IBKR floor, vendor gate+hist, vendor G24", "elig_vgate_ifloor", "G24_v"), ("IBKR elig + IBKR G24 (IBKR rule)", "elig_i_only", "G24_i")):
    s_, d_ = sel(B, ec_, kc)
    print(f"   {name:<45} n {len(s_):>3}  overlap {100 * len(s_ & ref) / max(1, len(ref)):>3.0f}%")
