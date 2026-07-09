"""
2022 backtest on SP500 universe with last-price as credit basis.
Selection at mid (canonical), payoff computed at LAST credit, Mon-Thu entries
with Friday-this-week expiry only (DTE 1-4), with vol-gate (SPY rv20<20% on
non-Mondays) and regime filter (SPY 100d SMA: bull→bull_put only, bear→bear_call only).
"""
import numpy as np, pandas as pd, time
from pathlib import Path
import sys
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

PARQUET = 'output/2022_sp500_last.parquet'
START_BANKROLL = 10000

print(f'Loading {PARQUET}...')
df_full = pd.read_parquet(PARQUET)
print(f'  loaded {len(df_full):,} rows, {df_full["Symbol"].nunique()} tickers')

# Build expiry-close lookup BEFORE filtering (need Friday DataDate==ExpirationDate rows)
expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                .first()
                .to_dict())
print(f'  built expiry_close lookup: {len(expiry_close):,} (Symbol, ExpirationDate) pairs')

# Build option BBO lookup from FULL data (covers all DataDates incl entry days)
df_idx = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()
df_idx_view = df_idx[['LastPrice','BidPrice','AskPrice']]

# Now filter for candidate building only
df = df_full
df['dow'] = df['DataDate'].dt.dayofweek
df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
df = df[(df['dow'] >= 0) & (df['dow'] <= 3)]           # Mon-Thu only
df = df[df['exp_dow'] == 4]                             # Friday expiries only
df = df[(df['DTE'] >= 1) & (df['DTE'] <= 4)]            # DTE 1-4
print(f'  after filter (Mon-Thu, Fri-expiry, DTE 1-4): {len(df):,} rows')

# Derived columns build_candidates expects
df['AbsDelta'] = df['Delta'].abs()
df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

# Set regime filter (SPY 100d SMA)
spy_csv = 'data/spy_us_d.csv'
spreads.REGIME_LOOKUP = spreads.build_regime_lookup(spy_csv, sma_window=100)
spreads.REGIME_FILTER = True
spreads.REGIME_PER_TICKER = False
spreads.GAP_FILTER = False
spreads.LOW_VIX_BULLPUT_FILTER = False
spreads.SLIPPAGE_CENTS = 0.0
bt_config.MIN_OPEN_INTEREST = 100

# Build candidates (canonical, uses MID for credit, applies regime gate)
print('Building candidates...')
t0 = time.time()
candidates = spreads.build_candidates(df)
print(f'  {len(candidates):,} candidates built in {time.time()-t0:.0f}s')

# Score with GROUND
print('Scoring with GROUND...')
t0 = time.time()
scored = ground.score_candidates(candidates)
import math
def gnd(r):
    G, DKL = r.get('G'), r.get('DKL')
    if G is None or DKL is None or pd.isna(G) or pd.isna(DKL):
        return float('nan')
    return (math.exp(G) - 1.0) * math.exp(-ground.DKL_K * DKL)
scored['GROUND'] = scored.apply(gnd, axis=1)
scored = scored.dropna(subset=['GROUND'])
print(f'  {len(scored):,} scored candidates in {time.time()-t0:.0f}s')

# Top-5 per day, GROUND >= 0.001 threshold to match canonical
selected = scored[scored['GROUND'] >= 0.001]
selected = selected.sort_values(['entry_date', 'GROUND'], ascending=[True, False]).groupby('entry_date').head(5)
print(f'  {len(selected):,} top-5 picks across {selected["entry_date"].nunique()} days')

