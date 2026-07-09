"""Yesterday's-LAST audit: score using PREVIOUS trading day's LastPrice instead
of today's. If today's LAST is smuggling post-15:01 info into the scoring, this
test will show a collapse vs. canonical. If results stay similar, today's LAST
is genuinely a usable signal at trade time.

Setup:
  - Scoring: prev-day LAST credit (no day-of lookahead possible)
  - Filter: Mon-Thu, top-5 ∧ GROUND≥0.001, regime ON, vol gate OFF
  - Realization: today's LAST × 0.85 (canonical fill assumption — unchanged)
  - Compare to today's-LAST canon for each year and combined.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
MULT = 0.85


def add_prev_day_last(df):
    """Add PrevLastPrice column: previous-trading-day LastPrice for the same
    (Symbol, Expiry, Strike, PutCall). Uses shift(1) within group ordered
    by DataDate."""
    df = df.sort_values(['Symbol','ExpirationDate','StrikePrice','PutCall','DataDate']).reset_index(drop=True)
    df['PrevLastPrice'] = df.groupby(['Symbol','ExpirationDate','StrikePrice','PutCall'])['LastPrice'].shift(1)
    return df


def score_year(df_full, use_prev):
    """Score one year with either today's LAST (use_prev=False) or yesterday's LAST."""
    df = df_full.copy()
    if use_prev:
        # Substitute prev-day LAST for LastPrice during scoring
        df['LastPrice'] = df['PrevLastPrice']
    df = df[df['LastPrice'].astype(float) > 0].copy()
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
    return scored.dropna(subset=['GROUND'])


def realize(scored, df_idx_view_today, expiry_close):
    """Top-5 ∧ GROUND≥0.001 per day, realize at today's-LAST × 0.85.
    df_idx_view_today carries today's LastPrice (real, not shifted)."""
    sel = scored[scored['GROUND'] >= 0.001]
    sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
    sel = sel.copy()
    def lookup_last(row):
        pc = 'put' if row['spread_type'] == 'bull_put' else 'call'
        try:
            sr = df_idx_view_today.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view_today.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            return max(float(sr['LastPrice']) - float(lr['LastPrice']), 0), expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_last'], sel['expiry_close'] = zip(*sel.apply(lookup_last, axis=1))
    ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last'] * MULT
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    return ok


def stats(ok):
    n = len(ok); tot = ok['pnl'].sum(); mu = ok['pnl'].mean() if n else 0
    win = 100*(ok['pnl']>0).mean() if n else 0
    daily = ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


def fmt(label, ok):
    n,tot,mu,win,sh = stats(ok)
    return f'{label:<26} {n:>5} ${tot:>+8,.0f} ${mu:>+6.2f} {win:>5.1f}% Sh{sh:>+5.2f}'


spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
spreads.REGIME_FILTER = True
spreads.REGIME_PER_TICKER = False
spreads.GAP_FILTER = False
spreads.LOW_VIX_BULLPUT_FILTER = False
spreads.SLIPPAGE_CENTS = 0.0
bt_config.MIN_OPEN_INTEREST = 100

today_totals = []; prev_totals = []
print(f'\n══ Yesterday-LAST audit (Mon-Thu, top-5∧GROUND≥0.001, 0.85×LAST realized) ══\n')
print(f'{"variant":<26} {"trades":>5} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*64)
for year in YEARS:
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    # PrevLastPrice on ALL data (not yet filtered for entry days)
    df_full = add_prev_day_last(df_full)
    # Today's LAST view for realization (DOES include today's actual last trade)
    df_idx_today = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]

    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin([0,1,2,3])]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]

    t0 = time.time()
    scored_today = score_year(df, use_prev=False)
    scored_prev  = score_year(df, use_prev=True)
    ok_today = realize(scored_today, df_idx_today, expiry_close)
    ok_prev  = realize(scored_prev,  df_idx_today, expiry_close)
    today_totals.append(ok_today); prev_totals.append(ok_prev)
    print(fmt(f'{year} TODAY-LAST score', ok_today))
    print(fmt(f'{year} YESTERDAY-LAST score',  ok_prev))
    print(f'                          [{time.time()-t0:.0f}s]', flush=True)

print()
print(fmt('TOTAL TODAY-LAST',     pd.concat(today_totals, ignore_index=True)))
print(fmt('TOTAL YESTERDAY-LAST', pd.concat(prev_totals,  ignore_index=True)))
