"""2-week tenor test on 2026: canon pipeline (mid selection, k=10, thr=0.07,
fills 0.80 x mid, G_rv + rv_vs_iv DKL) with entries at DTE 8-11 (Mon-Thu to
NEXT Friday). Compares against the canon DTE 1-4 OOT result.

Data: output/2026_sp500_dte11.parquet (fast_preprocess --dte-max 11).
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 10.0
THR = 0.07
START = 10_000.0
ACTIVE_DOWS = [0, 1, 2, 3]
DTE_MIN, DTE_MAX = 8, 11

bt_config.CREDIT_BASIS = "mid"
bt_config.CREDIT_SCALE = 1.0

IV_RANK_LOOKUP = None
try:
    _ivr = pd.read_parquet('output/iv_rank.parquet')
    _ivr['DataDate'] = pd.to_datetime(_ivr['DataDate'])
    IV_RANK_LOOKUP = _ivr[['Symbol', 'DataDate', 'iv_rank_bucket']]
except FileNotFoundError:
    print('WARNING: iv_rank.parquet missing')

RV_LOOKUP = None
try:
    _rv = pd.read_parquet('output/rv_table.parquet')
    _rv['DataDate'] = pd.to_datetime(_rv['DataDate'])
    RV_LOOKUP = _rv[['Symbol', 'DataDate', 'rv_30d']]
except FileNotFoundError:
    print('WARNING: rv_table.parquet missing')

POOL = er.load_master_pool()
print(f'Loaded master pool: {len(POOL):,} rows', flush=True)

df_full = pd.read_parquet('output/2026_sp500_dte11.parquet')
df_full = df_full[df_full['Symbol'].isin(SP100)]
print(f'{len(df_full):,} rows, DTE {df_full.DTE.min()}-{df_full.DTE.max()}', flush=True)

expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                .first().to_dict())

df = df_full.copy()
df['dow']     = df['DataDate'].dt.dayofweek
df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
df = df[df['dow'].isin(ACTIVE_DOWS)]
df = df[df['exp_dow']==4]
df = df[(df['DTE']>=DTE_MIN)&(df['DTE']<=DTE_MAX)]
df = df[df['LastPrice'].astype(float) > 0]
df = df[df['DataDate'] >= pd.Timestamp('2026-01-01')]
df = df.copy()
df['AbsDelta'] = df['Delta'].abs()
df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
if IV_RANK_LOOKUP is not None:
    df = df.merge(IV_RANK_LOOKUP, on=['Symbol', 'DataDate'], how='left')
if RV_LOOKUP is not None:
    df = df.merge(RV_LOOKUP, on=['Symbol', 'DataDate'], how='left')
print(f'{len(df):,} option rows in DTE {DTE_MIN}-{DTE_MAX} entry window', flush=True)

spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
spreads.REGIME_FILTER = False
spreads.REGIME_PER_TICKER = False
spreads.GAP_FILTER = False
spreads.LOW_VIX_BULLPUT_FILTER = False
spreads.SLIPPAGE_CENTS = 0.0
bt_config.MIN_OPEN_INTEREST = 100

candidates = spreads.build_candidates(df)
print(f'{len(candidates):,} candidates', flush=True)

parts = []
for dt in sorted(candidates['entry_date'].unique()):
    sub = candidates[candidates['entry_date'] == dt]
    if sub.empty: continue
    ok = er.install_window(POOL, pd.Timestamp(dt))
    if not ok:
        import historical_probs as hp
        hp._EMPIRICAL_TABLE = None
    parts.append(ground.score_candidates(sub))
scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
scored = scored.dropna(subset=['G', 'DKL']).copy()
scored['GROUND'] = (np.exp(scored['G']) - 1.0) * np.exp(-K_VAL * scored['DKL'])
print(f'{len(scored):,} scored', flush=True)

sel = (scored[scored['GROUND'] >= THR]
       .sort_values(['entry_date','GROUND'], ascending=[True,False])
       .groupby('entry_date').head(5)).copy()
sel['expiry_close'] = sel.apply(lambda r: expiry_close.get((r['ticker'], r['expiry_date'])), axis=1)
sel = sel.dropna(subset=['expiry_close'])
sel['width'] = sel['net_credit'] + sel['max_loss']
sel['credit'] = sel['net_credit'] * 0.80
sel['ml_adj'] = sel['width'] - sel['credit']
sel['pnl'] = sel.apply(lambda r: spreads.calc_pnl(
    r['expiry_close'], r['short_strike'], r['long_strike'],
    r['credit'], r['ml_adj'], r['spread_type']), axis=1) * 100

def _oc(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
    return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
sel['_oc'] = sel.apply(_oc, axis=1)
mask = (sel['_oc']=='PARTIAL') & (sel['pnl']>0)
sel.loc[mask,'pnl'] *= 0.5

sel['entry_date'] = pd.to_datetime(sel['entry_date'])
spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
spy = spy[(spy['Date']>=sel['entry_date'].min())&(spy['Date']<=pd.Timestamp('2026-12-31'))]
td = pd.DatetimeIndex(spy['Date'])
daily = sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl'].sum()
eq = START + daily.reindex(td, fill_value=0.0).cumsum()
wk = eq.resample('W-FRI').last().ffill().pct_change().dropna()
wsh = float(wk.mean()*np.sqrt(52)/wk.std(ddof=0)) if wk.std(ddof=0)>0 else 0.0
dd = float(((eq-eq.cummax())/eq.cummax()).min())

print('\n=== DTE 8-11 (2-week), 2026, canon k=10 thr=0.07, fills 0.80x mid, qty=1 ===')
print(f'n={len(sel)}  final ${float(eq.iloc[-1]):,.0f}  Sh(wk) {wsh:+.2f}  DD {100*dd:.1f}%  '
      f'win {100*(sel.pnl>0).mean():.1f}%  $/tr {sel.pnl.mean():.2f}')
print(f'credit/width: median {(sel.net_credit/sel.width).median():.2f}  b median {(sel.net_credit/sel.max_loss).median():.2f}')
print('\nreference DTE 1-4 canon OOT: n=121  final $11,753  Sh(wk) +2.76  DD -4.4%  win 57.9%  $/tr 14.49')
print('\noutcomes:', dict(sel['_oc'].value_counts()))
