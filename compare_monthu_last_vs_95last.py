"""Mon+Thu only, top-5 ∧ GROUND≥0.001, SP100, LAST vs 0.95×LAST credit.

Mon entry → DTE=4 to Fri. Thu entry → DTE=1 to Fri.
Vol-gate still applies (skip non-Mon when SPY rv_20≥20).
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'

spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy['rv20'] = spy['Close'].pct_change().rolling(20).std() * np.sqrt(252) * 100
spy_idx = pd.DatetimeIndex(spy['Date'])
def rv_for(d):
    pos = spy_idx.searchsorted(d, side='right') - 1
    return float(spy.iloc[pos]['rv20']) if pos >= 0 else None


def evaluate(scored, df_idx_view, expiry_close, credit_mult):
    sel = scored[scored['GROUND'] >= 0.001]
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
            raw_cr = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
            exp_close = expiry_close.get((row['ticker'], row['expiry_date']))
            return raw_cr, exp_close
        except KeyError:
            return None, None
    sel['raw_last_cr'], sel['expiry_close'] = zip(*sel.apply(lookup_last, axis=1))
    ok = sel.dropna(subset=['raw_last_cr','expiry_close']).copy()
    ok['credit'] = ok['raw_last_cr'] * credit_mult
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    return ok


def fmt(label, ok):
    n = len(ok); tot = ok['pnl'].sum(); mu = ok['pnl'].mean() if n else 0
    win = 100*(ok['pnl']>0).mean() if n else 0
    daily = ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return f'{label:<24} {n:>6} ${tot:>+10,.0f} ${mu:>+8.2f} {win:>6.1f}% {sh:>+7.2f}'


totals_last = []; totals_95last = []
for year in YEARS:
    pq = f'output/{year}_sp500_last.parquet'
    print(f'\n══ {year} SP100 Mon+Thu ══', flush=True)
    df_full = pd.read_parquet(pq)
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    print(f'  {len(df_full):,} rows', flush=True)

    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]

    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    # Mon (0) + Thu (3) only
    df = df[df['dow'].isin([0, 3])]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
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
    print(f'  {len(candidates):,} candidates in {time.time()-t0:.0f}s', flush=True)

    t0 = time.time()
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])
    print(f'  {len(scored):,} scored in {time.time()-t0:.0f}s', flush=True)

    okL  = evaluate(scored, df_idx_view, expiry_close, credit_mult=1.00)
    ok95 = evaluate(scored, df_idx_view, expiry_close, credit_mult=0.95)
    totals_last.append(okL); totals_95last.append(ok95)

    print(f'\n  {"basis":<24} {"trades":>6} {"profit $":>11} {"$/trade":>9} {"win %":>7} {"sharpe":>7}')
    print('  ' + '-'*70)
    print('  ' + fmt('1.00×LAST', okL))
    print('  ' + fmt('0.95×LAST', ok95))

print(f'\n══ 2022-2025 combined SP100 Mon+Thu, top-5 ∧ GROUND≥0.001 ══')
print(f'{"basis":<24} {"trades":>6} {"profit $":>11} {"$/trade":>9} {"win %":>7} {"sharpe":>7}')
print('-'*72)
print(fmt('1.00×LAST', pd.concat(totals_last,  ignore_index=True)))
print(fmt('0.95×LAST', pd.concat(totals_95last, ignore_index=True)))
