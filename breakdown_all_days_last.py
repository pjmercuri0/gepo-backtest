"""LAST-scored canon, breakdown by Mon/Tue/Wed/Thu at 0.80×/0.90×/1.00× LAST.
Friday expiry only, top-5 ∧ GROUND≥0.001, regime ON, vol gate OFF, SP100.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
MULTIPLIERS = [0.80, 0.90, 1.00]
DOW_NAMES = {0:'Mon', 1:'Tue', 2:'Wed', 3:'Thu'}


def filter_and_pnl(scored, df_idx_view, expiry_close, credit_mult):
    sel = scored[scored['GROUND'] >= 0.001]
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()
    def lookup_last(row):
        pc = 'put' if row['spread_type'] == 'bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            return max(float(sr['LastPrice']) - float(lr['LastPrice']), 0), expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_last'], sel['expiry_close'] = zip(*sel.apply(lookup_last, axis=1))
    ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last'] * credit_mult
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    ok['dow'] = pd.to_datetime(ok['entry_date']).dt.dayofweek
    return ok


def stats(ok):
    n = len(ok); tot = ok['pnl'].sum(); mu = ok['pnl'].mean() if n else 0
    win = 100*(ok['pnl']>0).mean() if n else 0
    daily = ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


year_cache = {}
for year in YEARS:
    pq = f'output/{year}_sp500_last.parquet'
    print(f'\n── {year} scoring ──', flush=True)
    df_full = pd.read_parquet(pq)
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin([0, 1, 2, 3])]  # Mon-Thu
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df.copy()
    df['BidPrice'] = df['LastPrice']; df['AskPrice'] = df['LastPrice']
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
    scored = scored.dropna(subset=['GROUND'])
    year_cache[year] = (scored, df_idx_view, expiry_close)
    print(f'  done', flush=True)


print(f'\n══ Per-day combined 2022-2025 ══')
for m in MULTIPLIERS:
    print(f'\n── {m:.2f}×LAST ──')
    print(f'{"day":<5} {"trades":>5} {"profit":>9} {"$/tr":>8} {"win %":>7} {"Sharpe":>9}')
    print('-'*55)
    all_ok = pd.concat([filter_and_pnl(*year_cache[y], m) for y in YEARS], ignore_index=True)
    for d in [0, 1, 2, 3]:
        sub = all_ok[all_ok['dow']==d]
        n,tot,mu,win,sh = stats(sub)
        print(f'{DOW_NAMES[d]:<5} {n:>5} ${tot:>+7,.0f} ${mu:>+6.2f} {win:>6.1f}% Sh{sh:>+5.2f}')
    n,tot,mu,win,sh = stats(all_ok)
    print(f'{"ALL":<5} {n:>5} ${tot:>+7,.0f} ${mu:>+6.2f} {win:>6.1f}% Sh{sh:>+5.2f}')
