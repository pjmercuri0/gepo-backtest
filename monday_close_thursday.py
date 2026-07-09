"""Monday entries (DTE=4, Fri expiry), top-5 ∧ GROUND≥0.001, SP100.

Compares 3 exit strategies:
  A. Hold to Friday expiry (current behavior, 1.00×LAST open)
  B. Close Thursday EOD at LAST (1.00×LAST open, 1.00×LAST close — ideal)
  C. Close Thursday EOD with realistic fill (0.95×LAST open, 1.05×LAST close)

For C, the 5% haircut on entry credit and 5% premium on close debit mirror
the live limit-order discipline (you pay slightly above LAST to get filled
on a closing buy, just like you accept slightly below LAST on an opening sell).
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


def fmt(label, df, pnl_col):
    n = len(df); tot = df[pnl_col].sum(); mu = df[pnl_col].mean() if n else 0
    win = 100*(df[pnl_col]>0).mean() if n else 0
    daily = df.groupby('entry_date')[pnl_col].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return f'{label:<40} {n:>5} ${tot:>+9,.0f} ${mu:>+7.2f} {win:>6.1f}% {sh:>+7.2f}'


tot_A = []; tot_B = []; tot_C = []
for year in YEARS:
    pq = f'output/{year}_sp500_last.parquet'
    print(f'\n══ {year} SP100 Mon-only ══', flush=True)
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
    # Monday only
    df = df[df['dow'] == 0]
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

    sel = scored[scored['GROUND'] >= 0.001]
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()
    sel['rv20'] = pd.to_datetime(sel['entry_date']).apply(rv_for)
    # Mondays are never vol-gated, so this is a no-op, but kept for symmetry
    sel = sel[~((pd.to_datetime(sel['entry_date']).dt.dayofweek != 0) & (sel['rv20'] >= 20.0))]

    # Look up entry LAST credit (Mon EOD), Thursday LAST debit, and Friday close
    def lookup_all(row):
        pc = 'put' if row['spread_type'] == 'bull_put' else 'call'
        entry_d  = pd.Timestamp(row['entry_date'])
        expiry_d = pd.Timestamp(row['expiry_date'])
        # Thursday = expiry − 1 day (Friday → Thursday). Skip if Thursday is a holiday.
        thursday_d = expiry_d - pd.Timedelta(days=1)
        try:
            es = df_idx_view.loc[(entry_d, row['ticker'], expiry_d, row['short_strike'], pc)]
            el = df_idx_view.loc[(entry_d, row['ticker'], expiry_d, row['long_strike'],  pc)]
            if isinstance(es, pd.DataFrame): es = es.iloc[0]
            if isinstance(el, pd.DataFrame): el = el.iloc[0]
            entry_credit = max(float(es['LastPrice']) - float(el['LastPrice']), 0)
        except KeyError:
            return None, None, None
        try:
            ts = df_idx_view.loc[(thursday_d, row['ticker'], expiry_d, row['short_strike'], pc)]
            tl = df_idx_view.loc[(thursday_d, row['ticker'], expiry_d, row['long_strike'],  pc)]
            if isinstance(ts, pd.DataFrame): ts = ts.iloc[0]
            if isinstance(tl, pd.DataFrame): tl = tl.iloc[0]
            thursday_debit = max(float(ts['LastPrice']) - float(tl['LastPrice']), 0)
        except KeyError:
            thursday_debit = None
        exp_close = expiry_close.get((row['ticker'], expiry_d))
        return entry_credit, thursday_debit, exp_close

    sel[['entry_last_cr','thu_last_db','exp_close']] = sel.apply(
        lambda r: pd.Series(lookup_all(r)), axis=1)
    ok = sel.dropna(subset=['entry_last_cr','exp_close']).copy()
    # Picks where Thursday quotes missing are dropped from B/C only
    ok['width'] = ok['net_credit'] + ok['max_loss']

    # A: hold to Friday (open at LAST, expiry payoff)
    ok['pnl_A'] = ok.apply(lambda r: spreads.calc_pnl(
        r['exp_close'], r['short_strike'], r['long_strike'],
        r['entry_last_cr'], r['width']-r['entry_last_cr'], r['spread_type']), axis=1) * 100

    # B: close Thursday EOD at LAST (open + close at 1.00×LAST)
    ok_thu = ok.dropna(subset=['thu_last_db']).copy()
    ok_thu['pnl_B'] = (ok_thu['entry_last_cr'] - ok_thu['thu_last_db']) * 100

    # C: realistic — 0.95×LAST entry credit, 1.05×LAST close debit
    ok_thu['pnl_C'] = (ok_thu['entry_last_cr'] * 0.95
                       - ok_thu['thu_last_db'] * 1.05) * 100

    tot_A.append(ok); tot_B.append(ok_thu[['entry_date','pnl_B']]); tot_C.append(ok_thu[['entry_date','pnl_C']])

    print(f'\n  {"variant":<40} {"trades":>5} {"profit $":>10} {"$/trade":>8} {"win %":>7} {"sharpe":>7}')
    print('  ' + '-'*82)
    print('  ' + fmt('A: hold to Friday (1.00×LAST)',         ok,     'pnl_A'))
    print('  ' + fmt('B: close Thu EOD (1.00×LAST both)',     ok_thu, 'pnl_B'))
    print('  ' + fmt('C: close Thu EOD (0.95 open / 1.05 close)', ok_thu, 'pnl_C'))
    if len(ok) != len(ok_thu):
        print(f'  [{len(ok)-len(ok_thu)} picks missing Thursday quotes — held to Friday only in A]', flush=True)


print(f'\n══ 2022-2025 combined SP100 Mon-only, top-5 ∧ GROUND≥0.001 ══')
print(f'{"variant":<40} {"trades":>5} {"profit $":>10} {"$/trade":>8} {"win %":>7} {"sharpe":>7}')
print('-'*84)
allA = pd.concat(tot_A,  ignore_index=True)
allB = pd.concat(tot_B,  ignore_index=True)
allC = pd.concat(tot_C,  ignore_index=True)
print(fmt('A: hold to Friday (1.00×LAST)',         allA, 'pnl_A'))
print(fmt('B: close Thu EOD (1.00×LAST both)',     allB, 'pnl_B'))
print(fmt('C: close Thu EOD (0.95 open / 1.05 close)', allC, 'pnl_C'))
