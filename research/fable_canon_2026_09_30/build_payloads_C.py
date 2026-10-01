"""Site payloads for strategy C: floor model credit >= 0.45 x width (both sides), bear calls only below the
prior-session SPY 100d SMA and IV < 0.35, pooled top 6 per day by GROUND (no threshold, no quote gate),
entries only when the name has >= 252 sessions of close history (start 2021-01-04), booked 1.04 x model.
Writes live/data/backtest_equity.json and live/data/oot_equity.json (caller backs up first)."""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, config as cfg, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
FLOOR, BEAR_IV, TOP = 0.45, 0.35, 6
K = float(sys.argv[1]) if len(sys.argv) > 1 else float(ec.K)   # D_ent penalty in the rank key (user 2026-09-30: 24)
FILL = float(sys.argv[2]) if len(sys.argv) > 2 else float(ec.FILL_MULT)   # 2026-09-30 (user): site book at 1.00 x model; live booking keeps ec.FILL_MULT
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(HERE / "frame_quotes_oi1.parquet")[KEY + ["IV"]], on=KEY, how="left")   # 2026-09-30: OI >= 1 frame
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"], how="left")
c = c[c.n_before >= ec.WINDOW].copy()
c["GROUND"] = c.EV * np.exp(-K * c.D_ent)   # rank key at this K (prepare() scored at ec.K)
g = ~c.exdiv_hit & ~c.earnings_hit; cw = c.model_credit / c.width
elig = (c.spread_type.eq("bull_put") & g & (cw >= FLOOR)) | (c.spread_type.eq("bear_call") & g & (cw >= FLOOR) & (c.IV < BEAR_IV) & c.below_100)
sel = c[elig].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date", sort=False).head(TOP)
picks = rbr.enrich(realize(sel, 10**6, fill=FILL))
picks.to_parquet(HERE / f"picks_C_252_k{K:g}.parquet")

def captions(payload):
    k = payload["config"]
    k["selection"] = (f"strategy C: model credit >= {FLOOR:.2f}x width (both sides), pooled top-{TOP}/day by GROUND at k={K:g} (no threshold); "
                      f"entries only once the name has {ec.WINDOW} sessions of history (start {picks.entry_date.min().date()})")
    k["regime"] = "bull puts every day; bear calls only below the prior-session SPY 100d SMA"
    k["bear_gates"] = (f"bear calls: IV < {BEAR_IV:.2f}, same credit floor, share the pooled top-{TOP}. Earnings and ex-dividend gates "
                       "(ex-date through expiry+1) apply to both sleeves.")
    k["parity"] = "no bull parity veto; no bear parity veto"
    k["fill_basis"] = f"{FILL:.2f}\u00d7 smile-fit model credit; partial-WIN at 50% intrinsic; no commission"
    k["scoring"] = f"G = Kelly log-growth on P_real at the smile-fit model credit; GROUND = (e^G\u22121)\u00b7e^(\u2212k\u00b7D_ent), k = {K:g}"
    return payload
for lab, m, yr, path in (("IS", picks.entry_date.dt.year <= 2025, 2025, "backtest_equity.json"),
                         ("OOT", picks.entry_date.dt.year == 2026, 2026, "oot_equity.json")):
    p = picks[m]
    with contextlib.redirect_stdout(io.StringIO()):
        pay = captions(rbr.patch_config(rmc.build_payload(p, yr, f"{'backtest 2021-25' if lab == 'IS' else 'OOT 2026'} (strategy C: GROUND rank, bears below 100d)"), lab == "OOT"))
    (ROOT / "live/data" / path).write_text(json.dumps(pay, indent=2))
    s = pay["summary"]
    print(f"{lab}: {s['n_trades']} trades, qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, DD {s['strategy_max_dd']}%, "
          f"qty1 ${s['qty1_final']:,.0f} DD {s['qty1_max_dd']}%, yield {s['strategy_yield']}%, window {s['window_start']}..{s['window_end']} -> live/data/{path}")
