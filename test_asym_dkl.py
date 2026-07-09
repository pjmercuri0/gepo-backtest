"""Quick test of asymmetric DKL hypothesis.

For 2023-2025 candidates, compute:
  - standard forward DKL: D(P_hist || Q_delta) — penalizes any divergence
  - asymmetric DKL: only penalize 'bad' divergence (analog of downside variance)
    * p_emp < p_delta → penalize (history says win less often)
    * q_emp > q_delta → penalize (history says lose more often)
    * ro: symmetric (partial-zone uncertainty)

Compare equity Sharpe under canonical thr+top-5 selection.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
YEARS = [2023, 2024, 2025]
ACTIVE_DOWS = [0, 1, 2, 3]
THRESH_BY_DOW = {0:0.030, 1:0.030, 2:0.030, 3:0.030}
K_VAL = 50
START_BANKROLL = 10_000.0
QTY = 2

print('Loading master pool...', flush=True)
POOL = er.load_master_pool()
print(f'  {len(POOL):,} rows', flush=True)


def score_year_with_emp(year):
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

    cand = spreads.build_candidates(df)
    parts = []
    dates = sorted(cand['entry_date'].unique())
    t0 = time.time()
    for i, dt in enumerate(dates):
        sub = cand[cand['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(POOL, pd.Timestamp(dt))

        # Capture both standard and asymmetric DKL plus empirical (p, q, ro)
        import historical_probs as hp
        rows = []
        for _, r in sub.iterrows():
            sd = abs(float(r['short_delta'])); ld = abs(float(r['long_delta']))
            if ld > sd: ld = sd
            p_d = 1.0 - sd; q_d = ld; ro_d = max(0.0, sd - ld)
            s = p_d + q_d + ro_d
            if s > 0: p_d, q_d, ro_d = p_d/s, q_d/s, ro_d/s

            # Empirical lookup
            p_e, q_e, ro_e, _ = hp.empirical_lookup_probs(
                short_delta=r['short_delta'], long_delta=r['long_delta'],
                iv_short=float(r['IV']), iv_long=float(r.get('long_IV', r['IV'])),
                dte_days=int(r['DTE']), spread_type=r['spread_type'])

            # Standard forward DKL
            dkl_std = 0.0
            if p_e and p_e > 0 and p_d > 0: dkl_std += p_e * math.log(p_e / p_d)
            if q_e and q_e > 0 and q_d > 0: dkl_std += q_e * math.log(q_e / q_d)
            if ro_e and ro_e > 0 and ro_d > 0: dkl_std += ro_e * math.log(ro_e / ro_d)
            dkl_std = max(0.0, dkl_std)

            # Asymmetric DKL: only penalize bad divergences
            dkl_asym = 0.0
            if p_e and p_e > 0 and p_d > 0 and p_e < p_d:
                dkl_asym += p_e * math.log(p_d / p_e)  # positive contribution
            if q_e and q_e > 0 and q_d > 0 and q_e > q_d:
                dkl_asym += q_e * math.log(q_e / q_d)  # positive contribution
            if ro_e and ro_e > 0 and ro_d > 0:
                dkl_asym += abs(ro_e * math.log(ro_e / ro_d))  # symmetric

            # Kelly EV from delta-derived
            b = float(r['net_credit']) / float(r['max_loss']) if r['max_loss'] > 0 else 0
            alpha = (b - 1.0) / (2.0 * b) if b > 0 else 0
            EV = p_d * b + ro_d * alpha * b - q_d
            G = math.log(EV) if EV > 0 else float('nan')

            rows.append({
                'entry_date': r['entry_date'],
                'expiry_date': r['expiry_date'],
                'ticker': r['ticker'],
                'spread_type': r['spread_type'],
                'short_strike': r['short_strike'],
                'long_strike': r['long_strike'],
                'net_credit': r['net_credit'],
                'max_loss': r['max_loss'],
                'w_star': r.get('w_star'),
                'p_d': p_d, 'q_d': q_d, 'ro_d': ro_d,
                'p_e': p_e if p_e else float('nan'),
                'q_e': q_e if q_e else float('nan'),
                'ro_e': ro_e if ro_e else float('nan'),
                'G': G,
                'dkl_std': dkl_std,
                'dkl_asym': dkl_asym,
            })
        parts.append(pd.DataFrame(rows))
        if (i+1) % 25 == 0:
            print(f'      scored {i+1}/{len(dates)} dates ({time.time()-t0:.0f}s)', flush=True)
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    print(f'    scored {len(scored):,} candidates', flush=True)

    # Realize PnL
    s = scored.copy()
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
    s['raw_last'], s['expiry_close'] = zip(*s.apply(lookup, axis=1))
    ok = s.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last'] * 0.85
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl_per_contract'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    ok['realize_date'] = pd.to_datetime(ok['expiry_date'])
    return ok


all_picks = []
for y in YEARS:
    print(f'\n── {y} ──', flush=True)
    yp = score_year_with_emp(y)
    if not yp.empty:
        all_picks.append(yp)
R = pd.concat(all_picks, ignore_index=True)
print(f'\nTotal: {len(R):,} candidates')

# Compute GROUND under both DKL formulas
R['G_std']  = np.where(pd.notna(R['G']), (np.exp(R['G']) - 1) * np.exp(-K_VAL * R['dkl_std']), np.nan)
R['G_asym'] = np.where(pd.notna(R['G']), (np.exp(R['G']) - 1) * np.exp(-K_VAL * R['dkl_asym']), np.nan)
R['entry_dow'] = pd.to_datetime(R['entry_date']).dt.dayofweek

SPY = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
SPY = SPY[(SPY['Date']>=pd.Timestamp('2023-01-01'))&(SPY['Date']<=pd.Timestamp('2025-12-31'))]
TD = pd.DatetimeIndex(SPY['Date'])

def select_and_realize(R, gcol):
    parts = []
    for dow in ACTIVE_DOWS:
        thr = THRESH_BY_DOW[dow]
        sub = R[(R['entry_dow']==dow) & (R[gcol] >= thr)]
        top = sub.sort_values(['entry_date', gcol], ascending=[True, False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return sel

def equity_summary(sel, label):
    if sel.empty:
        print(f'  {label}: no picks'); return
    sel = sel.copy(); sel['pnl'] = QTY * sel['pnl_per_contract']
    daily = sel.groupby('realize_date')['pnl'].sum().reindex(TD, fill_value=0.0)
    eq = START_BANKROLL + daily.cumsum()
    ret = eq.diff().fillna(0) / eq.shift(1).fillna(START_BANKROLL)
    sd = ret.std(ddof=0)
    sh = float(ret.mean()*np.sqrt(252)/sd) if sd>0 else 0
    weekly_eq = eq.resample('W-FRI').last().ffill()
    weekly_ret = weekly_eq.pct_change().dropna()
    wsh = float(weekly_ret.mean()*np.sqrt(52)/weekly_ret.std(ddof=0))
    peak = eq.cummax(); dd = ((eq-peak)/peak).min()*100
    print(f'  {label:<22} n={len(sel):>4}  ${eq.iloc[-1]:>+9,.0f}  pnl ${sel["pnl"].sum():>+8,.0f}  '
          f'win {100*(sel["pnl"]>0).mean():>4.1f}%  Sh{sh:+.2f}  wkSh{wsh:+.2f}  DD{dd:+.1f}%')

print('\n══ Comparison: standard vs asymmetric DKL (2023-2025, qty=2) ══')
print('Standard DKL @ thr=0.030 baseline:')
equity_summary(select_and_realize(R, 'G_std'),  'Std  thr=0.030')

print('\nAsymmetric DKL threshold sweep:')
def sweep_thresh(R, gcol, thresholds):
    for thr in thresholds:
        sub = R.copy()
        # Pass thr to selection
        parts = []
        for dow in ACTIVE_DOWS:
            s = sub[(sub['entry_dow']==dow) & (sub[gcol] >= thr)]
            top = s.sort_values(['entry_date', gcol], ascending=[True, False]).groupby('entry_date').head(5)
            parts.append(top)
        sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        equity_summary(sel, f'Asym thr={thr:.4f}')

sweep_thresh(R, 'G_asym', [0.005, 0.010, 0.015, 0.020, 0.025, 0.030])

# Also save realized data for further analysis
R.to_parquet('output/asym_dkl_realized.parquet', index=False)
print(f'\nSaved: output/asym_dkl_realized.parquet')

# Original overlap
sel_std = select_and_realize(R, 'G_std')
sel_asym = select_and_realize(R, 'G_asym')

# Overlap analysis
key_cols = ['entry_date','ticker','spread_type','short_strike']
std_set  = set(map(tuple, sel_std[key_cols].values))
asym_set = set(map(tuple, sel_asym[key_cols].values))
overlap = len(std_set & asym_set)
print(f'\nOverlap: {overlap} picks shared / {len(std_set)} std-only={len(std_set - asym_set)}, asym-only={len(asym_set - std_set)}')
