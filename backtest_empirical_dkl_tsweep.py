"""Sweep the empirical trailing window T (TRAIL_DAYS) for D(P_empirical || Q_iv).
Delta-20, 2020-2025, in-sample. RESEARCH ONLY — does not modify canon.

Key efficiency: under canon (PROB_BASIS='rv', DKL_REFERENCE='rv_vs_iv') neither
G nor the market side Q_iv depends on the empirical window, so candidates are
scored ONCE and only P_empirical is recomputed per T.

The per-date window build is the cost, so the pool is pre-sorted by
ExpirationDate and sliced with searchsorted instead of a full boolean scan of
15.6M rows per (date, T) pair. Bucketing, IV quintile bins and the n>=30
reliability floor replicate empirical_runner.build_window_tables exactly.
"""
import sys, os, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import historical_probs as hp
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
START = 10_000.0
ACTIVE = [0, 1, 2, 3]
THR, FILL = 0.05, 0.80
T_GRID = [45, 90, 140, 210, 300, 420, 560, 730]      # 210 = current canon
K_GRID = [6, 8, 10, 12, 16]
BASE = 'output/sweep_emp_dkl_base.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.30; bt_config.MIN_OPEN_INTEREST = 100


def lg(x):
    return math.log(max(x, 1e-12), bt_config.LOG_BASE)


