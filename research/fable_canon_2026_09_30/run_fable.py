"""Fable Canon (config.py 2026-09-30 final) on the vendor frame, IS 2020-08..2025 and OOT 2026.

Rules (live/ranker.py fable block, pooled): bull puts and bear calls every day; earnings/ex-div
gates; floor = 1.0 x model_credit / width >= 0.45 (both sides); bears also IV < 0.35 (vendor
short-leg IV); quote gate vendor_mid >= 1.00 x model_credit; pooled top 6 per day by
0.951 x vendor_mid / width; booked at ec.FILL_MULT (1.04) x model_credit (bear_regime_sweep.realize).
Payloads go to this directory, NOT live/data.
"""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, config as cfg, report_mid_canon as rmc
import report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize

HERE = Path(__file__).resolve().parent
KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]

with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
q = pd.read_parquet(HERE / "frame_quotes.parquet")
c = c.merge(q[KEY + ["vendor_mid", "vendor_nat", "IV", "long_IV"]], on=KEY, how="left")
assert c.vendor_mid.notna().all() and c.IV.notna().all(), "quote join incomplete"
c["cw_floor"] = cfg.FABLE_FLOOR_MULT * c.model_credit / c.width
c["cw_fill"] = cfg.FABLE_FILL_FRAC * c.vendor_mid / c.width
c["quote_ok"] = c.vendor_mid >= cfg.FABLE_QUOTE_GATE * c.model_credit
gates = ~c.exdiv_hit & ~c.earnings_hit
bull = c.spread_type.eq("bull_put") & gates & (c.cw_floor >= cfg.FABLE_MIN_CW)
bear = c.spread_type.eq("bear_call") & gates & (c.cw_floor >= cfg.FABLE_BEAR_MIN_CW) & (c.IV < cfg.FABLE_BEAR_MAX_IV)
print(f"config: floor {cfg.FABLE_FLOOR_BASIS} x{cfg.FABLE_FLOOR_MULT} >= {cfg.FABLE_MIN_CW}/{cfg.FABLE_BEAR_MIN_CW}, bear IV<{cfg.FABLE_BEAR_MAX_IV}, "
      f"quote gate {cfg.FABLE_QUOTE_GATE}, pooled={cfg.FABLE_POOLED} top {cfg.FABLE_TOP_N}, rank {cfg.FABLE_RANK_KEY}, book {cfg.FABLE_BOOK_BASIS} x{ec.FILL_MULT}")
print(f"frame {len(c):,} cands {c.entry_date.min().date()}..{c.entry_date.max().date()}; bull floor-eligible {bull.sum():,}, "
      f"+quote gate {(bull & c.quote_ok).sum():,}; bear floor+IV {bear.sum():,}, +quote gate {(bear & c.quote_ok).sum():,}")

def select(name, elig, pooled=True, top=6, key="cw_fill"):
    e = c[elig].sort_values(["entry_date", key], ascending=[True, False])
    if pooled:
        sel = e.groupby("entry_date", sort=False).head(top)
    else:
        sel = e.groupby(["entry_date", "spread_type"], sort=False).head(top)
    picks = rbr.enrich(realize(sel, 10**6))
    rows = []
    for lab, m in (("IS", picks.entry_date.dt.year <= 2025), ("OOT", picks.entry_date.dt.year == 2026)):
        p = picks[m]
        if p.empty: continue
        with contextlib.redirect_stdout(io.StringIO()):
            pay = rbr.patch_config(rmc.build_payload(p, 2025 if lab == "IS" else 2026, f"{name} {lab}"), lab == "OOT")
        s = pay["summary"]
        rows.append(dict(variant=name, window=lab, n=len(p), bulls=int(p.spread_type.eq("bull_put").sum()),
                         days=p.entry_date.nunique(), win=round(100 * p._outcome.eq("WIN").mean(), 1),
                         full_loss=round(100 * p._outcome.eq("LOSS").mean(), 1),
                         qty2_final=s["strategy_final"], qty1_final=s["qty1_final"], dsharpe=s["strategy_sharpe_dollar"],
                         sharpe=s["strategy_sharpe"], dd_qty2=s["strategy_max_dd"], dd_qty1=s["qty1_max_dd"], yield_pct=s["strategy_yield"],
                         pnl_per=round(p.pnl_per_contract.mean(), 1)))
        if name == "canon":
            (HERE / f"{'backtest' if lab == 'IS' else 'oot'}_equity.json").write_text(json.dumps(pay, indent=2))
    yr = picks.groupby(picks.entry_date.dt.year).agg(n=("pnl_per_contract", "size"), pnl_qty1=("pnl_per_contract", "sum"),
                                                     win=("_outcome", lambda s: round(100 * (s == "WIN").mean(), 1)))
    return rows, yr, picks

out = []
r, yr, picks = select("canon", (bull | bear) & c.quote_ok, pooled=cfg.FABLE_POOLED, top=cfg.FABLE_TOP_N); out += r
picks.to_parquet(HERE / "picks_canon.parquet")
print("\nCANON per year (qty1 $):"); print(yr.round(0).to_string())
r, _, _ = select("3_per_side", (bull | bear) & c.quote_ok, pooled=False, top=3); out += r
r, _, _ = select("no_quote_gate", bull | bear, pooled=True, top=6); out += r
r, _, _ = select("rank_GROUND", (bull | bear) & c.quote_ok, pooled=True, top=6, key="GROUND"); out += r
r, _, _ = select("bulls_only", bull & c.quote_ok, pooled=True, top=6); out += r
df = pd.DataFrame(out); df.to_csv(HERE / "results.csv", index=False)
print("\n", df.to_string(index=False))
