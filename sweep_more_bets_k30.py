"""At k=30, sweep top-N and threshold to find more-bets configurations
with respectable Sharpe. Mon-Thu, 0.85×clamped LAST, SP100, 2022-2025.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 30


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
        return (math.exp(G)-1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), df_idx_view, expiry_close


def realize(sel, df_idx_view, expiry_close):
    sel = sel.copy()
    if sel.empty:
        return pd.DataFrame(columns=['entry_date','pnl'])
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


def run(year_cache, top_n, threshold):
    all_year = []
    for scored, df_idx_view, expiry_close in year_cache:
        s = scored[scored['GROUND'] >= threshold]
        s = s.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(top_n)
        all_year.append(realize(s, df_idx_view, expiry_close))
    return pd.concat(all_year, ignore_index=True)


year_cache = []
for year in YEARS:
    print(f'── scoring {year} (k={K_VAL}) ──', flush=True)
    year_cache.append(score_year(year))


print(f'\n══ More-bets sweep at k={K_VAL} (Mon-Thu, 0.85×clamped LAST) ══\n')
configs = [
    ('top-5,  thr 0.0010', 5,  0.001),
    ('top-5,  thr 0.0005', 5,  0.0005),
    ('top-5,  thr 0.0001', 5,  0.0001),
    ('top-10, thr 0.0010', 10, 0.001),
    ('top-10, thr 0.0005', 10, 0.0005),
    ('top-10, thr 0.0001', 10, 0.0001),
    ('top-15, thr 0.0010', 15, 0.001),
    ('top-20, thr 0.0001', 20, 0.0001),
    ('top-50, thr 0.0001 (basket)', 50, 0.0001),
]
print(f'{"config":<32} {"trades":>6} {"profit":>10} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*72)
for label, n, thr in configs:
    ok = run(year_cache, n, thr)
    nn, tot, mu, win, sh = stats(ok)
    print(f'{label:<32} {nn:>6} ${tot:>+8,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
