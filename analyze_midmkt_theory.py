"""Post-sweep analysis on the mid-basis cache (output/sweep_midmkt_2020_25.parquet):

1. Theoretical k (Catoni/PAC-Bayes): k = (1+b_med) * sqrt(N / (8*(Kbar + ln(1/delta))))
   computed from the picks actually selected at the sweep-optimal (k, thr).
2. GROUND vs G-alone proof under the new basis:
   - top-5/day by G alone vs by GROUND at optimal k (same thr discipline)
   - DKL median split WITHIN top-G picks: Welch t-test on pnl (old result: t=4.7)
Usage: python3 analyze_midmkt_theory.py <k_opt> <thr_opt> [pnl_col, default pnl_80]
"""
import sys, math
import numpy as np, pandas as pd
from scipy import stats as sps

K_OPT  = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
THR    = float(sys.argv[2]) if len(sys.argv) > 2 else 0.075
PNLCOL = sys.argv[3] if len(sys.argv) > 3 else 'pnl_80'
DELTA  = 0.05

R = pd.read_parquet('output/sweep_midmkt_2020_25.parquet')
R['entry_date'] = pd.to_datetime(R['entry_date'])
R['GAMMA'] = (np.exp(R['G']) - 1.0) * np.exp(-K_OPT * R['DKL'])
R['b'] = R['net_credit'] / R['max_loss']
print(f'{len(R):,} candidates | basis: raw mid selection, {PNLCOL} fills\n')

# ── 1. Theoretical k at the empirical optimum ─────────────────────────
sel = (R[R['GAMMA'] >= THR]
       .sort_values(['entry_date','GAMMA'], ascending=[True,False])
       .groupby('entry_date').head(5))
N    = len(sel)
bmed = float(sel['b'].median())
Kbar = float(sel['DKL'].mean())
k_th = (1.0 + bmed) * math.sqrt(N / (8.0 * (Kbar + math.log(1.0/DELTA))))
print('── Theoretical k (Catoni/PAC-Bayes), from picks at the empirical optimum ──')
print(f'  selected N={N}, median b={bmed:.3f}, mean DKL (Kbar)={Kbar:.4f}, delta={DELTA}')
print(f'  k_theory = (1+{bmed:.2f}) * sqrt({N}/(8*({Kbar:.4f}+ln(1/{DELTA})))) = {k_th:.1f}')
print(f'  (LAST-basis reference: b_med 2.33, N 314 -> k 12)')
for Nref in [30, 100, 314, N]:
    kv = (1.0 + bmed) * math.sqrt(Nref / (8.0 * (Kbar + math.log(1.0/DELTA))))
    print(f'    at N={Nref:<5} -> k={kv:.1f}')

# ── 2. GROUND vs G-alone ──────────────────────────────────────────────
print(f'\n── GROUND vs G-alone (top-5/day, {PNLCOL}) ──')
def perf(sel, label):
    tot = sel[PNLCOL].sum(); mu = sel[PNLCOL].mean()
    win = 100*(sel[PNLCOL] > 0).mean()
    print(f'  {label:<28} n={len(sel):<5} total ${tot:>10,.0f}  $/tr {mu:>7.2f}  win {win:.1f}%')
    return tot

g_alone = (R.sort_values(['entry_date','G'], ascending=[True,False])
            .groupby('entry_date').head(5))
ground5 = (R[R['GAMMA'] >= THR]
            .sort_values(['entry_date','GAMMA'], ascending=[True,False])
            .groupby('entry_date').head(5))
tot_g  = perf(g_alone, 'G alone (no gate)')
tot_gr = perf(ground5, f'GROUND k={K_OPT:g} thr={THR:g}')
print(f'  GROUND / G-alone total: {tot_gr/tot_g:.2f}x' if tot_g != 0 else '  G-alone total is 0')

# ── 3. DKL split inside top-G picks (the t=4.7 analog) ────────────────
print('\n── DKL median split WITHIN top-G picks ──')
dmed = g_alone['DKL'].median()
lo = g_alone[g_alone['DKL'] <= dmed][PNLCOL]
hi = g_alone[g_alone['DKL'] >  dmed][PNLCOL]
t, p = sps.ttest_ind(lo, hi, equal_var=False)
print(f'  DKL median among top-G: {dmed:.4f}')
print(f'  low-DKL  half: n={len(lo):<5} mean ${lo.mean():>7.2f}  total ${lo.sum():>10,.0f}')
print(f'  high-DKL half: n={len(hi):<5} mean ${hi.mean():>7.2f}  total ${hi.sum():>10,.0f}')
print(f'  Welch t = {t:.2f}  (p = {p:.2e})   [LAST-basis reference: t = 4.7]')
