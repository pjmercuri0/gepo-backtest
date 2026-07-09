"""Audit the LAST-scoring backtest for look-ahead and other biases.

Tests:
  1. LAST=0 prevalence: how much of the universe gets filtered out?
  2. MID-top-5 vs LAST-top-5 overlap per day: are they picking the same spreads?
  3. Spot-check: pick a high-GROUND LAST-only pick and inspect its data.
  4. P&L of the marginal picks (in LAST top-5 but NOT MID top-5).
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEAR  = 2024  # representative year
SPY_CSV = 'data/spy_us_d.csv'


def _build_scored(df_full, basis):
    """Score one year of data using either MID or LAST in net_credit.

    spreads.py currently uses LastPrice for net_credit (canonical LAST scoring
    as of 2026-05-30). For a TRUE MID comparison we must trick spreads.py by
    overwriting LastPrice with the MID value — then net_credit = (mid - mid) =
    same as MID-based scoring. For LAST we leave LastPrice as-is.
    """
    df = df_full.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    if basis == 'mid':
        # Force spreads.py to compute net_credit from mid by stuffing mid into LastPrice
        df['LastPrice'] = df['MidPrice']
    elif basis == 'last':
        # Drop rows without a real LAST trade; spreads.py uses real LastPrice as-is
        df = df[df['LastPrice'].astype(float) > 0].copy()

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
    return scored


def _top5(scored):
    qual = scored[scored['GROUND'] >= 0.001]
    return qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)


def _key(row):
    return (row['entry_date'], row['ticker'], row['short_strike'], row['spread_type'])


print(f'═══ AUDIT: {YEAR} SP100 LAST-scoring ═══\n')

print('Loading parquet...')
df_full = pd.read_parquet(f'output/{YEAR}_sp500_last.parquet')
df_full = df_full[df_full['Symbol'].isin(SP100)]
n_total = len(df_full)
print(f'  {n_total:,} rows ({df_full["Symbol"].nunique()} tickers)')

# Filter to entry-eligible rows (Mon-Thu, Fri-expiry, DTE 1-4)
df_full['dow']     = df_full['DataDate'].dt.dayofweek
df_full['exp_dow'] = df_full['ExpirationDate'].dt.dayofweek
df_full = df_full[df_full['dow'].isin([0,1,2,3])]
df_full = df_full[df_full['exp_dow']==4]
df_full = df_full[(df_full['DTE']>=1)&(df_full['DTE']<=4)]
n_eligible = len(df_full)
print(f'  {n_eligible:,} entry-eligible rows')

# Setup spreads config (same as canonical)
spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
spreads.REGIME_FILTER = True
spreads.REGIME_PER_TICKER = False
spreads.GAP_FILTER = False
spreads.LOW_VIX_BULLPUT_FILTER = False
spreads.SLIPPAGE_CENTS = 0.0
bt_config.MIN_OPEN_INTEREST = 100

# ═══ TEST 1: LAST=0 prevalence ═══
print(f'\n── TEST 1: LAST=0 prevalence ──')
n_last_zero = (df_full['LastPrice'].astype(float) <= 0).sum()
n_last_pos  = (df_full['LastPrice'].astype(float) > 0).sum()
print(f'  rows with LAST > 0:  {n_last_pos:>8,} ({100*n_last_pos/n_eligible:.1f}%)')
print(f'  rows with LAST <= 0: {n_last_zero:>8,} ({100*n_last_zero/n_eligible:.1f}%)')
print(f'  → LAST filter drops {100*n_last_zero/n_eligible:.1f}% of the universe')

# ═══ Score both ways ═══
print(f'\nScoring with MID...')
scored_mid  = _build_scored(df_full, 'mid')
print(f'  MID-scored: {len(scored_mid):,} candidates')
print(f'Scoring with LAST...')
scored_last = _build_scored(df_full, 'last')
print(f'  LAST-scored: {len(scored_last):,} candidates')

top5_mid  = _top5(scored_mid)
top5_last = _top5(scored_last)
print(f'\n  MID-top-5 picks:  {len(top5_mid):,}')
print(f'  LAST-top-5 picks: {len(top5_last):,}')

# ═══ TEST 2: per-day overlap ═══
print(f'\n── TEST 2: MID vs LAST top-5 overlap per day ──')
mid_keys  = set(top5_mid.apply(_key, axis=1))
last_keys = set(top5_last.apply(_key, axis=1))
overlap   = mid_keys & last_keys
print(f'  picks in both:      {len(overlap):,}')
print(f'  MID-only picks:     {len(mid_keys - last_keys):,}')
print(f'  LAST-only picks:    {len(last_keys - mid_keys):,}')
print(f'  overlap ratio (jaccard): {len(overlap)/len(mid_keys | last_keys)*100:.1f}%')

# ═══ TEST 3: spot-check a LAST-only pick with high GROUND ═══
print(f'\n── TEST 3: spot-check a LAST-only high-GROUND pick ──')
last_only = top5_last[top5_last.apply(_key, axis=1).isin(last_keys - mid_keys)]
last_only_top = last_only.sort_values('GROUND', ascending=False).head(3)
for _, p in last_only_top.iterrows():
    pc = 'put' if p['spread_type']=='bull_put' else 'call'
    raw = df_full[(df_full['DataDate']==p['entry_date']) &
                  (df_full['Symbol']==p['ticker']) &
                  (df_full['ExpirationDate']==p['expiry_date']) &
                  (df_full['StrikePrice'].isin([p['short_strike'], p['long_strike']])) &
                  (df_full['PutCall']==pc)]
    print(f'\n  [{p["entry_date"].date()}] {p["ticker"]} {p["spread_type"]} {p["short_strike"]}/{p["long_strike"]}  GROUND={p["GROUND"]:.4f}')
    for _, r in raw.iterrows():
        role = 'SHORT' if r['StrikePrice']==p['short_strike'] else 'LONG '
        bp = float(r['BidPrice']); ap = float(r['AskPrice']); lp = float(r['LastPrice'])
        mid = (bp+ap)/2
        print(f'    {role} k={r["StrikePrice"]}: bid={bp:.3f} ask={ap:.3f} mid={mid:.3f} LAST={lp:.3f}  '
              f'LAST/mid={lp/mid if mid>0 else 0:.2f}x')

# ═══ TEST 4: realized P&L of LAST-only picks vs MID-only picks ═══
print(f'\n── TEST 4: P&L of marginal picks (LAST-only vs MID-only) ──')

expiry_close = (pd.read_parquet(f'output/{YEAR}_sp500_last.parquet')
                .pipe(lambda d: d[d['DataDate']==d['ExpirationDate']])
                .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                .first().to_dict())
df_idx_view = (pd.read_parquet(f'output/{YEAR}_sp500_last.parquet')
               .pipe(lambda d: d[d['Symbol'].isin(SP100)])
               .set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall'])
               .sort_index()[['LastPrice','BidPrice','AskPrice']])

def realize(top, label):
    rows = []
    for _, p in top.iterrows():
        pc = 'put' if p['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(p['entry_date'], p['ticker'], p['expiry_date'], p['short_strike'], pc)]
            lr = df_idx_view.loc[(p['entry_date'], p['ticker'], p['expiry_date'], p['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            raw_last = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
            ec = expiry_close.get((p['ticker'], p['expiry_date']))
            if ec is None: continue
            credit = raw_last * 0.85
            width  = p['net_credit'] + p['max_loss']
            pnl = spreads.calc_pnl(ec, p['short_strike'], p['long_strike'],
                                   credit, width-credit, p['spread_type']) * 100
            rows.append(pnl)
        except KeyError:
            continue
    if not rows: return None
    arr = np.array(rows)
    print(f'  {label}: {len(arr)} picks, sum ${arr.sum():+,.0f}, mean ${arr.mean():+.2f}, win {100*(arr>0).mean():.1f}%')
    return arr.sum()

mid_only  = top5_mid[top5_mid.apply(_key, axis=1).isin(mid_keys - last_keys)]
last_only = top5_last[top5_last.apply(_key, axis=1).isin(last_keys - mid_keys)]
both_mid  = top5_mid[top5_mid.apply(_key, axis=1).isin(overlap)]
both_last = top5_last[top5_last.apply(_key, axis=1).isin(overlap)]

realize(both_mid,  'overlap (in both MID and LAST top-5)')
realize(mid_only,  'MID-only picks (dropped by LAST)')
realize(last_only, 'LAST-only picks (new under LAST)')
