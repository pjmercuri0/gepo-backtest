"""Surgically rescore picks for one year and merge into the existing cache.

Use when you've expanded the empirical pool (e.g. added 2021) and want to
refresh just one year's picks without redoing the whole multi-year backtest.

Process:
  1. Load existing picks cache (output/picks_cache_*.parquet)
  2. Drop rows where year(entry_date) == YEAR
  3. Score JUST that year with the current pool (and current canonical
     DKL_REFERENCE / DKL_K / thresholds in ground.py + report_three_sizings)
  4. Realize pnl_per_contract
  5. Append → save cache
  6. Rebuild live/data/backtest_equity.json from the merged cache

Usage:
  python rescore_year.py 2022
"""
import sys, math, json, os, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 50
START_BANKROLL = 10_000.0
THRESH_BY_DOW = {0: 0.030, 1: 0.030, 2: 0.030, 3: 0.030}
ACTIVE_DOWS = [0, 1, 2, 3]
CACHE = f'output/picks_cache_k{K_VAL}_fwd_emp_W30.parquet'
OUT_JSON = 'live/data/backtest_equity.json'

if len(sys.argv) < 2:
    sys.exit('usage: python rescore_year.py YEAR [--min-entry YYYY-MM-DD]')
YEAR = int(sys.argv[1])
MIN_ENTRY = None
for a in sys.argv[2:]:
    if a.startswith('--min-entry='):
        MIN_ENTRY = pd.Timestamp(a.split('=', 1)[1])
    elif a == '--min-entry' and sys.argv.index(a) + 1 < len(sys.argv):
        MIN_ENTRY = pd.Timestamp(sys.argv[sys.argv.index(a) + 1])

if not os.path.exists(CACHE):
    sys.exit(f'cache not found: {CACHE} — run report_three_sizings.py first')

print(f'Loading pool + existing cache...', flush=True)
POOL = er.load_master_pool()
print(f'  pool: {len(POOL):,} rows ({POOL["ExpirationDate"].min().date()} → {POOL["ExpirationDate"].max().date()})')
existing = pd.read_parquet(CACHE)
print(f'  cache: {len(existing):,} picks ({existing["entry_date_dt"].min().date()} → {existing["entry_date_dt"].max().date()})')
before = len(existing)
existing = existing[existing['entry_date_dt'].dt.year != YEAR].copy()
print(f'  dropped {before - len(existing)} {YEAR} picks; {len(existing):,} remain')


def score_year_with_rolling_pool(year):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    ec = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    dv = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1) & (df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True; spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False; spreads.LOW_VIX_BULLPUT_FILTER = False; spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100
    candidates = spreads.build_candidates(df)
    if MIN_ENTRY is not None:
        before = len(candidates)
        candidates = candidates[pd.to_datetime(candidates['entry_date']) >= MIN_ENTRY]
        print(f'  filtered by min-entry={MIN_ENTRY.date()}: {before:,} → {len(candidates):,}', flush=True)
    print(f'  {len(candidates):,} candidates', flush=True)

    parts = []
    dates = sorted(candidates['entry_date'].unique())
    t0 = time.time()
    for i, dt in enumerate(dates):
        sub = candidates[candidates['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(POOL, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
        parts.append(ground.score_candidates(sub))
        if (i+1) % 50 == 0:
            print(f'    scored {i+1}/{len(dates)} dates ({time.time()-t0:.0f}s)', flush=True)
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G) - 1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])
    return scored, dv, ec


def realize(scored, dv, ec):
    s = scored.copy()
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    parts = []
    for dow in ACTIVE_DOWS:
        thr = THRESH_BY_DOW[dow]
        qual = s[(s['entry_dow']==dow) & (s['GROUND'] >= thr)]
        top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return pd.DataFrame()

    def lookup(row):
        pc = 'put' if row['spread_type']=='bull_put' else 'call'
        try:
            sr = dv.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = dv.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            sl = max(float(sr['BidPrice']), min(float(sr['LastPrice']), float(sr['AskPrice'])))
            ll = max(float(lr['BidPrice']), min(float(lr['LastPrice']), float(lr['AskPrice'])))
            return max(sl - ll, 0), ec.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_last'], sel['expiry_close'] = zip(*sel.apply(lookup, axis=1))
    ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last'] * 0.85
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl_per_contract'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    ok['max_loss_dollar'] = ok['max_loss_adj'] * 100
    ok['realize_date']  = pd.to_datetime(ok['expiry_date'])
    ok['entry_date_dt'] = pd.to_datetime(ok['entry_date'])
    return ok


print(f'\nScoring {YEAR}...', flush=True)
scored, dv, ec = score_year_with_rolling_pool(YEAR)
new_picks = realize(scored, dv, ec)
print(f'  realized {len(new_picks):,} new picks for {YEAR}')

merged = pd.concat([existing, new_picks], ignore_index=True).sort_values('entry_date_dt').reset_index(drop=True)
print(f'\nMerged cache: {len(merged):,} total picks')
merged.to_parquet(CACHE)
print(f'Wrote {CACHE}')

print(f'\nRebuilding {OUT_JSON} from merged cache...')
# Defer to report_three_sizings logic for equity simulation by re-importing & calling its
# pieces. Simplest: just import report_three_sizings as a module so it computes from
# the now-refreshed cache.
import subprocess
# report_three_sizings honors the cache and skips re-scoring if present.
r = subprocess.run([sys.executable, 'report_three_sizings.py'], capture_output=True, text=True)
print(r.stdout[-2000:])
if r.returncode != 0:
    print('STDERR:', r.stderr[-2000:])
