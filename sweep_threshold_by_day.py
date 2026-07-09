"""Per-day GROUND threshold sweep. For each entry day (Mon/Tue/Wed/Thu),
sweep thresholds and find the best one. Then compute combined result using
the best per-day thresholds vs the current canonical 0.001 across-the-board.

CAVEAT: this is in-sample tuning on 2022-2025. The "best" per-day threshold
will be optimistic for out-of-sample performance.

Filter: top-5 ∧ GROUND≥threshold per day, regime ON, vol gate OFF, LAST-scored
(clamped to BBO), 0.85×LAST realized.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
THRESHOLDS = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.010, 0.020]
DOW_NAMES = {0:'Mon', 1:'Tue', 2:'Wed', 3:'Thu'}


def score_year(year):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin([0,1,2,3])]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    candidates = spreads.build_candidates(df)
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), df_idx_view, expiry_close


def filter_and_pnl_with_threshold_map(scored, df_idx_view, expiry_close, thresh_by_dow):
    """thresh_by_dow: dict {0: 0.001, 1: 0.002, ...} — different threshold per DOW."""
    sel = scored.copy()
    sel['entry_dow'] = pd.to_datetime(sel['entry_date']).dt.dayofweek
    keep_rows = []
    for dow, group in sel.groupby('entry_dow'):
        thr = thresh_by_dow.get(int(dow), 0.001)
        qual = group[group['GROUND'] >= thr]
        top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        keep_rows.append(top)
    sel = pd.concat(keep_rows, ignore_index=True) if keep_rows else pd.DataFrame()
    if sel.empty:
        return pd.DataFrame()

    def lookup(row):
        pc = 'put' if row['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            sl = max(float(sr['BidPrice']), min(float(sr['LastPrice']), float(sr['AskPrice'])))
            ll = max(float(lr['BidPrice']), min(float(lr['LastPrice']), float(lr['AskPrice'])))
            return max(sl - ll, 0), expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_last'], sel['expiry_close'] = zip(*sel.apply(lookup, axis=1))
    ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last'] * 0.85
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    return ok


def stats(ok):
    if ok.empty: return 0, 0.0, 0.0, 0.0, 0.0
    n = len(ok); tot = ok['pnl'].sum(); mu = ok['pnl'].mean()
    win = 100*(ok['pnl']>0).mean()
    daily = ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


# Score all years once, cache
year_cache = []
for year in YEARS:
    print(f'── scoring {year} ──', flush=True)
    year_cache.append(score_year(year))


# Per-day, per-threshold matrix
print(f'\n══ Per-day × per-threshold sweep (2022-2025 combined, 0.85×clamped LAST) ══')
results = {dow: {} for dow in [0,1,2,3]}
for dow in [0,1,2,3]:
    print(f'\n── {DOW_NAMES[dow]} only ──')
    print(f'{"threshold":<10} {"trades":>5} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
    print('-'*52)
    for thr in THRESHOLDS:
        thresh_map = {dow: thr}
        all_year = []
        for scored, df_idx_view, expiry_close in year_cache:
            sel_only_this_dow = scored[pd.to_datetime(scored['entry_date']).dt.dayofweek == dow]
            ok = filter_and_pnl_with_threshold_map(sel_only_this_dow, df_idx_view, expiry_close, thresh_map)
            all_year.append(ok)
        all_ok = pd.concat(all_year, ignore_index=True)
        n,tot,mu,win,sh = stats(all_ok)
        results[dow][thr] = (n, tot, sh)
        print(f'{thr:<10.4f} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')


# Find best threshold per day by Sharpe (only if positive trades)
print(f'\n══ Best threshold per day (by Sharpe) ══')
best_by_dow = {}
for dow in [0,1,2,3]:
    best = max(results[dow].items(), key=lambda x: x[1][2])
    best_by_dow[dow] = best[0]
    n, tot, sh = best[1]
    print(f'  {DOW_NAMES[dow]}: threshold {best[0]:.4f}  →  {n} tr, ${tot:+,.0f}, Sh{sh:+.2f}')


# Combined backtest with per-day best thresholds
print(f'\n══ Combined backtest: best-per-day thresholds ══')
all_year = []
for scored, df_idx_view, expiry_close in year_cache:
    ok = filter_and_pnl_with_threshold_map(scored, df_idx_view, expiry_close, best_by_dow)
    all_year.append(ok)
all_best = pd.concat(all_year, ignore_index=True)
n,tot,mu,win,sh = stats(all_best)
print(f'  Per-day best (Mon={best_by_dow[0]:.4f}, Tue={best_by_dow[1]:.4f}, Wed={best_by_dow[2]:.4f}, Thu={best_by_dow[3]:.4f})')
print(f'  → {n} tr, ${tot:+,.0f}, ${mu:+.2f}/tr, {win:.1f}% win, Sharpe {sh:+.2f}')


# Compare to canonical (all 0.001)
print(f'\n══ Compare: canonical (all 0.001) ══')
canon_map = {0: 0.001, 1: 0.001, 2: 0.001, 3: 0.001}
all_year = []
for scored, df_idx_view, expiry_close in year_cache:
    ok = filter_and_pnl_with_threshold_map(scored, df_idx_view, expiry_close, canon_map)
    all_year.append(ok)
all_canon = pd.concat(all_year, ignore_index=True)
n,tot,mu,win,sh = stats(all_canon)
print(f'  → {n} tr, ${tot:+,.0f}, ${mu:+.2f}/tr, {win:.1f}% win, Sharpe {sh:+.2f}')
