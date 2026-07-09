"""Compare k=20, 25, 30 with the canonical setup:
- Mon/Tue/Thu (Wed dropped for backtest)
- top-5 per day
- Per-day GROUND thresholds (Mon 0.003 / Tue 0.005 / Thu 0.010) — same at all k
- ¹⁄₁₆ Kelly cap=5, fixed $10k base, 0.85×clamped LAST

CAVEAT: thresholds were calibrated at k=30. Same thresholds at different k
means different effective selectivity since GROUND scales differently.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
THRESH = {0: 0.003, 1: 0.005, 3: 0.010}
DOWS = [0, 1, 3]
KS = [20, 25, 30]
KELLY_FRAC = 0.0625
KELLY_CAP = 5
START_BANKROLL = 10_000.0


def build_year_candidates(year):
    """Build candidates ONCE per year — k only affects GROUND, not candidates."""
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow'] = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float)>0]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice']+df['AskPrice'])/2

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    cand = spreads.build_candidates(df)
    scored = ground.score_candidates(cand)  # adds G, DKL, w_star, etc.
    return scored, df_idx_view, expiry_close


def select_and_realize(scored, df_idx_view, expiry_close, k_val):
    s = scored.copy()
    # Recompute GROUND with this k
    def gnd(r):
        G,DKL=r.get('G'),r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0)*math.exp(-k_val*DKL)
    s['GROUND'] = s.apply(gnd, axis=1)
    s = s.dropna(subset=['GROUND'])
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    parts = []
    for dow in DOWS:
        sub = s[(s['entry_dow']==dow)&(s['GROUND']>=THRESH[dow])]
        top = sub.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty:
        return pd.DataFrame(columns=['realize_date','pnl'])
    def lookup(r):
        pc = 'put' if r['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(r['entry_date'],r['ticker'],r['expiry_date'],r['short_strike'],pc)]
            lr = df_idx_view.loc[(r['entry_date'],r['ticker'],r['expiry_date'],r['long_strike'],pc)]
            if hasattr(sr,'iloc') and sr.ndim>1: sr=sr.iloc[0]
            if hasattr(lr,'iloc') and lr.ndim>1: lr=lr.iloc[0]
            sl = max(float(sr['BidPrice']), min(float(sr['LastPrice']), float(sr['AskPrice'])))
            ll = max(float(lr['BidPrice']), min(float(lr['LastPrice']), float(lr['AskPrice'])))
            return max(sl-ll,0), expiry_close.get((r['ticker'],r['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_last'], sel['expiry_close'] = zip(*sel.apply(lookup, axis=1))
    ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last']*0.85
    ok['width'] = ok['net_credit']+ok['max_loss']
    ok['max_loss_adj'] = ok['width']-ok['credit']
    ok['ml_dollar'] = ok['max_loss_adj']*100
    ok['pnl_per_contract'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1)*100
    # ¹⁄₁₆ Kelly sizing (flat $10k base, cap=5)
    def qty(r):
        ws = r.get('w_star')
        if ws is None or pd.isna(ws) or ws<=0 or r['ml_dollar']<=0: return 1
        return max(1, min(KELLY_CAP, int(KELLY_FRAC*float(ws)*START_BANKROLL/r['ml_dollar'])))
    ok['qty'] = ok.apply(qty, axis=1)
    ok['pnl'] = ok['qty']*ok['pnl_per_contract']
    ok['realize_date'] = pd.to_datetime(ok['expiry_date'])
    return ok


def stats(ok):
    if ok.empty: return 0, 0.0, 0.0, 0.0
    n = len(ok); tot = ok['pnl'].sum()
    daily = ok.groupby('realize_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg>0 else 0
    win = 100*(ok['pnl']>0).mean()
    return n, tot, win, sh


year_cache = []
for year in YEARS:
    print(f'── building {year} ──', flush=True)
    year_cache.append(build_year_candidates(year))


print(f'\n══ Comparison: k=20 vs 25 vs 30 (Mon/Tue/Thu, top-5, per-day thresh, ¹⁄₁₆K cap=5) ══')
print(f'{"k":>4} {"trades":>6} {"profit":>10} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*52)
for k in KS:
    all_ok = pd.concat([select_and_realize(*yc, k) for yc in year_cache], ignore_index=True)
    n, tot, win, sh = stats(all_ok)
    mu = tot/n if n else 0
    print(f'{k:>4} {n:>6} ${tot:>+8,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')

print(f'\n══ Per-year Sharpe ══')
print(f'{"k":>4}  ' + '  '.join([f'{y}' for y in YEARS]))
print('-'*36)
for k in KS:
    parts = [f'{k:>4}']
    for yc in year_cache:
        ok = select_and_realize(*yc, k)
        n, tot, win, sh = stats(ok)
        parts.append(f'Sh{sh:+5.2f}')
    print('  '.join(parts))
