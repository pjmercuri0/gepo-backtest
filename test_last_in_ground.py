"""Re-score GROUND using LAST in net_credit instead of MID, compare to canon.

Canonical pipeline computes net_credit = (short_bid+short_ask)/2 − (long_bid+long_ask)/2,
then b = credit/max_loss feeds GROUND/Kelly. This script tests scoring with
LAST instead: net_credit = short_last − long_last (skip if either leg LAST≤0).

Implementation trick: monkey-patch BidPrice and AskPrice to LastPrice on
each row. spreads.build_candidates then computes "mid" = (LAST+LAST)/2 = LAST
without modification.

Filter set (canon as of 2026-05-30): Mon+Thu entries, top-5 ∧ GROUND≥0.001,
regime filter on, vol-gate OFF, LAST credit at expiry.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'


def filter_and_pnl(scored, df_idx_view, expiry_close):
    sel = scored[scored['GROUND'] >= 0.001]
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()
    # Vol gate OFF — new canon

    def lookup_last(row):
        pc = 'put' if row['spread_type'] == 'bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            last_cr = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
            return last_cr, expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['last_credit'], sel['expiry_close'] = zip(*sel.apply(lookup_last, axis=1))
    ok = sel.dropna(subset=['last_credit','expiry_close']).copy()
    ok['width']    = ok['net_credit'] + ok['max_loss']
    ok['pnl_last'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['last_credit'], r['width']-r['last_credit'], r['spread_type']), axis=1) * 100
    return ok


def score_and_filter(df, df_idx_view, expiry_close, basis):
    """basis='mid' (canon) or 'last' (use LAST in net_credit)."""
    df = df.copy()
    if basis == 'last':
        # Drop rows without a real LAST trade
        df = df[df['LastPrice'].astype(float) > 0]
        # Monkey-patch BidPrice = AskPrice = LastPrice so spreads.build_candidates
        # computes net_credit = (LAST+LAST)/2 − (LAST+LAST)/2 = LAST_short − LAST_long
        df['BidPrice'] = df['LastPrice']
        df['AskPrice'] = df['LastPrice']
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

    candidates = spreads.build_candidates(df)
    if candidates.empty:
        return pd.DataFrame()
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])
    return filter_and_pnl(scored, df_idx_view, expiry_close)


def fmt(label, ok):
    n = len(ok); tot = ok['pnl_last'].sum(); mu = ok['pnl_last'].mean() if n else 0
    win = 100*(ok['pnl_last']>0).mean() if n else 0
    daily = ok.groupby('entry_date')['pnl_last'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return f'{label:<28} {n:>5} ${tot:>+9,.0f} ${mu:>+7.2f} {win:>6.1f}% Sh{sh:>+5.2f}'


tot_mid = []; tot_last = []
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
    df = df[df['dow'].isin([0, 3])]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    t0 = time.time()
    okMID  = score_and_filter(df, df_idx_view, expiry_close, basis='mid')
    print(f'  MID-scored:  {len(okMID):,} picks ({time.time()-t0:.0f}s)', flush=True)
    t0 = time.time()
    okLAST = score_and_filter(df, df_idx_view, expiry_close, basis='last')
    print(f'  LAST-scored: {len(okLAST):,} picks ({time.time()-t0:.0f}s)', flush=True)
    tot_mid.append(okMID); tot_last.append(okLAST)

    print('  ' + fmt('MID-scored (current canon)',  okMID))
    print('  ' + fmt('LAST-scored (test variant)',  okLAST))

print(f'\n══ 2022-2025 combined SP100 Mon+Thu, top-5 ∧ GROUND≥0.001, no vol gate, LAST credit ══')
print(f'{"variant":<28} {"trades":>5} {"profit":>10} {"$/tr":>8} {"win %":>7} {"Sharpe":>9}')
print('-'*70)
print(fmt('MID-scored (current canon)',  pd.concat(tot_mid,  ignore_index=True)))
print(fmt('LAST-scored (test variant)',  pd.concat(tot_last, ignore_index=True)))
