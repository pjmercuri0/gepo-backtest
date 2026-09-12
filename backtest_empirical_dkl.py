"""D(P_empirical || Q_iv) — measured breach frequency vs market price.

RESEARCH ONLY. Does not modify ground.py or canon.

Rationale: every DKL tested at delta-20 so far (rv_vs_iv, delta_vs_credit) has
been Black-Scholes vs Black-Scholes — same N(d2) functional form, different vol
input. They all behaved identically because you cannot extract information by
comparing a model to itself. The EMPIRICAL triple is the only non-BS
distribution available: measured breach frequencies from master_pool, bucketed
by (DTE, delta, IV, iv_rank). It cannot saturate to (1,0,0) the way RV does
(min cell 197 samples in the 0.10-0.30 band) and is calibrated to the outcome
by construction.

  P_empirical : historical_probs.empirical_lookup_probs, installed per entry
                date via empirical_runner.install_window (TRAILING window only,
                so it stays causal — no lookahead).
  Q_iv        : nd2_probs_for_spread at the quoted IV (the market's price).

DKL = D(P_emp || Q_iv), penalty sign preserved (k>0), order preserved
(truth left, market right). Also computes the one-sided form: only the
component where empirical says breach is MORE likely than the market prices.

G is left untouched (PROB_BASIS='rv'), so empirical enters only through DKL —
no double-counting of the same fit, per SESSION_HANDOFF 0.19.
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
KS = [0, 2, 4, 6, 8, 10, 12, 16, 20, 30]
CACHE = 'output/sweep_empirical_dkl.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.30; bt_config.MIN_OPEN_INTEREST = 100


def lg(x):
    return math.log(max(x, 1e-12), bt_config.LOG_BASE)


def emp_vs_iv(row):
    """Return (dkl_unsigned, dkl_onesided, p_emp, q_emp, ro_emp, p_iv, q_iv, ro_iv)."""
    nan8 = (np.nan,) * 8
    sd, ld = row.get('short_delta'), row.get('long_delta')
    if sd is None or ld is None or pd.isna(sd) or pd.isna(ld):
        return nan8
    ivr = row.get('iv_rank_bucket')
    ivr = None if (ivr is None or pd.isna(ivr)) else int(ivr)
    iv_s, iv_l = float(row['IV']), float(row.get('long_IV', row['IV']))
    if iv_s <= 0 or iv_l <= 0:
        return nan8
    # P: empirical (uses the window installed for this entry date)
    p_e, q_e, ro_e, _ = hp.empirical_lookup_probs(
        short_delta=float(sd), long_delta=float(ld), iv_short=iv_s, iv_long=iv_l,
        dte_days=int(row['DTE']), spread_type=row['spread_type'], iv_rank_bucket=ivr)
    if p_e is None:
        return nan8
    # Q: market price via N(d2) at quoted IV
    p_i, q_i, ro_i, _ = hp.nd2_probs_for_spread(
        short_strike=float(row['short_strike']), long_strike=float(row['long_strike']),
        spot=float(row['entry_price']), iv_short=iv_s, iv_long=iv_l,
        dte_days=int(row['DTE']), spread_type=row['spread_type'])
    if p_i is None:
        return nan8
    d = 0.0
    if p_e > 0 and p_i > 0:   d += p_e * lg(p_e / p_i)
    if q_e > 0 and q_i > 0:   d += q_e * lg(q_e / q_i)
    if ro_e > 0 and ro_i > 0: d += ro_e * lg(ro_e / ro_i)
    unsigned = max(0.0, d)
    # one-sided: empirical says the bad outcomes are MORE likely than priced
    r = 0.0
    if q_e > q_i and q_i > 0:  r += q_e * lg(q_e / q_i)
    if p_e < p_i and p_e > 0:  r += p_i * lg(p_i / p_e)
    return unsigned, max(0.0, r), p_e, q_e, ro_e, p_i, q_i, ro_i


def build():
    if os.path.exists(CACHE):
        print(f'Loading {CACHE}'); return pd.read_parquet(CACHE)
    ivr = pd.read_parquet('output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr['DataDate'])
    rvt = pd.read_parquet('output/rv_table.parquet'); rvt['DataDate'] = pd.to_datetime(rvt['DataDate'])
    td = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    pool = er.load_master_pool(); print(f'pool {len(pool):,}', flush=True)
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
        parts = []
        for dt in sorted(c.entry_date.unique()):
            s = c[c.entry_date == dt]
            if s.empty: continue
            # install the TRAILING empirical window for this date (causal)
            has_win = er.install_window(pool, pd.Timestamp(dt))
            if not has_win:
                hp._EMPIRICAL_TABLE = None
            sc = ground.score_candidates(s)
            # compute the empirical-vs-IV DKL under the SAME installed window
            res = sc.apply(emp_vs_iv, axis=1, result_type='expand')
            res.columns = ['DKL_emp', 'DKL_emp_onesided', 'p_emp', 'q_emp', 'ro_emp',
                           'p_iv', 'q_iv', 'ro_iv']
            parts.append(pd.concat([sc, res], axis=1))
        sc = pd.concat(parts, ignore_index=True).dropna(subset=['G', 'DKL']).copy()
        sc['expiry_close'] = sc.apply(lambda r: ec.get((r['ticker'], r['expiry_date'])), axis=1)
        sc = sc.dropna(subset=['expiry_close']).copy()
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
                'short_delta','long_delta','net_credit','max_loss','width','G','DKL',
                'DKL_emp','DKL_emp_onesided','p_emp','q_emp','ro_emp','p_iv','q_iv','ro_iv',
                'IV','rv_30d','expiry_close','_outcome','pnl_80']
        out.append(sc[[k for k in keep if k in sc.columns]])
        print(f'   {len(sc):,} scored', flush=True)
    R = pd.concat(out, ignore_index=True)
    R.to_parquet(CACHE); print(f'wrote {CACHE}: {len(R):,}')
    return R


def metrics(sel, spy):
    s = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= pd.Timestamp('2025-12-31'))]
    tdi = pd.DatetimeIndex(s['Date'])
    eq = START + sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum().reindex(tdi, fill_value=0).cumsum()
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = wk.std(ddof=0)
    sh = float(wk.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    return dict(n=len(sel), win=100*(sel.pnl_80>0).mean(), per=sel.pnl_80.mean(),
                final=float(eq.iloc[-1]), sh=sh, dd=dd)


def sweep(R, col, label, spy):
    print(f'\n  {label}')
    print(f'    {"k":>4} {"picks":>6} {"win%":>6} {"P&L/ctr":>9} {"final(q1)":>11} {"Sh(wk)":>7} {"MaxDD":>7}')
    d0 = R.dropna(subset=[col])
    for k in KS:
        d = d0.copy(); d['S'] = (np.exp(d.G) - 1.0) * np.exp(-k * d[col])
        sel = (d[d.S >= THR].sort_values(['entry_date','S'], ascending=[True,False])
               .groupby('entry_date').head(5))
        if sel.empty: print(f'    {k:>4}  no trades'); continue
        m = metrics(sel, spy)
        print(f'    {k:>4} {m["n"]:>6,} {m["win"]:>5.1f}% {m["per"]:>9.2f} ${m["final"]:>10,.0f} {m["sh"]:>+7.2f} {m["dd"]:>6.1f}%')


if __name__ == '__main__':
    R = build()
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
    td = set(spy['Date'].dt.normalize()); R = R[R.entry_date.dt.normalize().isin(td)]
    ok = R.DKL_emp.notna()
    print(f'\nn={len(R):,}  D(P_emp||Q_iv) computable on {100*ok.mean():.1f}%')
    E = R[ok]
    act_p = (E._outcome == 'WIN').mean(); act_q = (E._outcome == 'LOSS').mean(); act_ro = (E._outcome == 'PARTIAL').mean()
    print(f'  CALIBRATION (mean triple vs ACTUAL):')
    print(f'    empirical P : p={E.p_emp.mean():.4f} q={E.q_emp.mean():.4f} ro={E.ro_emp.mean():.4f}')
    print(f'    market   Q  : p={E.p_iv.mean():.4f} q={E.q_iv.mean():.4f} ro={E.ro_iv.mean():.4f}')
    print(f'    ACTUAL      : p={act_p:.4f} q={act_q:.4f} ro={act_ro:.4f}')
    print(f'  saturation: p_emp==1 on {100*(E.p_emp>=0.9999).mean():.1f}%  (RV-implied was 51%)')
    print(f'  DKL_emp: median {E.DKL_emp.median():.4f}  p90 {E.DKL_emp.quantile(.9):.4f}  zero {100*(E.DKL_emp<=1e-9).mean():.1f}%')
    print(f'  one-sided fires on {100*(E.DKL_emp_onesided>1e-9).mean():.1f}%  (rv one-sided fired on 14%)')
    print(f'  corr(DKL_emp, pnl)          = {E.DKL_emp.corr(E.pnl_80):+.4f}')
    print(f'  corr(DKL_emp_onesided, pnl) = {E.DKL_emp_onesided.corr(E.pnl_80):+.4f}')
    print(f'  corr(canon DKL, pnl)        = {E.DKL.corr(E.pnl_80):+.4f}')
    sweep(R, 'DKL', 'CANON D(P_rv||Q_iv)', spy)
    sweep(R, 'DKL_emp', 'NEW D(P_empirical||Q_iv)', spy)
    sweep(R, 'DKL_emp_onesided', 'NEW one-sided D(P_empirical||Q_iv)', spy)
