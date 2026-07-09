"""Score each candidate once with THREE DKL flavors, realize all, then
sweep per-day GROUND thresholds. Single pass through scoring/realization.

DKL flavors per candidate:
  uniform  : DKL(p,q,ro || 1/3,1/3,1/3)        — canonical
  forward  : DKL(P_hist || Q_delta)             — rolling 50w P_hist
  backward : DKL(Q_delta || P_hist)             — same data, flipped args
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import historical_probs as hp

SP100 = set(bt_config.SP100_TICKERS)
WARMUP_YEAR = 2022
TEST_YEARS = [2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE_DOWS = [0, 1, 2, 3]
KELLY_FRAC = 0.0625
KELLY_CAP = 5
START_BANKROLL = 10_000.0
TRAIL_DAYS = 350
K = ground.DKL_K  # GROUND k

# Per-day thresholds to sweep
SWEEP = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.030]


# ──────────────────────────────────────────────────────────────────────
# Master pool
# ──────────────────────────────────────────────────────────────────────
POOL_CACHE = 'output/master_pool_2022_2025_v3.parquet'  # v3 = with PutCall + regime

def build_master_pool():
    import os
    if os.path.exists(POOL_CACHE):
        pool = pd.read_parquet(POOL_CACHE)
        print(f'  loaded cached master pool: {len(pool):,} rows ({POOL_CACHE})', flush=True)
        return pool
    # Build regime series from SPY 100d SMA (matches what scoring uses)
    regime_series = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    frames = []
    for y in [WARMUP_YEAR] + TEST_YEARS:
        df = pd.read_parquet(f'output/{y}_sp500_last.parquet')
        df = df[df['Symbol'].isin(SP100)]
        ec = (df[df['DataDate']==df['ExpirationDate']]
              .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first())
        df = df[(df['DTE']>=1)&(df['DTE']<=4)].copy()
        df = df[df['DataDate'].dt.dayofweek.isin([0,1,2,3])]
        df = df[df['ExpirationDate'].dt.dayofweek==4]
        df['expiry_close'] = df.set_index(['Symbol','ExpirationDate']).index.map(ec.get)
        df = df.dropna(subset=['expiry_close','Delta','ImpliedVolatility'])
        df['abs_delta'] = df['Delta'].abs()
        df['itm'] = np.where(
            df['PutCall'].str.lower()=='put',
            df['expiry_close'] < df['StrikePrice'],
            df['expiry_close'] > df['StrikePrice'],
        ).astype(int)
        df['putcall_norm'] = df['PutCall'].str.lower()
        # Regime is determined by DataDate (when the trade was open). Use as-of lookup.
        dd_sorted = df['DataDate'].sort_values().unique()
        reg_idx = regime_series.index.searchsorted(dd_sorted, side='right') - 1
        reg_idx = np.clip(reg_idx, 0, len(regime_series) - 1)
        reg_map = dict(zip(dd_sorted, regime_series.iloc[reg_idx].values))
        df['regime'] = df['DataDate'].map(reg_map).fillna('bull')
        frames.append(df[['DataDate','ExpirationDate','DTE','putcall_norm','regime','abs_delta','ImpliedVolatility','itm']])
    pool = pd.concat(frames, ignore_index=True)
    pool['delta_bucket'] = (pool['abs_delta']*10).astype(int).clip(0,9)
    pool['iv_capped']    = pool['ImpliedVolatility'].clip(upper=3.0)
    print(f'  master pool: {len(pool):,} rows', flush=True)
    pool.to_parquet(POOL_CACHE, index=False)
    print(f'  cached to {POOL_CACHE}', flush=True)
    return pool


def build_table_for_window(pool, asof):
    """Returns dict[(putcall, regime) → agg] plus shared iv_bins."""
    lo = asof - pd.Timedelta(days=TRAIL_DAYS)
    sub = pool[(pool['ExpirationDate'] >= lo) & (pool['ExpirationDate'] < asof)]
    if sub.empty:
        return None, None
    iv_bins = sub['iv_capped'].quantile([0,0.2,0.4,0.6,0.8,1.0]).values
    iv_bins[-1] += 0.001
    sub = sub.copy()
    sub['iv_bucket'] = pd.cut(sub['iv_capped'], bins=iv_bins, labels=False, include_lowest=True)
    tables = {}
    for pc in ['put','call']:
        for reg in ['bull','bear']:
            sub_kk = sub[(sub['putcall_norm']==pc) & (sub['regime']==reg)]
            if sub_kk.empty:
                tables[(pc,reg)] = None
                continue
            agg = sub_kk.groupby(['DTE','delta_bucket','iv_bucket']).agg(
                n=('itm','size'), p_itm=('itm','mean')
            ).reset_index()
            agg['p_itm_reliable'] = np.where(agg['n']>=30, agg['p_itm'], np.nan)
            tables[(pc,reg)] = agg
    return tables, iv_bins


# ──────────────────────────────────────────────────────────────────────
# Single-pass scorer: compute 3 GROUND columns per candidate
# ──────────────────────────────────────────────────────────────────────
def score_three_flavors(cand, pool):
    """Score per entry_date: ground.score_candidates gives uniform DKL + Kelly;
    post-process to add forward & backward empirical DKLs."""
    parts = []
    dates = sorted(cand['entry_date'].unique())
    t0 = time.time()
    ground.DKL_REFERENCE = "uniform"
    # Build SPY regime series for entry-date lookup
    regime_series = spreads.build_regime_lookup(SPY_CSV, sma_window=100)

    for i, dt in enumerate(dates):
        tables, bins = build_table_for_window(pool, pd.Timestamp(dt))
        hp._EMPIRICAL_IV_BINS = bins
        sub = cand[cand['entry_date'] == dt]
        if sub.empty:
            continue

        # Regime for this entry_date (as-of lookup)
        dt_ts = pd.Timestamp(dt)
        ridx = regime_series.index.searchsorted(dt_ts, side='right') - 1
        ridx = max(0, min(ridx, len(regime_series)-1))
        cur_regime = regime_series.iloc[ridx] if len(regime_series) > 0 else 'bull'

        scored = ground.score_candidates(sub).copy()
        # scored has columns: p, q, ro, w_star, G (=ell), EV, DKL (uniform), etc.
        # Compute forward & backward empirical DKL row-by-row, then three GROUNDs.
        def add_empirical(r):
            p, q, ro = r['p'], r['q'], r['ro']
            if pd.isna(p) or pd.isna(q) or pd.isna(ro):
                return pd.Series({'dkl_fwd': float('nan'), 'dkl_bwd': float('nan')})
            # Swap empirical table by (spread_type, regime)
            pc = 'put' if r['spread_type'] == 'bull_put' else 'call'
            hp._EMPIRICAL_TABLE = tables.get((pc, cur_regime)) if tables else None
            if hp._EMPIRICAL_TABLE is None:
                return pd.Series({'dkl_fwd': r['DKL'], 'dkl_bwd': r['DKL']})
            p_emp, q_emp, ro_emp, _ = hp.empirical_lookup_probs(
                short_delta = r['short_delta'],
                long_delta  = r['long_delta'],
                iv_short    = float(r['IV']),
                iv_long     = float(r.get('long_IV', r['IV'])),
                dte_days    = int(r['DTE']),
            )
            if (p_emp is None) or (p_emp <= 0):
                return pd.Series({'dkl_fwd': r['DKL'], 'dkl_bwd': r['DKL']})
            dkl_fwd = 0.0
            if p_emp  > 0 and p  > 0: dkl_fwd += p_emp  * math.log(p_emp  / p)
            if ro_emp > 0 and ro > 0: dkl_fwd += ro_emp * math.log(ro_emp / ro)
            if q_emp  > 0 and q  > 0: dkl_fwd += q_emp  * math.log(q_emp  / q)
            dkl_bwd = 0.0
            if p  > 0 and p_emp  > 0: dkl_bwd += p  * math.log(p  / p_emp)
            if ro > 0 and ro_emp > 0: dkl_bwd += ro * math.log(ro / ro_emp)
            if q  > 0 and q_emp  > 0: dkl_bwd += q  * math.log(q  / q_emp)
            return pd.Series({'dkl_fwd': max(0.0, dkl_fwd), 'dkl_bwd': max(0.0, dkl_bwd)})

        emp = scored.apply(add_empirical, axis=1)
        scored = pd.concat([scored, emp], axis=1)

        # Compute three GROUND columns: Γ = (exp(G)-1) * exp(-k·DKL) where G is ell
        def gnd(dkl_col):
            return scored.apply(lambda r: (math.exp(r['G']) - 1.0) * math.exp(-K * r[dkl_col])
                                if pd.notna(r['G']) and pd.notna(r[dkl_col]) else float('nan'), axis=1)
        scored['G_uni'] = gnd('DKL')
        scored['G_fwd'] = gnd('dkl_fwd')
        scored['G_bwd'] = gnd('dkl_bwd')

        parts.append(scored)
        if (i+1) % 25 == 0:
            print(f'      scored {i+1}/{len(dates)} dates ({time.time()-t0:.0f}s)', flush=True)
    return pd.concat(parts, ignore_index=True)


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


# Pre-realize: for each scored candidate, compute pnl_per_ctr (independent of GROUND threshold)
def realize_all(scored, dv, ec):
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


def apply_thresh_topn(realized, ground_col, thresh_per_dow):
    """Select top-5 per (entry_date, dow) where GROUND_col >= per-dow threshold."""
    r = realized.copy()
    r['entry_dow']=pd.to_datetime(r['entry_date']).dt.dayofweek
    parts=[]
    for dow in ACTIVE_DOWS:
        thr = thresh_per_dow[dow]
        sub = r[(r['entry_dow']==dow) & (r[ground_col] >= thr)]
        top = sub.sort_values(['entry_date', ground_col], ascending=[True, False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return sel
    def qty(r):
        ws=r.get('w_star')
        if ws is None or pd.isna(ws) or ws<=0 or r['ml_dollar']<=0: return 1
        return max(1,min(KELLY_CAP,int(KELLY_FRAC*float(ws)*START_BANKROLL/r['ml_dollar'])))
    sel['qty']=sel.apply(qty,axis=1); sel['pnl']=sel['qty']*sel['pnl_per_ctr']
    return sel


def stats(ok):
    if ok.empty: return 0,0,0,0,0
    n=len(ok); tot=ok['pnl'].sum(); mu=ok['pnl'].mean()
    win=100*(ok['pnl']>0).mean()
    daily=ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg=daily.std(ddof=0); sh=(daily.mean()*np.sqrt(252)/sg) if sg>0 else 0
    return n,tot,mu,win,sh


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
print(f'\n══ DKL sweep: uniform vs forward(hist‖delta) vs backward(delta‖hist) ══')
print(f'Test years {TEST_YEARS}, rolling 50w P_hist for empirical.\n', flush=True)

REALIZED_CACHE = 'output/sweep_realized_2023_25.parquet'
import os
if os.path.exists(REALIZED_CACHE):
    R = pd.read_parquet(REALIZED_CACHE)
    print(f'Loaded cached realized: {len(R):,} candidates ({REALIZED_CACHE})', flush=True)
else:
    print('Building master pool...', flush=True)
    pool = build_master_pool()

    all_realized = []
    for yr in TEST_YEARS:
        print(f'\n── Year {yr} ──', flush=True)
        cand, dv, ec = load_year(yr)
        print(f'  {len(cand):,} candidates', flush=True)
        scored = score_three_flavors(cand, pool)
        print(f'  scored {len(scored):,}; realizing pnl_per_ctr...', flush=True)
        realized = realize_all(scored, dv, ec)
        realized['year'] = yr
        print(f'  realized {len(realized):,}', flush=True)
        all_realized.append(realized)

    R = pd.concat(all_realized, ignore_index=True)
    print(f'\nAll-year realized: {len(R):,} candidates with pnl_per_ctr', flush=True)
    R.to_parquet(REALIZED_CACHE, index=False)
    print(f'  cached realized to {REALIZED_CACHE}', flush=True)

# ──────────────────────────────────────────────────────────────────────
# Sweep
# ──────────────────────────────────────────────────────────────────────
def sweep_for(col, label):
    print(f'\n══ {label} ({col}) — same threshold across Mon/Tue/Thu ══')
    print(f'{"thr":>7} {"trades":>6} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>7}')
    print('-'*52)
    for thr in SWEEP:
        sel = apply_thresh_topn(R, col, {d:thr for d in ACTIVE_DOWS})
        n,tot,mu,win,sh = stats(sel)
        print(f'{thr:>7.4f} {n:>6} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}', flush=True)

sweep_for('G_uni', 'UNIFORM')
sweep_for('G_fwd', 'FORWARD  D(hist‖delta)')
sweep_for('G_bwd', 'BACKWARD D(delta‖hist)')


# Per-day sweep on each flavor
def sweep_per_day(col, label):
    print(f'\n══ {label} per-day threshold sweep ══')
    dnames = {0:'Mon', 1:'Tue', 2:'Wed', 3:'Thu'}
    for dow in ACTIVE_DOWS:
        dname = dnames[dow]
        print(f'\n  {dname} only:')
        print(f'  {"thr":>7} {"trades":>6} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>7}')
        print('  ' + '-'*50)
        for thr in SWEEP:
            sel = apply_thresh_topn(R, col, {d:(thr if d==dow else 999) for d in ACTIVE_DOWS})
            n,tot,mu,win,sh = stats(sel)
            print(f'  {thr:>7.4f} {n:>6} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}', flush=True)

sweep_per_day('G_fwd', 'FORWARD')
sweep_per_day('G_bwd', 'BACKWARD')
