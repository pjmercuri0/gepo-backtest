"""SPY N-day SMA sweep for the bear-call regime gate, strategy C (k=24, 252-session start).
Bulls every day; bear calls only when the PRIOR session's SPY close < its N-day SMA."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, prepare, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
K = 24.0
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c)
c = c.merge(pd.read_parquet(HERE / "frame_quotes.parquet")[KEY + ["IV"]], on=KEY)
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"]); c = c[c.n_before >= 252].copy()
c["GROUND"] = c.EV * np.exp(-K * c.D_ent)
g = ~c.exdiv_hit & ~c.earnings_hit; cw = c.model_credit / c.width
bull = c.spread_type.eq("bull_put") & g & (cw >= 0.45)
bear_base = c.spread_type.eq("bear_call") & g & (cw >= 0.45) & (c.IV < 0.35)
# prior-session SPY close vs its N-day SMA
s = spy.sort_values("Date").reset_index(drop=True); dates = s.Date.to_numpy("datetime64[ns]")
pos = np.searchsorted(dates, c.entry_date.to_numpy("datetime64[ns]"), side="left") - 1; ok = pos >= 0; safe = np.clip(pos, 0, len(s) - 1)
close_prev = s.Close.to_numpy(float)[safe]
def run(name, elig):
    e = c[elig].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date", sort=False).head(6)
    picks = rbr.enrich(realize(e, 10**6)); row = dict(gate=name)
    for lab, m, yr in (("IS", picks.entry_date.dt.year <= 2025, 2025), ("OOT", picks.entry_date.dt.year == 2026, 2026)):
        p = picks[m]
        with contextlib.redirect_stdout(io.StringIO()):
            sm = rmc.build_payload(p, yr, name)["summary"]
        row[f"{lab}_bears"] = int(p.spread_type.eq("bear_call").sum()); row[f"{lab}_bear_pnl"] = round(p[p.spread_type.eq("bear_call")].pnl_per_contract.sum())
        row[f"{lab}_final"] = sm["strategy_final"]; row[f"{lab}_dsh"] = sm["strategy_sharpe_dollar"]; row[f"{lab}_dd"] = sm["strategy_max_dd"]; row[f"{lab}_yield"] = sm["strategy_yield"]
    return row
rows = [run("no bears", bull)]
for N in (10, 20, 30, 50, 75, 100, 125, 150, 200):
    sma = s.Close.rolling(N, min_periods=N).mean().to_numpy(float)[safe]
    below = pd.Series(ok & (close_prev < sma), index=c.index)
    rows.append(run(f"below SMA{N}", bull | (bear_base & below)))
rows.append(run("bears every day", bull | bear_base))
df = pd.DataFrame(rows); df.to_csv(HERE / "sma_sweep_C.csv", index=False); print(df.to_string(index=False))
