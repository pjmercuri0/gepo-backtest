"""Rebuild the vendor window (2026-08-20..09-24, bull puts) with the OI gate OFF, score it, and re-measure the IBKR overlap."""
import sys, io, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research/dkl_2026_09_13")); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, config as cfg, build_frame as bf
from sma_bull_regime_sweep import GAP_SERIES
HERE = Path(__file__).resolve().parent; K = 24.0
J = pd.read_csv(HERE / "reconcile_vendor_ibkr.csv"); both = sorted(set(J[J._merge.eq("both")].day))
pi = set(J[J.pick_i.astype(bool) & J.day.isin(both)].set_index(["day", "ticker"]).index)
miss = set(J[J._merge.eq("right_only") & J.pick_i.astype(bool) & J.day.isin(both)].set_index(["day", "ticker"]).index)
out = {}
for min_oi in (cfg.MIN_OPEN_INTEREST, 0):
    with contextlib.redirect_stdout(io.StringIO()):
        C = bf.build_year(2026, set(cfg.SP100_TICKERS), min_oi)
    C = C[(C.entry_date >= "2026-08-20") & (C.entry_date <= "2026-09-24") & C.spread_type.eq("bull_put")].copy()
    cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
    C = C.merge(cl.rename(columns={"date": "expiry_date", "close": "expiry_close"}), on=["ticker", "expiry_date"], how="left").dropna(subset=["expiry_close"])
    closes = ec.backtest_closes(); mu = ec.gap_drift(C, closes, pd.read_parquet(GAP_SERIES))
    with contextlib.redirect_stdout(io.StringIO()):
        S = ec.score(C, closes, k=K, mu=mu)
    S["day"] = S.entry_date.dt.strftime("%Y-%m-%d"); S = S[S.day.isin(both)]
    S["G24"] = S.EV * np.exp(-K * S.D_ent); S["floor"] = (S.model_credit / S.width) >= 0.45
    top = S[S.floor].sort_values(["day", "G24"], ascending=[True, False]).groupby("day").head(6)
    pv = set(top.set_index(["day", "ticker"]).index); uni = set(S.set_index(["day", "ticker"]).index)
    cr = 1.04 * top.model_credit; part = cr - (top.short_strike - top.expiry_close); part = np.where(part > 0, 0.5 * part, part)
    pnl = (np.where(top.expiry_close > top.short_strike, cr, np.where(top.expiry_close <= top.long_strike, -(top.width - cr), part)) * 100).sum()
    out[min_oi] = dict(universe=len(uni), of_68_now_built=len(miss & uni), of_68_now_picked=len(miss & pv), picks=len(pv), overlap=len(pv & pi), pnl=round(pnl))
    print(f"min OI {min_oi:>3}: vendor universe {len(uni)} (day,ticker); of the 68 missing IBKR picks now built {len(miss & uni)}, now picked {len(miss & pv)}; "
          f"vendor top-6 picks {len(pv)}, overlap with IBKR {len(pv & pi)} ({100 * len(pv & pi) / len(pi):.0f}%), vendor P&L qty1 ${pnl:,.0f}")
pd.DataFrame(out).T.to_csv(HERE / "missing68_oi_off.csv")
