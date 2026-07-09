"""Compare credit-basis assumptions for the backtest. Goal: find the most
realistic single basis for 15:01 fills on Mon-Thu entries expiring Friday.

Bases tested (in order from optimistic to conservative):
  1.00 × MID                   — theoretical fair value (rarely fills)
  0.95 × MID                   — aggressive limit, sometimes fills
  0.85 × MID                   — moderate limit, usually fills (matches early real-fill memory)
  0.75 × MID                   — previous default live order floor
  NATURAL (short_bid−long_ask) — immediate-fill price, worst case
  0.85 × clamped LAST          — current canon

Selection still uses LAST-scoring (canonical) for all bases — we only vary the
realization side. This isolates the fill-basis assumption from the selection logic.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'


def score_year(year, days):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(days)]
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
        return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])

    sel = scored[scored['GROUND'] >= 0.001]
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()

    def lookup(row):
        pc = 'put' if row['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            sb, sa = float(sr['BidPrice']), float(sr['AskPrice'])
            lb, la = float(lr['BidPrice']), float(lr['AskPrice'])
            sl = max(sb, min(float(sr['LastPrice']), sa))
            ll = max(lb, min(float(lr['LastPrice']), la))
            mid_credit  = (sb+sa)/2 - (lb+la)/2
            nat_credit  = sb - la         # short@bid, long@ask = immediate fill
            last_credit = max(sl - ll, 0) # clamped LAST credit
            ec = expiry_close.get((row['ticker'], row['expiry_date']))
            return mid_credit, nat_credit, last_credit, ec
        except KeyError:
            return None,None,None,None

    sel[['mid_cr','nat_cr','last_cr','ec']] = sel.apply(lambda r: pd.Series(lookup(r)), axis=1)
    ok = sel.dropna(subset=['mid_cr','nat_cr','last_cr','ec']).copy()
    ok['width'] = ok['net_credit'] + ok['max_loss']
    return ok


def pnl(ok, credit_series):
    """Compute total P&L for a given credit basis series."""
    rows = []
    for (_, r), credit in zip(ok.iterrows(), credit_series):
        credit = max(float(credit), 0.0)
        ml = r['width'] - credit
        pnl_share = spreads.calc_pnl(r['ec'], r['short_strike'], r['long_strike'],
                                      credit, ml, r['spread_type'])
        rows.append({'entry_date': r['entry_date'], 'pnl': pnl_share * 100})
    return pd.DataFrame(rows)


def stats(df):
    n = len(df); tot = df['pnl'].sum(); mu = df['pnl'].mean() if n else 0
    win = 100*(df['pnl']>0).mean() if n else 0
    daily = df.groupby('entry_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


print('Loading + scoring all years...')
all_ok_mt  = pd.concat([score_year(y, [0,1,2,3]) for y in YEARS], ignore_index=True)
all_ok_thu = pd.concat([score_year(y, [3])       for y in YEARS], ignore_index=True)
print(f'  Mon-Thu: {len(all_ok_mt):,} picks  |  Thu-only: {len(all_ok_thu):,} picks')


def report(ok, label):
    print(f'\n══ {label} 2022-2025 SP100 ══')
    print(f'{"basis":<26} {"trades":>5} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
    print('-'*64)
    bases = [
        ('1.00 × MID (theoretical)',  ok['mid_cr']),
        ('0.95 × MID (aggressive)',   ok['mid_cr'] * 0.95),
        ('0.85 × MID (moderate)',     ok['mid_cr'] * 0.85),
        ('0.75 × MID (conservative)', ok['mid_cr'] * 0.75),
        ('NATURAL (bid−ask)',         ok['nat_cr']),
        ('0.85 × clamped LAST',       ok['last_cr'] * 0.85),
    ]
    for name, series in bases:
        df = pnl(ok, series)
        n,tot,mu,win,sh = stats(df)
        print(f'  {name:<26} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')


report(all_ok_mt,  'Mon-Thu')
report(all_ok_thu, 'Thu-only')
