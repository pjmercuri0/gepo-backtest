"""2D sweep of (k, thr) for q_down_ro_sym DKL canonical.

Step 1 (slow ~25 min): score ALL candidates for 2020-2025 with G, DKL pre-filter,
cache to output/sweep_qdown_scored.parquet.

Step 2 (fast): for each (k, thr) combo, recompute GROUND = (exp(G)-1)·exp(-k·DKL),
filter top-5 per dow above thr, simulate qty=2, report Sharpe/yield/DD/n_picks.
"""
import sys, math, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
START_BANKROLL = 10_000.0
ACTIVE_DOWS = [0, 1, 2, 3]
MIN_ENTRY_DATE = pd.Timestamp('2020-08-01')

SCORED_CACHE = 'output/sweep_qdown_scored.parquet'

K_GRID   = [50, 100, 150, 200, 300, 500, 750]
THR_GRID = [0.003, 0.005, 0.010, 0.020, 0.030, 0.050, 0.075, 0.100]


def score_year_full(year, pool):
    """Return ALL scored candidates (with G, DKL, realized PnL) — no GROUND filter, no top-5."""
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df[df['DataDate'] >= MIN_ENTRY_DATE]
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
    parts = []
    dates = sorted(candidates['entry_date'].unique())
    for dt in dates:
        sub = candidates[candidates['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(pool, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
        parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    scored = scored.dropna(subset=['G', 'DKL']).copy()

    # Attach realized PnL (mirrors realize() in report_three_sizings.py)
    def lookup(row):
        pc = 'put' if row['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            sl = max(float(sr['BidPrice']), min(float(sr['LastPrice']), float(sr['AskPrice'])))
            ll = max(float(lr['BidPrice']), min(float(lr['LastPrice']), float(lr['AskPrice'])))
            return max(sl - ll, 0), expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    scored['raw_last'], scored['expiry_close'] = zip(*scored.apply(lookup, axis=1))
    scored = scored.dropna(subset=['raw_last','expiry_close']).copy()
    scored['credit'] = scored['raw_last'] * 0.85
    scored['width']  = scored['net_credit'] + scored['max_loss']
    scored['max_loss_adj'] = scored['width'] - scored['credit']
    scored['pnl_per_contract'] = scored.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    scored['max_loss_dollar'] = scored['max_loss_adj'] * 100
    scored['realize_date'] = pd.to_datetime(scored['expiry_date'])
    scored['entry_date_dt'] = pd.to_datetime(scored['entry_date'])
    scored['entry_dow'] = scored['entry_date_dt'].dt.dayofweek
    return scored


def build_full_cache():
    pool = er.load_master_pool()
    print(f'Loaded master pool: {len(pool):,} rows', flush=True)
    parts = []
    for y in YEARS:
        print(f'── {y} ──', flush=True)
        parts.append(score_year_full(y, pool))
    full = pd.concat(parts, ignore_index=True).sort_values('entry_date_dt').reset_index(drop=True)
    full.to_parquet(SCORED_CACHE)
    print(f'\nCached {len(full):,} scored candidates → {SCORED_CACHE}')
    return full


def simulate(picks):
    """qty=2 only, chronological."""
    bankroll = START_BANKROLL
    daily_pnl = {}
    for _, row in picks.iterrows():
        pnl = 2 * row['pnl_per_contract']
        rd = row['realize_date']
        daily_pnl[rd] = daily_pnl.get(rd, 0.0) + pnl
        bankroll += pnl
        if bankroll <= 0: bankroll = 1
    return pd.Series(daily_pnl).sort_index()


def eval_combo(scored, k, thr, spy_days):
    s = scored.copy()
    s['GROUND'] = (np.exp(s['G']) - 1.0) * np.exp(-k * s['DKL'])
    qual = s[s['GROUND'] >= thr]
    top = qual.sort_values(['entry_date_dt','GROUND'], ascending=[True,False]).groupby('entry_date_dt').head(5)
    if top.empty:
        return {'k':k,'thr':thr,'n_picks':0,'pnl':0,'final':START_BANKROLL,'sharpe':0,'sharpe_w':0,'max_dd':0,'yield':0}
    daily = simulate(top)
    eq = START_BANKROLL + daily.reindex(spy_days, fill_value=0.0).cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sd = ret.std(ddof=0)
    sh = float(ret.mean() * np.sqrt(252) / sd) if sd > 0 else 0.0
    weekly_eq = eq.resample('W-FRI').last().ffill()
    wr = weekly_eq.pct_change().dropna()
    wsd = wr.std(ddof=0)
    wsh = float(wr.mean() * np.sqrt(52) / wsd) if wsd > 0 else 0.0
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) * 100
    final = float(eq.iloc[-1])
    pnl = final - START_BANKROLL
    wagered = float((top['max_loss_dollar'] * 2).sum())
    yld = (pnl / wagered * 100) if wagered > 0 else 0.0
    return {'k':k,'thr':thr,'n_picks':int(len(top)),'pnl':round(pnl,0),'final':round(final,0),
            'sharpe':round(sh,2),'sharpe_w':round(wsh,2),'max_dd':round(dd,1),'yield':round(yld,2)}


def main():
    if os.path.exists(SCORED_CACHE):
        print(f'Loading cached scored candidates: {SCORED_CACHE}')
        scored = pd.read_parquet(SCORED_CACHE)
        print(f'  {len(scored):,} scored candidates loaded')
    else:
        scored = build_full_cache()

    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
    start = scored['entry_date_dt'].min().normalize()
    spy = spy[(spy['Date'] >= start) & (spy['Date'] <= pd.Timestamp(f'{YEARS[-1]}-12-31'))].reset_index(drop=True)
    spy_days = pd.DatetimeIndex(spy['Date'])

    print(f'\n══ k × thr sweep (qty=2, full 2020-08 → 2025-12) ══')
    print(f'  {len(scored):,} pre-filter scored candidates')
    print(f'  G range: {scored["G"].min():.4f} to {scored["G"].max():.4f}')
    print(f'  DKL range: {scored["DKL"].min():.4f} to {scored["DKL"].max():.4f}')

    rows = []
    for k in K_GRID:
        for thr in THR_GRID:
            r = eval_combo(scored, k, thr, spy_days)
            rows.append(r)
    df = pd.DataFrame(rows)

    print(f'\n── Sharpe (daily, annualized) ──')
    pv = df.pivot(index='k', columns='thr', values='sharpe')
    print(pv.to_string(float_format=lambda x: f'{x:+.2f}'))

    print(f'\n── Sharpe (weekly, annualized) ──')
    pv = df.pivot(index='k', columns='thr', values='sharpe_w')
    print(pv.to_string(float_format=lambda x: f'{x:+.2f}'))

    print(f'\n── Final $ (start $10k) ──')
    pv = df.pivot(index='k', columns='thr', values='final')
    print(pv.to_string(float_format=lambda x: f'{x:>7,.0f}'))

    print(f'\n── n_picks ──')
    pv = df.pivot(index='k', columns='thr', values='n_picks')
    print(pv.to_string(float_format=lambda x: f'{x:>5.0f}'))

    print(f'\n── Yield % (PnL / Wagered) ──')
    pv = df.pivot(index='k', columns='thr', values='yield')
    print(pv.to_string(float_format=lambda x: f'{x:+.2f}'))

    print(f'\n── Max DD % ──')
    pv = df.pivot(index='k', columns='thr', values='max_dd')
    print(pv.to_string(float_format=lambda x: f'{x:>6.1f}'))

    # Top-10 by weekly Sharpe (penalized by minimum pick threshold)
    print(f'\n── Top 10 combos by weekly Sharpe (min n_picks ≥ 100) ──')
    top10 = df[df['n_picks'] >= 100].nlargest(10, 'sharpe_w')
    print(top10.to_string(index=False))

    df.to_csv('output/sweep_qdown_results.csv', index=False)
    print(f'\nWrote output/sweep_qdown_results.csv')


if __name__ == '__main__':
    main()
