"""Ablation: does the D_ent penalty earn its place?

Reruns the canon selection at several k with EVERYTHING else held fixed --
same candidate frame, same threshold, same top-N/day, same fill and commission --
and reports the risk-adjusted result. k=0 is the penalty switched off, so
GROUND collapses to EV and the book is chosen on Kelly expectancy alone.

    GROUND = EV * exp(-k * D_ent)      k=0  ->  GROUND == EV

Calmar = CAGR / |max drawdown| is the headline: the claim being tested is that
D_ent buys drawdown reduction, not extra P&L.

Run from the repo root on the machine that holds the frame:
    python research/dkl_2026_09_13/ablate_k.py
"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec
import report_ent_canon as R

KS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
START = 10000.0
QTY = 2          # canonical sizing


def book(win, k):
    """report_ent_canon.select() with k as a parameter instead of ec.K."""
    C = pd.read_parquet(R.FRAME)
    C = C[C.win == win].dropna(subset=['EV']).copy()
    C['entry_date'] = pd.to_datetime(C.entry_date)
    C['expiry_date'] = pd.to_datetime(C.expiry_date)
    C['GROUND'] = C.EV * np.exp(-k * C.D_ent)
    s = (C[C.GROUND >= ec.THR]
         .sort_values(['entry_date', 'GROUND'], ascending=[True, False])
         .groupby('entry_date').head(ec.TOP_N)).copy()
    s['credit'] = (s.model_credit * ec.FILL_MULT).round(4)
    s['max_loss_adj'] = (s.width - s.credit).round(4)
    s = s[s.max_loss_adj > 0].copy()
    s['_outcome'] = s.apply(R._outcome, axis=1)
    s['pnl'] = s.apply(R._pnl, axis=1) * QTY
    return s


def stats(s):
    if s.empty:
        return None
    pnl = s.groupby('expiry_date')['pnl'].sum().sort_index()
    eq = START + pnl.cumsum()
    peak = eq.cummax()
    mdd = abs(((eq - peak) / peak).min())
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    tot = eq.iloc[-1] / START
    cagr = tot ** (1 / yrs) - 1 if yrs > 0 and tot > 0 else float('nan')
    wk = pnl.resample('W').sum()
    sharpe = wk.mean() / wk.std() * np.sqrt(52) if wk.std() > 0 else float('nan')
    return dict(n=len(s), final=eq.iloc[-1], pnl=s.pnl.sum(), cagr=cagr, mdd=mdd,
                calmar=(cagr / mdd if mdd > 0 else float('nan')), sharpe=sharpe,
                win=(s._outcome == 'WIN').mean() * 100,
                dent=s.D_ent.median())


if __name__ == '__main__':
    if not os.path.exists(R.FRAME):
        sys.exit(f"missing frame: {R.FRAME}\n"
                 "  It is gitignored (~1.4 GB). Run this on the machine that built it,\n"
                 "  or rebuild it from the research scripts in this directory.")
    for win, lbl in (('IS', 'backtest 2020-25'), ('OOT', 'OOT 2026')):
        print(f"\n=== {lbl} ===   fill {ec.FILL_MULT}x model, ${ec.COMMISSION}/spread, "
              f"thr {ec.THR}, top-{ec.TOP_N}/day, qty {QTY}")
        print(f"  {'k':>5}{'trades':>8}{'medD_ent':>10}{'win%':>7}{'P&L':>11}"
              f"{'final':>10}{'CAGR':>8}{'maxDD':>8}{'CALMAR':>8}{'Sharpe':>8}")
        base = None
        for k in KS:
            st = stats(book(win, k))
            if st is None:
                print(f"  {k:>5.2f}   no trades"); continue
            tag = '   <- canon' if abs(k - ec.K) < 1e-9 else ('   <- penalty OFF' if k == 0 else '')
            print(f"  {k:>5.2f}{st['n']:>8}{st['dent']:>10.4f}{st['win']:>7.1f}"
                  f"{st['pnl']:>11,.0f}{st['final']:>10,.0f}{st['cagr']*100:>7.1f}%"
                  f"{st['mdd']*100:>7.1f}%{st['calmar']:>8.2f}{st['sharpe']:>8.2f}{tag}")
