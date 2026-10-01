"""Strategy C (k arg, 252 gate, no quote gate) on an alternate candidate frame. Writes payloads + picks to this dir ONLY.
usage: run_C_on_frame.py <frame.parquet> <frame_quotes.parquet> <K> <label>"""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
import bear_regime_sweep as brs
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
FRAME, QUOTES, K, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
brs.FRAME = FRAME
FLOOR, BEAR_IV, TOP = 0.45, 0.35, 6
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = brs.prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(QUOTES)[KEY + ["IV"]], on=KEY, how="left")
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"], how="left")
c = c[c.n_before >= ec.WINDOW].copy(); c["GROUND"] = c.EV * np.exp(-K * c.D_ent)
g = ~c.exdiv_hit & ~c.earnings_hit; cw = c.model_credit / c.width
elig = (c.spread_type.eq("bull_put") & g & (cw >= FLOOR)) | (c.spread_type.eq("bear_call") & g & (cw >= FLOOR) & (c.IV < BEAR_IV) & c.below_100)
print(f"{LABEL}: frame {len(c):,} candidates (252 gate), eligible {int(elig.sum()):,}, eligible per day {elig.sum() / c.entry_date.nunique():.1f}")
sel = c[elig].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date", sort=False).head(TOP)
picks = rbr.enrich(realize(sel, 10**6)); picks.to_parquet(HERE / f"picks_{LABEL}.parquet")
for lab, m, yr in (("IS", picks.entry_date.dt.year <= 2025, 2025), ("OOT", picks.entry_date.dt.year == 2026, 2026)):
    p = picks[m]
    with contextlib.redirect_stdout(io.StringIO()):
        pay = rbr.patch_config(rmc.build_payload(p, yr, f"{LABEL} {lab}"), lab == "OOT")
    pay["config"]["selection"] = f"strategy C ({LABEL}): model credit >= {FLOOR:.2f}x width, pooled top-{TOP}/day by GROUND at k={K:g}, 252-session start"
    (HERE / f"{LABEL}_{'backtest' if lab == 'IS' else 'oot'}_equity.json").write_text(json.dumps(pay, indent=2))
    s = pay["summary"]
    print(f"  {lab}: {s['n_trades']} trades, qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, DD {s['strategy_max_dd']}%, yield {s['strategy_yield']}%, "
          f"win {100 * p._outcome.eq('WIN').mean():.0f}%, full-loss {100 * p._outcome.eq('LOSS').mean():.0f}%, bulls {int(p.spread_type.eq('bull_put').sum())}")
