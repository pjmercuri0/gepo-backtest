"""Trailing-window-weeks sweep for forward empirical DKL.

For each entry_date and each candidate, computes dkl_fwd using rolling
windows of W ∈ {10, 20, 30, 40, 50} weeks. EV/Kelly are scored once
(they don't depend on W). Realizes pnl_per_ctr once.

Output: output/wsweep_realized.parquet with columns
  G, w_star, ml_dollar, pnl_per_ctr, entry_date, expiry_date, spread_type
  dkl_fwd_10w, dkl_fwd_20w, dkl_fwd_30w, dkl_fwd_40w, dkl_fwd_50w

Then per-W k×threshold sweep on equity Sharpe.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground, historical_probs as hp

SP100 = set(bt_config.SP100_TICKERS)
TEST_YEARS = [2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE_DOWS = [0, 1, 2, 3]
KELLY_FRAC = 0.0625
KELLY_CAP = 5
START_BANKROLL = 10_000.0
W_WEEKS = [10, 20, 30, 40, 50]
W_DAYS  = [w * 7 for w in W_WEEKS]
POOL_PATH = 'output/master_pool.parquet'
OUT_PATH  = 'output/wsweep_realized.parquet'

K_VALS     = [10, 20, 30, 50, 75, 100]
THRESHOLDS = [0.0, 0.005, 0.010, 0.020, 0.030, 0.050]


def build_window_table(pool, asof, trail_days):
    """Put/call-split bucket table for the given trailing window."""
    lo = asof - pd.Timedelta(days=trail_days)
    sub = pool[(pool['ExpirationDate'] >= lo) & (pool['ExpirationDate'] < asof)]
    if sub.empty:
        return None, None
    iv_bins = sub['iv_capped'].quantile([0, 0.2, 0.4, 0.6, 0.8, 1.0]).values
    iv_bins[-1] += 0.001
    sub = sub.copy()
    sub['iv_bucket'] = pd.cut(sub['iv_capped'], bins=iv_bins, labels=False, include_lowest=True)
    tables = {}
    for pc in ['put','call']:
        sub_pc = sub[sub['putcall_norm']==pc]
        if sub_pc.empty:
            tables[pc] = None; continue
        agg = sub_pc.groupby(['DTE','delta_bucket','iv_bucket']).agg(
            n=('itm','size'), p_itm=('itm','mean')
        ).reset_index()
        agg['p_itm_reliable'] = np.where(agg['n']>=30, agg['p_itm'], np.nan)
        tables[pc] = agg
    return tables, iv_bins


def load_year(year):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    ec = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    dv = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']=df['DataDate'].dt.dayofweek; df['exp_dow']=df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float)>0]
    df = df.copy()
    df['AbsDelta']=df['Delta'].abs(); df['MidPrice']=(df['BidPrice']+df['AskPrice'])/2
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER=True; spreads.REGIME_PER_TICKER=False
    spreads.GAP_FILTER=False; spreads.LOW_VIX_BULLPUT_FILTER=False; spreads.SLIPPAGE_CENTS=0.0
    bt_config.MIN_OPEN_INTEREST=100
    cand = spreads.build_candidates(df)
    return cand, dv, ec


def fwd_dkl_for_row(p, q, ro, p_emp, q_emp, ro_emp):
    if p_emp is None or p_emp <= 0: return np.nan
    dkl = 0.0
    if p_emp  > 0 and p  > 0: dkl += p_emp  * math.log(p_emp  / p)
    if ro_emp > 0 and ro > 0: dkl += ro_emp * math.log(ro_emp / ro)
    if q_emp  > 0 and q  > 0: dkl += q_emp  * math.log(q_emp  / q)
    return max(0.0, dkl)


def score_year(year, pool):
    """Score year with all W values; realize once."""
    cand, dv, ec = load_year(year)
    print(f'  {len(cand):,} candidates', flush=True)
    if cand.empty: return pd.DataFrame()
    # Score once with uniform to get p, q, ro, w_star, G (ell)
    ground.DKL_REFERENCE = 'uniform'
    scored = ground.score_candidates(cand).copy()

    # For each entry_date, build W tables and add per-W DKL columns
    dates = sorted(scored['entry_date'].unique())
    t0 = time.time()
    # pre-allocate columns
    for w in W_WEEKS:
        scored[f'dkl_fwd_{w}w'] = np.nan

    for i, dt in enumerate(dates):
        dt_ts = pd.Timestamp(dt)
        # Build tables for each W (heaviest cost: groupby for each W)
        tables_by_w = {}
        bins_by_w   = {}
        for w, td in zip(W_WEEKS, W_DAYS):
            tables_by_w[w], bins_by_w[w] = build_window_table(pool, dt_ts, td)

        sub = scored[scored['entry_date'] == dt]
        if sub.empty: continue

        for idx, r in sub.iterrows():
            p, q, ro = r['p'], r['q'], r['ro']
            if pd.isna(p): continue
            pc = 'put' if r['spread_type'] == 'bull_put' else 'call'
            for w in W_WEEKS:
                tbl = tables_by_w[w].get(pc) if tables_by_w[w] else None
                if tbl is None:
                    continue
                hp._EMPIRICAL_TABLE = tbl
                hp._EMPIRICAL_IV_BINS = bins_by_w[w]
                p_emp, q_emp, ro_emp, _ = hp.empirical_lookup_probs(
                    short_delta = r['short_delta'],
                    long_delta  = r['long_delta'],
                    iv_short    = float(r['IV']),
                    iv_long     = float(r.get('long_IV', r['IV'])),
                    dte_days    = int(r['DTE']),
                    spread_type = r['spread_type'],
                )
                scored.at[idx, f'dkl_fwd_{w}w'] = fwd_dkl_for_row(p, q, ro, p_emp, q_emp, ro_emp)

        if (i+1) % 25 == 0:
            print(f'      scored {i+1}/{len(dates)} dates ({time.time()-t0:.0f}s)', flush=True)

    # Realize pnl_per_ctr
    print(f'  scored {len(scored):,}; realizing pnl_per_ctr...', flush=True)
    def lookup(r):
        pc='put' if r['spread_type']=='bull_put' else 'call'
        try:
            sr=dv.loc[(r['entry_date'],r['ticker'],r['expiry_date'],r['short_strike'],pc)]
            lr=dv.loc[(r['entry_date'],r['ticker'],r['expiry_date'],r['long_strike'],pc)]
            if hasattr(sr,'iloc') and sr.ndim>1: sr=sr.iloc[0]
            if hasattr(lr,'iloc') and lr.ndim>1: lr=lr.iloc[0]
            sl=max(float(sr['BidPrice']),min(float(sr['LastPrice']),float(sr['AskPrice'])))
            ll=max(float(lr['BidPrice']),min(float(lr['LastPrice']),float(lr['AskPrice'])))
            return max(sl-ll,0), ec.get((r['ticker'],r['expiry_date']))
        except KeyError: return None,None
    scored['raw_last'], scored['expiry_close'] = zip(*scored.apply(lookup, axis=1))
    ok = scored.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit']=ok['raw_last']*0.85
    ok['width']=ok['net_credit']+ok['max_loss']
    ok['ml_adj']=ok['width']-ok['credit']; ok['ml_dollar']=ok['ml_adj']*100
    ok['pnl_per_ctr']=ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'],r['short_strike'],r['long_strike'],r['credit'],r['ml_adj'],r['spread_type']),axis=1)*100
    return ok


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
print(f'═ W-sweep: trailing {W_WEEKS} weeks for forward empirical DKL ═')
print(f'Test years {TEST_YEARS}\n', flush=True)

print('Loading pool...', flush=True)
pool = pd.read_parquet(POOL_PATH)
print(f'  {len(pool):,} rows ({POOL_PATH})', flush=True)

import os
if os.path.exists(OUT_PATH):
    R = pd.read_parquet(OUT_PATH)
    print(f'Loaded cached realized: {len(R):,} rows ({OUT_PATH})', flush=True)
else:
    all_ok = []
    for yr in TEST_YEARS:
        print(f'\n── Year {yr} ──', flush=True)
        all_ok.append(score_year(yr, pool))
    R = pd.concat(all_ok, ignore_index=True)
    R.to_parquet(OUT_PATH, index=False)
    print(f'\nAll-year realized: {len(R):,} ({OUT_PATH})', flush=True)


# ──────────────────────────────────────────────────────────────────────
# Equity-Sharpe sweep per W
# ──────────────────────────────────────────────────────────────────────
_spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
TRADING_DAYS = pd.DatetimeIndex(_spy['Date'])


def select(R, gcol, thr):
    sub = R[R[gcol] >= thr]
    if sub.empty: return sub
    sub = sub.copy()
    sub['entry_dow'] = pd.to_datetime(sub['entry_date']).dt.dayofweek
    parts = []
    for dow in ACTIVE_DOWS:
        d = sub[sub['entry_dow'] == dow]
        parts.append(d.sort_values(['entry_date', gcol], ascending=[True, False])
                       .groupby('entry_date').head(5))
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return sel
    def qty(r):
        ws = r['w_star']; mld = r['ml_dollar']
        if pd.isna(ws) or ws <= 0 or mld <= 0: return 1
        return max(1, min(KELLY_CAP, int(KELLY_FRAC * float(ws) * START_BANKROLL / mld)))
    sel['qty'] = sel.apply(qty, axis=1)
    sel['pnl'] = sel['qty'] * sel['pnl_per_ctr']
    return sel


def equity_sharpe(ok):
    if ok.empty: return 0.0
    daily_pnl = ok.groupby(pd.to_datetime(ok['expiry_date']))['pnl'].sum()
    daily_pnl = daily_pnl.reindex(TRADING_DAYS, fill_value=0.0)
    eq = START_BANKROLL + daily_pnl.cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sd = ret.std(ddof=0)
    return float(ret.mean() * np.sqrt(252) / sd) if sd > 0 else 0.0


print(f'\n═ Equity-Sharpe sweep: W × k × threshold (forward DKL) ═')
print(f'{"W":>3} {"k":>4} {"thr":>9} {"trades":>6} {"profit":>9} {"$/tr":>7} {"eq_Sh":>6}')
print('-' * 60)
best_overall = None
for w in W_WEEKS:
    dkl_col = f'dkl_fwd_{w}w'
    for k in K_VALS:
        R['_G'] = np.where(pd.notna(R['G']) & pd.notna(R[dkl_col]),
                            (np.exp(R['G']) - 1.0) * np.exp(-k * R[dkl_col]),
                            np.nan)
        best_w_k = None
        for thr in THRESHOLDS:
            sel = select(R, '_G', thr)
            if sel.empty: continue
            sh = equity_sharpe(sel)
            n = len(sel); tot = sel['pnl'].sum(); mu = sel['pnl'].mean()
            if best_w_k is None or sh > best_w_k[0]:
                best_w_k = (sh, thr, n, tot, mu)
        if best_w_k is None: continue
        sh, thr, n, tot, mu = best_w_k
        print(f'{w:>3} {k:>4} {thr:>9.4f} {n:>6} ${tot:>+7,.0f} ${mu:>+5.2f} Sh{sh:>+5.2f}', flush=True)
        if best_overall is None or sh > best_overall[0]:
            best_overall = (sh, w, k, thr, n, tot, mu)

print('\n═ Best (W, k, thr) overall ═')
sh, w, k, thr, n, tot, mu = best_overall
print(f'  W={w}w  k={k}  thr={thr:.4f}  →  {n} trades  ${tot:+,.0f}  ${mu:+.2f}/tr  eq_Sh{sh:+.2f}')
