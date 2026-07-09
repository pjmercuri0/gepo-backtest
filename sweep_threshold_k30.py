"""Per-day GROUND threshold sweep at k=30. Find new optimal thresholds.
Mon-Thu, top-5, regime ON, vol gate OFF, LAST-scored (clamped to BBO),
0.85×clamped LAST realized.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 30
THRESHOLDS = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.010, 0.020, 0.030, 0.050]
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
    # Recompute GROUND with k=30
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


year_cache = []
for year in YEARS:
    print(f'── scoring {year} (k={K_VAL}) ──', flush=True)
    year_cache.append(score_year(year))


print(f'\n══ Per-day × per-threshold sweep at k={K_VAL} (Mon-Thu, 0.85×clamped LAST) ══')
results = {dow: {} for dow in [0,1,2,3]}
for dow in [0,1,2,3]:
    print(f'\n── {DOW_NAMES[dow]} only ──')
    print(f'{"threshold":<10} {"trades":>5} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
    print('-'*54)
    for thr in THRESHOLDS:
        all_year = []
        for scored, df_idx_view, expiry_close in year_cache:
            sub = scored[pd.to_datetime(scored['entry_date']).dt.dayofweek == dow]
            qual = sub[sub['GROUND'] >= thr]
            top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
            all_year.append(realize(top, df_idx_view, expiry_close))
        all_ok = pd.concat(all_year, ignore_index=True)
        n,tot,mu,win,sh = stats(all_ok)
        results[dow][thr] = (n, tot, sh)
        print(f'{thr:<10.4f} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')


print(f'\n══ Best threshold per day at k={K_VAL} (by Sharpe with ≥100 trades) ══')
best_by_dow = {}
for dow in [0,1,2,3]:
    # Filter to thresholds with at least 100 trades to avoid tiny-sample noise
    candidates = [(thr, res) for thr, res in results[dow].items() if res[0] >= 100]
    if not candidates:
        candidates = list(results[dow].items())
    best = max(candidates, key=lambda x: x[1][2])
    best_by_dow[dow] = best[0]
    n, tot, sh = best[1]
    print(f'  {DOW_NAMES[dow]}: thr {best[0]:.4f}  →  {n} tr, ${tot:+,.0f}, Sh{sh:+.2f}')


print(f'\n══ Combined backtest at k={K_VAL}: best-per-day thresholds ══')
all_year = []
for scored, df_idx_view, expiry_close in year_cache:
    sel_parts = []
    s = scored.copy()
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    for dow in [0,1,2,3]:
        thr = best_by_dow[dow]
        sub = s[s['entry_dow'] == dow]
        qual = sub[sub['GROUND'] >= thr]
        top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        sel_parts.append(top)
    combined = pd.concat(sel_parts, ignore_index=True)
    all_year.append(realize(combined, df_idx_view, expiry_close))
all_ok = pd.concat(all_year, ignore_index=True)
n,tot,mu,win,sh = stats(all_ok)
print(f'  Mon={best_by_dow[0]:.4f}, Tue={best_by_dow[1]:.4f}, Wed={best_by_dow[2]:.4f}, Thu={best_by_dow[3]:.4f}')
print(f'  → {n} tr, ${tot:+,.0f}, ${mu:+.2f}/tr, {win:.1f}% win, Sharpe {sh:+.2f}')
