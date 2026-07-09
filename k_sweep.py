"""K-sweep on cached realized candidates.

For each k ∈ K_VALS and each DKL flavor ∈ {uniform, forward, backward}:
  recompute Γ = (exp(G) - 1) × exp(-k · DKL)
  sweep GROUND threshold (same across Mon/Tue/Wed/Thu)
  report best Sharpe (with trades, profit, $/tr, win%)

Uses the cached realized parquet from test_dkl_sweep.py — must contain
G (ell), DKL (uniform), dkl_fwd, dkl_bwd, pnl_per_ctr, w_star, entry_date.
Run test_dkl_sweep.py (v2 — put/call split) first to populate the cache.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')

CACHE = 'output/sweep_realized_2023_25.parquet'
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE_DOWS = [0, 1, 2, 3]
KELLY_FRAC = 0.0625
KELLY_CAP = 5
START_BANKROLL = 10_000.0

K_VALS    = [5, 10, 15, 20, 30, 40, 50, 75, 100]
THRESHOLDS = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.030, 0.050]

# SPY trading-day index for equity-curve Sharpe
_spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
TRADING_DAYS = pd.DatetimeIndex(_spy['Date'])

R = pd.read_parquet(CACHE)
print(f'Loaded {len(R):,} candidates from {CACHE}')

needed = ['G', 'DKL', 'dkl_fwd', 'dkl_bwd', 'pnl_per_ctr', 'w_star', 'entry_date', 'spread_type', 'ml_dollar']
missing = [c for c in needed if c not in R.columns]
if missing:
    print(f'MISSING columns: {missing}')
    print(f'Available columns: {sorted(R.columns)}')
    sys.exit(1)

R = R.dropna(subset=['G','DKL','dkl_fwd','dkl_bwd','pnl_per_ctr']).copy()
print(f'  after dropping NaN G/DKL/pnl: {len(R):,}')
R['entry_dow'] = pd.to_datetime(R['entry_date']).dt.dayofweek
print()


def equity_sharpe(ok):
    """Sharpe on daily fractional equity returns, aligned to SPY trading days.
    Matches report_three_sizings.py convention: fixed-$10k sizing, eq=START+cumsum(pnl)."""
    if ok.empty: return 0.0
    daily_pnl = ok.groupby(pd.to_datetime(ok['expiry_date']))['pnl'].sum()
    daily_pnl = daily_pnl.reindex(TRADING_DAYS, fill_value=0.0)
    eq = START_BANKROLL + daily_pnl.cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sd = ret.std(ddof=0)
    return float(ret.mean() * np.sqrt(252) / sd) if sd > 0 else 0.0


def stats(ok):
    """Returns (n, total_pnl, mean_pnl, win%, equity_sharpe)."""
    if ok.empty: return 0, 0, 0, 0, 0
    n = len(ok); tot = ok['pnl'].sum(); mu = ok['pnl'].mean()
    win = 100 * (ok['pnl'] > 0).mean()
    return n, tot, mu, win, equity_sharpe(ok)


def select(R, gcol, thr):
    sub = R[R[gcol] >= thr]
    if sub.empty: return sub
    parts = []
    for dow in ACTIVE_DOWS:
        d = sub[sub['entry_dow'] == dow]
        parts.append(d.sort_values(['entry_date', gcol], ascending=[True, False])
                     .groupby('entry_date').head(5))
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return sel

    def qty(r):
        ws = r['w_star']; mld = r['ml_dollar']
        if pd.isna(ws) or ws <= 0 or mld <= 0: return 1
        return max(1, min(KELLY_CAP, int(KELLY_FRAC * float(ws) * START_BANKROLL / mld)))
    sel = sel.copy()
    sel['qty'] = sel.apply(qty, axis=1)
    sel['pnl'] = sel['qty'] * sel['pnl_per_ctr']
    return sel


def gnd_col(R, dkl_col, k):
    g = R['G']; d = R[dkl_col]
    return np.where(pd.notna(g) & pd.notna(d), (np.exp(g) - 1.0) * np.exp(-k * d), np.nan)


flavors = [('UNIFORM', 'DKL'),
           ('FORWARD  D(hist‖delta)', 'dkl_fwd'),
           ('BACKWARD D(delta‖hist)', 'dkl_bwd')]

for label, dkl_col in flavors:
    print(f'══ {label} k-sweep ══')
    print(f'  best by Sharpe (across {len(THRESHOLDS)} thresholds) per k:')
    print(f'  {"k":>4}  {"best_thr":>9} {"trades":>6} {"profit":>9} {"$/tr":>7} {"win%":>6} {"Sharpe":>7}')
    print('  ' + '-' * 60)
    for k in K_VALS:
        R['_G'] = gnd_col(R, dkl_col, k)
        best = None
        for thr in THRESHOLDS:
            sel = select(R, '_G', thr)
            n, tot, mu, win, sh = stats(sel)
            if best is None or sh > best[0]:
                best = (sh, thr, n, tot, mu, win)
        sh, thr, n, tot, mu, win = best
        print(f'  {k:>4}  {thr:>9.4f} {n:>6} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    print()

# Show best (k, thr) for each flavor head-to-head
print('══ Head-to-head: best (k, thr) per flavor ══')
print(f'{"flavor":<26} {"k":>4} {"thr":>9} {"trades":>6} {"profit":>9} {"$/tr":>7} {"win%":>6} {"Sharpe":>7}')
print('-' * 80)
for label, dkl_col in flavors:
    best = None
    for k in K_VALS:
        R['_G'] = gnd_col(R, dkl_col, k)
        for thr in THRESHOLDS:
            sel = select(R, '_G', thr)
            n, tot, mu, win, sh = stats(sel)
            if best is None or sh > best[0]:
                best = (sh, k, thr, n, tot, mu, win)
    sh, k, thr, n, tot, mu, win = best
    print(f'{label:<26} {k:>4} {thr:>9.4f} {n:>6} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
