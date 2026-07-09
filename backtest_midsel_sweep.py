"""k x thr sweep, selection on RAW MID credit; fills stress-tested at
0.80/0.85/0.90/0.95 x mid.

Per depth analysis 2026-06-10 (870k same-day prints): trades center on mid at
every quote width, so mid is the market price and selection scores on it.
The fill fraction is a realization-only stress parameter (real fills 2026-05-28
averaged 0.82 x mid, n=5).

Scores ALL candidates 2020-25 once, caches G/DKL/credit/expiry_close per
candidate; Gamma = (exp(G)-1) * exp(-k*DKL) and per-fraction P&L are then
recomputed per (k, thr, f) cell offline. qty=1 sizing. Separate 2026 OOT cache.
"""
import sys, math, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
START_BANKROLL = 10_000.0
ACTIVE_DOWS = [0, 1, 2, 3]
K_VALS = [4, 6, 8, 10, 12, 16, 20, 25, 30]
THRESHOLDS = [0.0, 0.005, 0.010, 0.015, 0.020, 0.030, 0.040, 0.050, 0.075, 0.100]
FILL_FRACS = [0.80, 0.85, 0.90, 0.95]

bt_config.CREDIT_BASIS = "mid"
bt_config.CREDIT_SCALE = 1.0  # selection on raw mid; fills stressed downstream

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