# Vol-gate (skip non-Mondays when SPY rv20 >= 20%)
spy = pd.read_csv('data/spy_us_d.csv', parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy['rv20'] = spy['Close'].pct_change().rolling(20).std() * np.sqrt(252) * 100
spy_idx = pd.DatetimeIndex(spy['Date'])
def rv_for(d):
    pos = spy_idx.searchsorted(d, side='right') - 1
    return float(spy.iloc[pos]['rv20']) if pos >= 0 else None
selected['rv20'] = pd.to_datetime(selected['entry_date']).apply(rv_for)
selected['dow'] = pd.to_datetime(selected['entry_date']).dt.dayofweek
before_gate = len(selected)
selected = selected[~((selected['dow']!=0) & (selected['rv20']>=20.0))]
print(f'  after vol-gate: {len(selected):,} picks (dropped {before_gate-len(selected):,})')

# For each pick, look up LAST credit at entry: short_last - long_last
# Also re-confirm expiry close from same parquet
print('\\nLooking up LAST credit + expiry close for each pick...')

def lookup_last(row):
    pc = 'put' if row['spread_type'] == 'bull_put' else 'call'
    try:
        sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
        lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'], pc)]
        if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
        if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
        last_cr = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
        exp_close = expiry_close.get((row['ticker'], row['expiry_date']))
        return last_cr, exp_close
    except KeyError:
        return None, None

selected['last_credit'], selected['expiry_close'] = zip(*selected.apply(lookup_last, axis=1))
ok = selected.dropna(subset=['last_credit','expiry_close']).copy()
print(f'  matched {len(ok):,}/{len(selected):,} picks with last + close data')

# Compute payoff using corrected formula at BOTH bases
def pnl_share(spot, sp, bp, cr, ml, stype):
    return spreads.calc_pnl(spot, sp, bp, cr, ml, stype)

ok['width'] = ok['net_credit'] + ok['max_loss']
# MID basis (canonical net_credit from selection)
ok['pnl_mid']  = ok.apply(lambda r: pnl_share(r['expiry_close'], r['short_strike'], r['long_strike'],
                                                r['net_credit'], r['max_loss'], r['spread_type']), axis=1) * 100
# LAST basis
ok['pnl_last'] = ok.apply(lambda r: pnl_share(r['expiry_close'], r['short_strike'], r['long_strike'],
                                                r['last_credit'], r['width']-r['last_credit'], r['spread_type']), axis=1) * 100

print(f'\\n══════════════════════════════════════════════════════════════════')
print(f'  2022 SP500 backtest, Mon-Thu DTE 1-4, Fri-expiry, vol-gated')
print(f'══════════════════════════════════════════════════════════════════')
print(f'\\n{"basis":<14} {"trades":>7} {"profit $":>11} {"$/trade":>9} {"win %":>7} {"sharpe":>7}')
print('-'*60)
for label, col, cr_col in [('MID credit',  'pnl_mid',  'net_credit'),
                            ('LAST credit', 'pnl_last', 'last_credit')]:
    n = len(ok)
    tot = ok[col].sum()
    mu  = ok[col].mean()
    win = 100*(ok[col]>0).mean()
    daily = ok.groupby('entry_date')[col].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    print(f'{label:<14} {n:>7} ${tot:>+10,.0f} ${mu:>+8.2f} {win:>6.1f}% {sh:>+7.2f}')

# Average credit comparison
print(f'\\nCredit basis comparison:')
print(f'  mean MID  credit: ${ok["net_credit"].mean():.3f}/share')
print(f'  mean LAST credit: ${ok["last_credit"].mean():.3f}/share')
print(f'  mean diff (LAST-MID): ${(ok["last_credit"]-ok["net_credit"]).mean():+.4f}/share')
print(f'  abs diff > $0.10: {((ok["last_credit"]-ok["net_credit"]).abs()>0.10).sum()} picks')

ok['entry_dow'] = pd.to_datetime(ok['entry_date']).dt.dayofweek
print(f'\\n══ Weekday breakdown (LAST credit, canonical) ══')
print(f'{"DOW":<5} {"DTE":<4} {"trades":>7} {"profit $":>10} {"$/trade":>9} {"win %":>7} {"sharpe":>7}')
print('-'*55)
for d in [0,1,2,3]:
    sub = ok[ok['entry_dow']==d]
    if sub.empty: continue
    dte = sub['DTE'].mode().iloc[0]
    n = len(sub); tot = sub['pnl_last'].sum(); mu = sub['pnl_last'].mean()
    win = 100*(sub['pnl_last']>0).mean()
    daily = sub.groupby('entry_date')['pnl_last'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    name = {0:'Mon',1:'Tue',2:'Wed',3:'Thu'}[d]
    print(f'{name:<5} {int(dte):<4} {n:>7} ${tot:>+9,.0f} ${mu:>+8.2f} {win:>6.1f}% {sh:>+7.2f}')

ok[['entry_date','expiry_date','ticker','spread_type','short_strike','long_strike',
    'net_credit','last_credit','max_loss','width','expiry_close',
    'pnl_mid','pnl_last','GROUND','DTE','entry_dow']].to_csv('output/picks_sp500_2022.csv', index=False)
print(f'\\nSaved picks to output/picks_sp500_2022.csv')
