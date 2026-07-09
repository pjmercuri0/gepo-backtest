"""SMA sweep: score the full backtest under SMA = 50, 100 (current), 200.

For each SMA value, runs the canonical scoring pipeline across years
2020 (H2 only) - 2025 and reports the equity-Sharpe + final-bankroll
under qty=2 sizing.

Reuses the master pool and per-date rolling lookup (W=30, k=50, thr=0.030,
forward DKL). Only the regime gate (which spreads are built) changes
between SMA values.
"""
import sys, math, time
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
YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
MIN_ENTRY = pd.Timestamp('2020-07-01')

SMAS = [50, 100, 200]


def score_year_sma(year, pool, sma_window):
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

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=sma_window)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    cand = spreads.build_candidates(df)
    if year == 2020:
        cand = cand[pd.to_datetime(cand['entry_date']) >= MIN_ENTRY]
    if cand.empty: return pd.DataFrame()

    parts = []
    dates = sorted(cand['entry_date'].unique())
    for dt in dates:
        sub = cand[cand['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(pool, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
        parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if scored.empty: return scored

    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G) - 1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    scored = scored.dropna(subset=['GROUND'])

    # Realize per-pick PnL
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
    ok['realize_date']  = pd.to_datetime(ok['expiry_date'])
    ok['entry_date_dt'] = pd.to_datetime(ok['entry_date'])
    return ok


print('Loading master pool...', flush=True)
pool = er.load_master_pool()
print(f'  pool: {len(pool):,} rows', flush=True)

spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
results = {}
for sma in SMAS:
    print(f'\n══ SMA={sma} ══', flush=True)
    t0 = time.time()
    all_picks = []
    for year in YEARS:
        print(f'  scoring {year}...', flush=True)
        y_picks = score_year_sma(year, pool, sma)
        if not y_picks.empty:
            all_picks.append(y_picks)
            print(f'    {len(y_picks)} picks', flush=True)
    picks = pd.concat(all_picks, ignore_index=True).sort_values('entry_date_dt').reset_index(drop=True)

    # qty=2 sizing
    picks['pnl'] = 2 * picks['pnl_per_contract']

    # Anchor curve at first entry
    start_date = picks['entry_date_dt'].min().normalize()
    end_date = pd.Timestamp(f'{YEARS[-1]}-12-31')
    spy_sub = spy[(spy['Date'] >= start_date) & (spy['Date'] <= end_date)].reset_index(drop=True)
    TD = pd.DatetimeIndex(spy_sub['Date'])

    daily = picks.groupby('realize_date')['pnl'].sum().reindex(TD, fill_value=0.0)
    eq = START_BANKROLL + daily.cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sh = float(ret.mean() * np.sqrt(252) / ret.std(ddof=0)) if ret.std(ddof=0) > 0 else 0
    peak = eq.cummax(); dd = ((eq - peak) / peak).min()
    final = float(eq.iloc[-1])
    n_years = (TD[-1] - TD[0]).days / 365.25
    cagr = ((final / START_BANKROLL) ** (1/n_years) - 1) * 100
    results[sma] = dict(picks=len(picks), final=final, cagr=cagr, sharpe=sh, dd=dd*100,
                        elapsed=time.time()-t0)
    print(f'  SMA={sma}: {len(picks)} picks · ${final:,.0f} · CAGR {cagr:+.1f}% · '
          f'Sharpe {sh:+.2f} · DD {dd*100:.1f}% · ({time.time()-t0:.0f}s)', flush=True)

print('\n══ SMA sweep summary ══')
print(f'{"SMA":>4} {"picks":>6} {"final":>10} {"CAGR":>7} {"Sharpe":>7} {"MaxDD":>7}')
print('-' * 50)
for sma, r in results.items():
    print(f'{sma:>4} {r["picks"]:>6} ${r["final"]:>+8,.0f} {r["cagr"]:>+6.1f}% Sh{r["sharpe"]:>+5.2f} {r["dd"]:>+6.1f}%')