# ---------- one-time scoring pass (T-independent) ----------
def build_base():
    if os.path.exists(BASE):
        print(f'Loading base {BASE}'); return pd.read_parquet(BASE)
    ivr = pd.read_parquet('output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr['DataDate'])
    rvt = pd.read_parquet('output/rv_table.parquet'); rvt['DataDate'] = pd.to_datetime(rvt['DataDate'])
    td = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    out = []
    for y in [2020, 2021, 2022, 2023, 2024, 2025]:
        print(f'-- {y} --', flush=True)
        d = pd.read_parquet(f'output/{y}_sp500_last.parquet'); d = d[d.Symbol.isin(SP100)]
        d['PutCall'] = d.PutCall.str.lower().str.strip()
        ec = (d[d.DataDate == d.ExpirationDate]
              .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
        d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
        d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
        d = d[(d.LastPrice.astype(float) > 0) & d.DataDate.isin(td)].copy()
        d['AbsDelta'] = d.Delta.abs(); d['MidPrice'] = (d.BidPrice + d.AskPrice) / 2
        d = d.merge(ivr[['Symbol', 'DataDate', 'iv_rank_bucket']], on=['Symbol', 'DataDate'], how='left')
        d = d.merge(rvt[['Symbol', 'DataDate', 'rv_30d']], on=['Symbol', 'DataDate'], how='left')
        spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
        c = spreads.build_candidates(d)
        if c.empty: continue
        hp._EMPIRICAL_TABLE = None            # G/DKL under canon don't need it
        sc = ground.score_candidates(c).dropna(subset=['G', 'DKL']).copy()
        # market side Q_iv — T-independent, compute once
        def q_iv(r):
            p, q, ro, _ = hp.nd2_probs_for_spread(
                short_strike=float(r['short_strike']), long_strike=float(r['long_strike']),
                spot=float(r['entry_price']), iv_short=float(r['IV']),
                iv_long=float(r.get('long_IV', r['IV'])),
                dte_days=int(r['DTE']), spread_type=r['spread_type'])
            return pd.Series({'p_iv': p, 'q_iv': q, 'ro_iv': ro})
        sc = pd.concat([sc, sc.apply(q_iv, axis=1)], axis=1)
        sc['expiry_close'] = sc.apply(lambda r: ec.get((r['ticker'], r['expiry_date'])), axis=1)
        sc = sc.dropna(subset=['expiry_close', 'p_iv']).copy()
        if sc.empty: continue
        sc['width'] = sc['net_credit'] + sc['max_loss']
        def _oc(r):
            sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
            if r['spread_type'] == 'bull_put':
                return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
            return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
        sc['_outcome'] = sc.apply(_oc, axis=1)
        cr = sc['net_credit'] * FILL; ml = sc['width'] - cr
        pnl = sc.apply(lambda r, c_=cr, m_=ml: spreads.calc_pnl(
            r['expiry_close'], r['short_strike'], r['long_strike'],
            c_.loc[r.name], m_.loc[r.name], r['spread_type']), axis=1) * 100
        msk = (sc['_outcome'] == 'PARTIAL') & (pnl > 0); pnl[msk] *= 0.5
        sc['pnl_80'] = pnl
        keep = ['entry_date','expiry_date','ticker','spread_type','short_strike','long_strike',
                'short_delta','long_delta','IV','long_IV','DTE','iv_rank_bucket','entry_price',
                'net_credit','max_loss','width','G','DKL','p_iv','q_iv','ro_iv',
                'expiry_close','_outcome','pnl_80']
        out.append(sc[[k for k in keep if k in sc.columns]])
        print(f'   {len(sc):,} scored', flush=True)
    R = pd.concat(out, ignore_index=True)
    R.to_parquet(BASE); print(f'wrote {BASE}: {len(R):,}')
    return R


# ---------- fast per-T empirical tables ----------
def make_tables(pool_sorted, exp_vals, asof, T):
    lo = asof - pd.Timedelta(days=T)
    i0 = np.searchsorted(exp_vals, lo.to_datetime64(), side='left')
    i1 = np.searchsorted(exp_vals, asof.to_datetime64(), side='left')
    if i1 <= i0: return None, None
    sub = pool_sorted.iloc[i0:i1]
    if sub.empty: return None, None
    iv_bins = sub['iv_capped'].quantile([0, .2, .4, .6, .8, 1.0]).values
    iv_bins[-1] += 0.001
    if not np.all(np.diff(iv_bins) > 0): return None, None
    sub = sub.copy()
    sub['iv_bucket'] = pd.cut(sub['iv_capped'], bins=iv_bins, labels=False, include_lowest=True)
    sub['iv_rank_bucket'] = sub['iv_rank_bucket'].fillna(-1).astype(int) if 'iv_rank_bucket' in sub else -1
    tables = {}
    for pc in ['put', 'call']:
        s = sub[sub['putcall_norm'] == pc]
        if s.empty: tables[pc] = None; continue
        agg = s.groupby(['DTE', 'delta_bucket', 'iv_bucket', 'iv_rank_bucket']).agg(
            n=('itm', 'size'), p_itm=('itm', 'mean')).reset_index()
        agg['p_itm_reliable'] = np.where(agg['n'] >= 30, agg['p_itm'], np.nan)
        tables[pc] = agg
    return tables, iv_bins


def dkl_for_row(r):
    ivr = r['iv_rank_bucket']
    ivr = None if pd.isna(ivr) else int(ivr)
    p_e, q_e, ro_e, _ = hp.empirical_lookup_probs(
        short_delta=float(r['short_delta']), long_delta=float(r['long_delta']),
        iv_short=float(r['IV']), iv_long=float(r['long_IV']),
        dte_days=int(r['DTE']), spread_type=r['spread_type'], iv_rank_bucket=ivr)
    if p_e is None: return np.nan
    p_i, q_i, ro_i = r['p_iv'], r['q_iv'], r['ro_iv']
    d = 0.0
    if p_e > 0 and p_i > 0:   d += p_e * lg(p_e / p_i)
    if q_e > 0 and q_i > 0:   d += q_e * lg(q_e / q_i)
    if ro_e > 0 and ro_i > 0: d += ro_e * lg(ro_e / ro_i)
    return max(0.0, d)


def metrics(sel, spy):
    s = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= pd.Timestamp('2025-12-31'))]
    tdi = pd.DatetimeIndex(s['Date'])
    eq = START + sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum().reindex(tdi, fill_value=0).cumsum()
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = wk.std(ddof=0)
    sh = float(wk.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    return len(sel), 100*(sel.pnl_80 > 0).mean(), float(eq.iloc[-1]), sh, dd


if __name__ == '__main__':
    R = build_base()
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
    td = set(spy['Date'].dt.normalize()); R = R[R.entry_date.dt.normalize().isin(td)].copy()
    pool = er.load_master_pool().sort_values('ExpirationDate').reset_index(drop=True)
    exp_vals = pool['ExpirationDate'].values
    print(f'\nbase n={len(R):,}  pool {len(pool):,}  dates {R.entry_date.nunique()}')
    dates = sorted(R.entry_date.unique())
    print(f'\n{"="*86}\nT sweep — D(P_emp||Q_iv), delta-20, 2020-2025 (210 = current canon)\n{"="*86}')
    print(f'  {"T(days)":>8} {"T(wks)":>7} {"k":>4} {"picks":>6} {"win%":>6} {"final(q1)":>11} {"Sh(wk)":>7} {"MaxDD":>7} {"cov%":>6}')
    for T in T_GRID:
        col = np.full(len(R), np.nan)
        idx_map = {d: np.where(R.entry_date.values == d)[0] for d in dates}
        for d in dates:
            tables, bins = make_tables(pool, exp_vals, pd.Timestamp(d), T)
            if tables is None: continue
            er.install_tables(tables, bins)
            ii = idx_map[d]
            sub = R.iloc[ii]
            col[ii] = sub.apply(dkl_for_row, axis=1).values
        R['DKL_T'] = col
        cov = 100 * np.isfinite(col).mean()
        best = None
        for k in K_GRID:
            d0 = R.dropna(subset=['DKL_T']).copy()
            d0['S'] = (np.exp(d0.G) - 1.0) * np.exp(-k * d0['DKL_T'])
            sel = (d0[d0.S >= THR].sort_values(['entry_date','S'], ascending=[True,False])
                   .groupby('entry_date').head(5))
            if sel.empty: continue
            n, w, f, sh, dd = metrics(sel, spy)
            if best is None or sh > best[4]: best = (k, n, w, f, sh, dd)
            print(f'  {T:>8} {T/7:>7.0f} {k:>4} {n:>6,} {w:>5.1f}% ${f:>10,.0f} {sh:>+7.2f} {dd:>6.1f}% {cov:>5.1f}%')
        if best: print(f'  {"":>8} {"":>7} best k={best[0]}: Sh {best[4]:+.2f}, ${best[3]:,.0f}, DD {best[5]:.1f}%')
        print()
