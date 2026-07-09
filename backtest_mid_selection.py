"""Validation: selection on MID-based credit (CREDIT_BASIS='mid'),
fills at 0.80 x mid. Compare vs k=12 canon (selection+fills on clamped LAST).
Runs both windows: 2020-25 backtest and 2026 OOT. Read-only vs canon caches;
writes its own caches. Does NOT touch live/data JSONs.
"""
import sys, math, json, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 12
START_BANKROLL = 10_000.0
THRESH_BY_DOW = {0: 0.075, 1: 0.075, 2: 0.075, 3: 0.075}
ACTIVE_DOWS = [0, 1, 2, 3]
FILL_FRAC = 0.80

bt_config.CREDIT_BASIS = "mid"

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


def score_year(year, min_entry_date):
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
    df = df[df['DataDate'] >= min_entry_date]
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
    dates = sorted(candidates['entry_date'].unique())
    for dt in dates:
        sub = candidates[candidates['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(POOL, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
        parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), df_idx_view, expiry_close


def realize(scored, df_idx_view, expiry_close):
    s = scored.copy()
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    parts = []
    for dow in ACTIVE_DOWS:
        thr = THRESH_BY_DOW[dow]
        sub = s[s['entry_dow'] == dow]
        qual = sub[sub['GROUND'] >= thr]
        top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return pd.DataFrame(columns=['entry_date','realize_date','pnl_per_contract','max_loss_dollar'])
    def lookup(row):
        pc = 'put' if row['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            sm = (float(sr['BidPrice']) + float(sr['AskPrice'])) / 2.0
            lm = (float(lr['BidPrice']) + float(lr['AskPrice'])) / 2.0
            return max(sm - lm, 0), expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_mid'], sel['expiry_close'] = zip(*sel.apply(lookup, axis=1))
    ok = sel.dropna(subset=['raw_mid','expiry_close']).copy()
    ok['credit'] = ok['raw_mid'] * FILL_FRAC
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl_per_contract'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    ok['max_loss_dollar'] = ok['max_loss_adj'] * 100
    ok['realize_date'] = pd.to_datetime(ok['expiry_date'])
    ok['entry_date_dt'] = pd.to_datetime(ok['entry_date'])
    return ok


def build_picks(years, min_entry_date, cache_path):
    if os.path.exists(cache_path):
        print(f'Loading cached picks from {cache_path}...')
        picks = pd.read_parquet(cache_path)
        picks['entry_date_dt'] = pd.to_datetime(picks['entry_date_dt'])
        return picks
    parts = []
    for year in years:
        print(f'-- {year} --', flush=True)
        scored, df_idx_view, expiry_close = score_year(year, min_entry_date)
        parts.append(realize(scored, df_idx_view, expiry_close))
    picks = pd.concat(parts, ignore_index=True).sort_values('entry_date_dt').reset_index(drop=True)
    print(f'  {len(picks):,} picks -- caching to {cache_path}')
    picks.to_parquet(cache_path)
    return picks


def _outcome_class(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        if sp > ss:  return 'WIN'
        if sp <= ls: return 'LOSS'
        return 'PARTIAL'
    else:
        if sp < ss:  return 'WIN'
        if sp >= ls: return 'LOSS'
        return 'PARTIAL'


def apply_partial_haircut(picks):
    picks = picks.copy()
    picks['_outcome'] = picks.apply(_outcome_class, axis=1)
    mask = (picks['_outcome'] == 'PARTIAL') & (picks['pnl_per_contract'] > 0)
    picks.loc[mask, 'pnl_per_contract'] *= 0.5
    return picks


def simulate(picks, qty):
    bankroll = START_BANKROLL
    daily_pnl = {}
    for _, row in picks.iterrows():
        pnl = qty * row['pnl_per_contract']
        rd = row['realize_date']
        daily_pnl[rd] = daily_pnl.get(rd, 0.0) + pnl
        bankroll += pnl
        if bankroll <= 0: bankroll = 1
    return pd.Series(daily_pnl).sort_index()


def stats(picks, qty, label, end_year):
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
    start_date = picks['entry_date_dt'].min().normalize()
    spy = spy[(spy['Date'] >= start_date) & (spy['Date'] <= pd.Timestamp(f'{end_year}-12-31'))].reset_index(drop=True)
    td = pd.DatetimeIndex(spy['Date'])
    pnl = simulate(picks, qty)
    eq = START_BANKROLL + pnl.reindex(td, fill_value=0.0).cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sd = ret.std(ddof=0)
    sh = float(ret.mean()*np.sqrt(252)/sd) if sd > 0 else 0.0
    weekly = eq.resample('W-FRI').last().ffill().pct_change().dropna()
    wsd = weekly.std(ddof=0)
    wsh = float(weekly.mean()*np.sqrt(52)/wsd) if wsd > 0 else 0.0
    peak = eq.cummax()
    dd = float(((eq-peak)/peak).min())
    final = float(eq.iloc[-1])
    print(f'  {label:<10} final ${final:>10,.0f}  ret {100*(final-START_BANKROLL)/START_BANKROLL:+8.1f}%  '
          f'Sh {sh:+.2f}  Sh(wk) {wsh:+.2f}  MaxDD {100*dd:.1f}%  n={len(picks)}')


if __name__ == '__main__':
    print('=== MID selection + 0.80 x mid fills ===')
    bt = build_picks([2020,2021,2022,2023,2024,2025], pd.Timestamp('2020-01-01'),
                     f'output/picks_cache_midsel_k{K_VAL}_thr075.parquet')
    bt = apply_partial_haircut(bt)
    print('\n2020-25 backtest:')
    stats(bt, 1, 'qty=1', 2025)
    stats(bt, 2, 'qty=2', 2025)

    oot = build_picks([2026], pd.Timestamp('2026-01-01'),
                      f'output/picks_cache_midsel_oot2026_k{K_VAL}_thr075.parquet')
    oot = apply_partial_haircut(oot)
    print('\n2026 OOT:')
    stats(oot, 1, 'qty=1', 2026)
    stats(oot, 2, 'qty=2', 2026)