def score_and_realize_year(year, min_entry_date):
    """Score ALL candidates for the year; attach expiry close + per-fraction pnl."""
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df[df['DataDate'] >= min_entry_date]
    # Exchange-calendar filter: vendor republishes stale rows on holidays.
    _spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    df = df[df['DataDate'].isin(set(_spy['Date']))]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    if IV_RANK_LOOKUP is not None:
        df = df.merge(IV_RANK_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    if RV_LOOKUP is not None:
        df = df.merge(RV_LOOKUP, on=['Symbol', 'DataDate'], how='left')

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = False
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    candidates = spreads.build_candidates(df)

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

    scored['expiry_close'] = scored.apply(
        lambda r: expiry_close.get((r['ticker'], r['expiry_date'])), axis=1)
    ok = scored.dropna(subset=['expiry_close']).copy()
    ok['width'] = ok['net_credit'] + ok['max_loss']

    def _oc(r):
        sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
        if r['spread_type'] == 'bull_put':
            return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
    ok['_outcome'] = ok.apply(_oc, axis=1)

    for f in FILL_FRACS:
        col = f'pnl_{int(f*100)}'
        credit = ok['net_credit'] * f
        ml = ok['width'] - credit
        pnl = ok.apply(lambda r, c=credit, m=ml: spreads.calc_pnl(
            r['expiry_close'], r['short_strike'], r['long_strike'],
            c.loc[r.name], m.loc[r.name], r['spread_type']), axis=1) * 100
        # Partial-WIN haircut (canon 2026-06-08): 50% intrinsic on partial wins.
        mask = (ok['_outcome'] == 'PARTIAL') & (pnl > 0)
        pnl[mask] *= 0.5
        ok[col] = pnl

    keep = ['entry_date','expiry_date','ticker','spread_type','short_strike','long_strike',
            'net_credit','max_loss','width','G','DKL','expiry_close','_outcome','w_star'] + \
           [f'pnl_{int(f*100)}' for f in FILL_FRACS]
    keep = [c for c in keep if c in ok.columns]
    return ok[keep]


def build_cache(years, min_entry_date, cache_path):
    if os.path.exists(cache_path):
        print(f'Loading sweep cache {cache_path}...')
        return pd.read_parquet(cache_path)
    parts = []
    for year in years:
        print(f'-- {year} --', flush=True)
        parts.append(score_and_realize_year(year, min_entry_date))
    c = pd.concat(parts, ignore_index=True)
    print(f'  {len(c):,} realized candidates -- caching to {cache_path}', flush=True)
    c.to_parquet(cache_path)
    return c


_spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')


def evaluate(R, k, thr, start, end, pnl_col):
    sub = R[R['GAMMA_' + str(k)] >= thr] if ('GAMMA_' + str(k)) in R.columns else None
    if sub is None:
        R = R.copy()
        R['GAMMA_' + str(k)] = (np.exp(R['G']) - 1.0) * np.exp(-k * R['DKL'])
        sub = R[R['GAMMA_' + str(k)] >= thr]
    if sub.empty: return None
    gcol = 'GAMMA_' + str(k)
    sel = (sub.sort_values(['entry_date', gcol], ascending=[True,False])
              .groupby('entry_date').head(5))
    # Window starts at the SELECTION's first entry (not the candidate pool's):
    # the pool-start convention added flat days and shifted Sharpe by ~0.01-0.02
    # vs the report generator, causing recurring cross-table rounding mismatches.
    spy = _spy[(_spy['Date'] >= sel['entry_date'].min()) & (_spy['Date'] <= end)]
    td = pd.DatetimeIndex(spy['Date'])
    daily = sel.groupby(pd.to_datetime(sel['expiry_date']))[pnl_col].sum()
    eq = START_BANKROLL + daily.reindex(td, fill_value=0.0).cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sd = ret.std(ddof=0)
    sh = float(ret.mean()*np.sqrt(252)/sd) if sd > 0 else 0.0
    weekly = eq.resample('W-FRI').last().ffill().pct_change().dropna()
    wsd = weekly.std(ddof=0)
    wsh = float(weekly.mean()*np.sqrt(52)/wsd) if wsd > 0 else 0.0
    peak = eq.cummax()
    dd = float(((eq-peak)/peak).min())
    final = float(eq.iloc[-1])
    win = 100*(sel[pnl_col] > 0).mean()
    return dict(k=k, thr=thr, n=len(sel), final=final, sharpe=sh, sharpe_wk=wsh,
                dd=100*dd, win=win, per_tr=sel[pnl_col].mean())


def precompute_gammas(R):
    for k in K_VALS:
        R['GAMMA_' + str(k)] = (np.exp(R['G']) - 1.0) * np.exp(-k * R['DKL'])
    return R


if __name__ == '__main__':
    BT = build_cache([2020,2021,2022,2023,2024,2025], pd.Timestamp('2020-01-01'),
                     'output/sweep_midmkt_v2_2020_25.parquet')
    BT['entry_date'] = pd.to_datetime(BT['entry_date'])
    BT = precompute_gammas(BT)
    print(f'\n{len(BT):,} candidates in sweep cache')
    start, end = BT['entry_date'].min(), pd.Timestamp('2025-12-31')

    rows = []
    for k in K_VALS:
        for thr in THRESHOLDS:
            base = evaluate(BT, k, thr, start, end, 'pnl_80')
            if not base: continue
            for f in FILL_FRACS[1:]:
                r = evaluate(BT, k, thr, start, end, f'pnl_{int(f*100)}')
                base[f'final_{int(f*100)}'] = r['final']
                base[f'sh_wk_{int(f*100)}'] = r['sharpe_wk']
            rows.append(base)
    res = pd.DataFrame(rows).rename(columns={'final':'final_80','sharpe_wk':'sh_wk_80'})
    pd.set_option('display.width', 250)

    cols = ['k','thr','n','final_80','final_85','final_90','final_95',
            'sh_wk_80','sh_wk_85','sh_wk_90','sh_wk_95','dd','win']
    fmt = {c: '${:,.0f}'.format for c in cols if c.startswith('final')}
    fmt.update({c: '{:+.2f}'.format for c in cols if c.startswith('sh_')})
    fmt.update({'dd': '{:.1f}%'.format, 'win': '{:.1f}%'.format})

    print('\n=== Top 15 by final @ 0.80 fill (worst case) ===')
    print(res.sort_values('final_80', ascending=False).head(15)[cols].to_string(index=False, formatters=fmt))
    print('\n=== Top 15 by Sharpe(wk) @ 0.80 fill ===')
    print(res.sort_values('sh_wk_80', ascending=False).head(15)[cols].to_string(index=False, formatters=fmt))
    res.to_csv('output/sweep_midmkt_v2_results.csv', index=False)
    print('\nWrote output/sweep_midmkt_results.csv')

    OOT = build_cache([2026], pd.Timestamp('2026-01-01'),
                      'output/sweep_midmkt_v2_oot2026.parquet')
    OOT['entry_date'] = pd.to_datetime(OOT['entry_date'])
    OOT = precompute_gammas(OOT)
    print(f'\n{len(OOT):,} OOT candidates')
    o_start, o_end = OOT['entry_date'].min(), pd.Timestamp('2026-12-31')
    print('\n=== 2026 OOT at top-5 cells (by final_80) ===')
    for _, c in res.sort_values('final_80', ascending=False).head(5).iterrows():
        for f in FILL_FRACS:
            r = evaluate(OOT, int(c['k']), c['thr'], o_start, o_end, f'pnl_{int(f*100)}')
            if r:
                print(f"  k={int(c['k']):<3} thr={c['thr']:<6} fill={f:.2f}  n={r['n']:<4} "
                      f"final ${r['final']:>9,.0f}  Sh(wk) {r['sharpe_wk']:+.2f}  DD {r['dd']:.1f}%")
            else:
                print(f"  k={int(c['k']):<3} thr={c['thr']:<6} fill={f:.2f}  -- no trades")
