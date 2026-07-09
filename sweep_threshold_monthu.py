"""Mon+Thu entries, hold to Friday expiry, SP100, top-5 per day.
Sweep GROUND threshold; report combined 2022-2025 + per-year Sharpe/profit.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
THRESHOLDS = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.01, 0.02]

spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy['rv20'] = spy['Close'].pct_change().rolling(20).std() * np.sqrt(252) * 100
spy_idx = pd.DatetimeIndex(spy['Date'])
def rv_for(d):
    pos = spy_idx.searchsorted(d, side='right') - 1
    return float(spy.iloc[pos]['rv20']) if pos >= 0 else None


# Per-year cache: load + score once, then apply each threshold.
year_scored = {}
for year in YEARS:
    pq = f'output/{year}_sp500_last.parquet'
    print(f'\n── loading {year} ──', flush=True)
    df_full = pd.read_parquet(pq)
    df_full = df_full[df_full['Symbol'].isin(SP100)]

    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]

    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin([0, 3])]    # Mon + Thu only
    df = df[df['exp_dow'] == 4]
    df = df[(df['DTE'] >= 1) & (df['DTE'] <= 4)]
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    t0 = time.time()
    candidates = spreads.build_candidates(df)
    print(f'  candidates {len(candidates):,} ({time.time()-t0:.0f}s)', flush=True)
    t0 = time.time()
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])
    print(f'  scored {len(scored):,} ({time.time()-t0:.0f}s)', flush=True)

    year_scored[year] = {
        'scored': scored,
        'df_idx_view': df_idx_view,
        'expiry_close': expiry_close,
    }


def run_threshold(year_data, thr):
    scored      = year_data['scored']
    df_idx_view = year_data['df_idx_view']
    expiry_close = year_data['expiry_close']

    sel = scored[scored['GROUND'] >= thr] if thr > 0 else scored
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()
    sel['rv20'] = pd.to_datetime(sel['entry_date']).apply(rv_for)
    sel['dow']  = pd.to_datetime(sel['entry_date']).dt.dayofweek
    sel = sel[~((sel['dow']!=0) & (sel['rv20']>=20.0))]

    def lookup_last(row):
        pc = 'put' if row['spread_type'] == 'bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            last_cr = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
            exp_close = expiry_close.get((row['ticker'], row['expiry_date']))
            return last_cr, exp_close
        except KeyError:
            return None, None
    sel['last_credit'], sel['expiry_close'] = zip(*sel.apply(lookup_last, axis=1))
    ok = sel.dropna(subset=['last_credit','expiry_close']).copy()
    ok['width']    = ok['net_credit'] + ok['max_loss']
    ok['pnl_last'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['last_credit'], r['width']-r['last_credit'], r['spread_type']), axis=1) * 100
    return ok


def stats(ok):
    n = len(ok); tot = ok['pnl_last'].sum(); mu = ok['pnl_last'].mean() if n else 0
    win = 100*(ok['pnl_last']>0).mean() if n else 0
    daily = ok.groupby('entry_date')['pnl_last'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


print('\n\n══ Per-year totals (Mon+Thu, hold-to-Friday, top-5, 1.00×LAST) ══')
header = f'{"thr":>7} | ' + ' | '.join([f'{y} (tr/$/Sh)' for y in YEARS]) + ' || combined'
print(header)
print('-' * len(header))
combined_rows = []
for thr in THRESHOLDS:
    parts = [f'{thr:>7.4f}']
    okList = []
    for year in YEARS:
        ok = run_threshold(year_scored[year], thr)
        okList.append(ok)
        n, tot, mu, win, sh = stats(ok)
        parts.append(f'{n:>4} ${tot:>+7,.0f} {sh:>+5.2f}')
    all_ok = pd.concat(okList, ignore_index=True)
    n, tot, mu, win, sh = stats(all_ok)
    parts.append(f' || {n:>4} ${tot:>+8,.0f} ${mu:>+6.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    print(' | '.join(parts))
    combined_rows.append((thr, n, tot, mu, win, sh))

print('\n\n══ Combined 2022-2025 summary ══')
print(f'{"threshold":<10} {"trades":>7} {"profit":>11} {"$/tr":>8} {"win %":>7} {"Sharpe":>8}')
print('-'*55)
for thr, n, tot, mu, win, sh in combined_rows:
    print(f'{thr:<10.4f} {n:>7} ${tot:>+9,.0f} ${mu:>+6.2f} {win:>6.1f}% {sh:>+8.2f}')
