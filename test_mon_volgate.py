"""Test: gate Monday too at SPY rv_20 ≥ 20?
Mon+Thu, hold to Friday, top-5 ∧ GROUND≥0.001, SP100, 1.00×LAST.

A: current (Mon always trades; Tue-Thu gated)  ← canonical
B: Mon also gated at rv_20 ≥ 20
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


def evaluate(scored, df_idx_view, expiry_close, mode):
    """mode = 'A' canonical | 'B' gate-all | 'C' no-gate"""
    sel = scored[scored['GROUND'] >= 0.001]
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()
    sel['rv20'] = pd.to_datetime(sel['entry_date']).apply(rv_for)
    sel['dow']  = pd.to_datetime(sel['entry_date']).dt.dayofweek
    if mode == 'B':
        sel = sel[sel['rv20'] < 20.0]                                 # every day gated
    elif mode == 'A':
        sel = sel[~((sel['dow']!=0) & (sel['rv20']>=20.0))]           # canonical: Mon ungated
    elif mode == 'C':
        pass                                                           # no gate
    else:
        raise ValueError(mode)

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


def stats(ok, pnl='pnl_last'):
    n = len(ok); tot = ok[pnl].sum(); mu = ok[pnl].mean() if n else 0
    win = 100*(ok[pnl]>0).mean() if n else 0
    daily = ok.groupby('entry_date')[pnl].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


def fmt(label, ok):
    n, tot, mu, win, sh = stats(ok)
    return f'{label:<32} {n:>5} ${tot:>+9,.0f} ${mu:>+7.2f} {win:>6.1f}% Sh{sh:>+5.2f}'


tot_A = []; tot_B = []; tot_C = []
for year in YEARS:
    pq = f'output/{year}_sp500_last.parquet'
    print(f'\n══ {year} SP100 Mon+Thu ══', flush=True)
    df_full = pd.read_parquet(pq)
    df_full = df_full[df_full['Symbol'].isin(SP100)]

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

    okA = evaluate(scored, df_idx_view, expiry_close, mode='A')
    okB = evaluate(scored, df_idx_view, expiry_close, mode='B')
    okC = evaluate(scored, df_idx_view, expiry_close, mode='C')
    tot_A.append(okA); tot_B.append(okB); tot_C.append(okC)

    print('  ' + fmt('A (canonical: Mon ungated)',  okA))
    print('  ' + fmt('B (all days gated)',          okB))
    print('  ' + fmt('C (no gate at all)',          okC))
    # Thu high-vol re-included in C vs A:
    thuA = okA[pd.to_datetime(okA['entry_date']).dt.dayofweek == 3]
    thuC = okC[pd.to_datetime(okC['entry_date']).dt.dayofweek == 3]
    extra_thu = thuC[~thuC.set_index(['entry_date','ticker','short_strike']).index.isin(
                     thuA.set_index(['entry_date','ticker','short_strike']).index)]
    print(f'    Thu re-included by C (high-vol Thursdays):  '
          f'{len(extra_thu):>3} tr  ${extra_thu["pnl_last"].sum():>+7,.0f}', flush=True)


print(f'\n══ 2022-2025 combined SP100 Mon+Thu, top-5 ∧ GROUND≥0.001, 1.00×LAST ══')
print(f'{"variant":<32} {"trades":>5} {"profit":>10} {"$/tr":>8} {"win %":>7} {"Sharpe":>9}')
print('-'*74)
allA = pd.concat(tot_A, ignore_index=True)
allB = pd.concat(tot_B, ignore_index=True)
allC = pd.concat(tot_C, ignore_index=True)
print(fmt('A (canonical: Mon ungated)', allA))
print(fmt('B (all days gated)',         allB))
print(fmt('C (no gate at all)',         allC))

# Sub-analyses: which Mondays / Thursdays each variant added or dropped
mA = allA[pd.to_datetime(allA['entry_date']).dt.dayofweek == 0]
mB = allB[pd.to_datetime(allB['entry_date']).dt.dayofweek == 0]
dropped_mons = mA[~mA.set_index(['entry_date','ticker','short_strike']).index.isin(
                  mB.set_index(['entry_date','ticker','short_strike']).index)]
print(f'\nB drops these high-vol Mondays: {len(dropped_mons)} tr  '
      f'${dropped_mons["pnl_last"].sum():+,.0f}  mean ${dropped_mons["pnl_last"].mean():+.2f}')

tA = allA[pd.to_datetime(allA['entry_date']).dt.dayofweek == 3]
tC = allC[pd.to_datetime(allC['entry_date']).dt.dayofweek == 3]
added_thus = tC[~tC.set_index(['entry_date','ticker','short_strike']).index.isin(
                tA.set_index(['entry_date','ticker','short_strike']).index)]
print(f'C adds these high-vol Thursdays:  {len(added_thus)} tr  '
      f'${added_thus["pnl_last"].sum():+,.0f}  mean ${added_thus["pnl_last"].mean():+.2f}')
