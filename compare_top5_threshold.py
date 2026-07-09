"""Compare top-5 with vs without the GROUND >= 0.001 threshold for 2022-2025.

Variant A (canonical): top-5 per day AND GROUND >= 0.001
Variant B (top-5 only):  top-5 per day, no threshold floor

Same Mon-Thu DTE 1-4, Fri-expiry, vol-gate, LAST credit, SP100 universe.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)

YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'

# Pre-compute SPY rv_20 once
spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy['rv20'] = spy['Close'].pct_change().rolling(20).std() * np.sqrt(252) * 100
spy_idx = pd.DatetimeIndex(spy['Date'])
def rv_for(d):
    pos = spy_idx.searchsorted(d, side='right') - 1
    return float(spy.iloc[pos]['rv20']) if pos >= 0 else None


def evaluate(scored, df_idx_view, expiry_close, threshold_floor, top_n=5):
    """Apply ranking + vol-gate + LAST lookup; return DataFrame with pnl_last."""
    if threshold_floor is not None:
        sel = scored[scored['GROUND'] >= threshold_floor]
    else:
        sel = scored
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(top_n)

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
            last_cr  = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
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


def fmt(label, ok):
    n = len(ok); tot = ok['pnl_last'].sum(); mu = ok['pnl_last'].mean() if n else 0
    win = 100*(ok['pnl_last']>0).mean() if n else 0
    daily = ok.groupby('entry_date')['pnl_last'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return f'{label:<28} {n:>6} ${tot:>+10,.0f} ${mu:>+8.2f} {win:>6.1f}% {sh:>+7.2f}'


totals = {'A': [], 'B': [], 'C': []}
for year in YEARS:
    pq = f'output/{year}_sp500_last.parquet'
    print(f'\n══ {year} SP100 ══', flush=True)
    print(f'loading {pq}...', flush=True)
    t0 = time.time()
    df_full = pd.read_parquet(pq)
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    print(f'  {len(df_full):,} rows, {df_full["Symbol"].nunique()} tickers (SP100 filtered, {time.time()-t0:.0f}s)', flush=True)

    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]

    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[(df['dow']>=0)&(df['dow']<=3)]
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

    print('  building candidates...', flush=True); t0 = time.time()
    candidates = spreads.build_candidates(df)
    print(f'    {len(candidates):,} in {time.time()-t0:.0f}s', flush=True)

    print('  scoring GROUND...', flush=True); t0 = time.time()
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])
    print(f'    {len(scored):,} in {time.time()-t0:.0f}s', flush=True)

    okA = evaluate(scored, df_idx_view, expiry_close, threshold_floor=0.001, top_n=5)
    okB = evaluate(scored, df_idx_view, expiry_close, threshold_floor=None,  top_n=5)
    okC = evaluate(scored, df_idx_view, expiry_close, threshold_floor=0.001, top_n=10)
    totals['A'].append(okA); totals['B'].append(okB); totals['C'].append(okC)

    print(f'\n  {"variant":<28} {"trades":>6} {"profit $":>11} {"$/trade":>9} {"win %":>7} {"sharpe":>7}')
    print('  ' + '-'*72)
    print('  ' + fmt(f'A: top-5 ∧ GROUND≥0.001',  okA))
    print('  ' + fmt(f'B: top-5 (no threshold)',  okB))
    print('  ' + fmt(f'C: top-10 ∧ GROUND≥0.001', okC))

print(f'\n══ 2022-2025 combined SP100 ══')
print(f'{"variant":<28} {"trades":>6} {"profit $":>11} {"$/trade":>9} {"win %":>7} {"sharpe":>7}')
print('-'*74)
allA = pd.concat(totals['A'], ignore_index=True)
allB = pd.concat(totals['B'], ignore_index=True)
allC = pd.concat(totals['C'], ignore_index=True)
print(fmt('A: top-5 ∧ GROUND≥0.001',  allA))
print(fmt('B: top-5 (no threshold)',  allB))
print(fmt('C: top-10 ∧ GROUND≥0.001', allC))

# Marginal picks: those in B but not in A
print(f'\n══ Marginal picks (in B, not in A): GROUND < 0.001 ══')
marginalA = allA[['entry_date','ticker','short_strike','spread_type']].apply(tuple, axis=1)
marginalB = allB[['entry_date','ticker','short_strike','spread_type']].apply(tuple, axis=1)
extra = allB[~marginalB.isin(set(marginalA))]
print(f'  {len(extra):,} extra trades; total ${extra["pnl_last"].sum():+,.0f}; '
      f'mean ${extra["pnl_last"].mean():+.2f}; win {100*(extra["pnl_last"]>0).mean():.1f}%')
