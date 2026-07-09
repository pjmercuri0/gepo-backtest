"""Sweep DKL_K (GROUND amplification constant) under CURRENT canon.
Mon-Thu, top-5 ∧ GROUND≥0.001, 0.85×clamped LAST, SP100, 2022-2025.
Reveals whether k=20 is special or arbitrary.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
K_VALUES = [1, 5, 10, 15, 20, 25, 30, 50, 100]


# Build + score once per year (G and DKL are k-independent; only GROUND uses k)
year_cache = []
for year in YEARS:
    print(f'── building {year} ──', flush=True)
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
    year_cache.append((scored, df_idx_view, expiry_close))


def filter_and_pnl(scored, df_idx_view, expiry_close, k_val):
    s = scored.copy()
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-k_val * DKL)
    s['GROUND'] = s.apply(gnd, axis=1)
    s = s.dropna(subset=['GROUND'])
    # Per-day thresholds (sweet spot from earlier sweep):
    #   Mon/Tue → 0.005 ; Wed/Thu → 0.003
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    thresh_map = {0: 0.005, 1: 0.005, 2: 0.003, 3: 0.003}
    keep = []
    for dow, group in s.groupby('entry_dow'):
        thr = thresh_map.get(int(dow), 0.001)
        keep.append(group[group['GROUND'] >= thr])
    s = pd.concat(keep, ignore_index=True) if keep else s.iloc[:0]
    s = s.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    s = s.copy()

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
    s['raw_last'], s['expiry_close'] = zip(*s.apply(lookup, axis=1))
    ok = s.dropna(subset=['raw_last','expiry_close']).copy()
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


print(f'\n══ k-sweep (Mon-Thu, top-5 ∧ GROUND≥0.001, 0.85×clamped LAST, SP100, 2022-2025) ══')
print(f'{"k":>4} {"trades":>6} {"profit":>10} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*52)
for k in K_VALUES:
    all_ok = pd.concat([filter_and_pnl(*yc, k) for yc in year_cache], ignore_index=True)
    n, tot, mu, win, sh = stats(all_ok)
    mark = '  ← canon' if k == 20 else ''
    print(f'{k:>4} {n:>6} ${tot:>+8,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}{mark}')
