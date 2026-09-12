"""One-sided (RISK-directional) DKL vs canonical unsigned DKL, at delta-20,
crossed with an IV-contamination filter. 2020-2025, in-sample. RESEARCH ONLY —
does not modify ground.py or any canon.

Canonical DKL is D(P_rv || Q_iv), an UNSIGNED divergence: it fires equally when
IV >> RV (seller overpaid = good) and when RV >> IV (real danger). Since IV
exceeds RV ~96% of the time, the canonical penalty exp(-k*DKL) mostly punishes
being overpaid.

ONE-SIDED DKL counts only the direction that hurts a credit seller — reality
(RV) says you lose MORE often than the market (IV) is pricing:

    dkl_risk = q_rv*ln(q_rv/q_iv)   if q_rv > q_iv   (loss more likely than priced)
             + p_iv*ln(p_iv/p_rv)   if p_rv < p_iv   (win less likely than priced)

Non-negative, zero when merely overpaid, so exp(-k*dkl_risk) keeps the PENALTY
sign but now penalizes genuine risk instead of the VRP edge.

p = P(WIN), q = P(LOSS), ro = P(partial)  [project canon].
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
KS = [0, 2, 4, 6, 8, 10, 12, 16, 20]
CACHE = 'output/sweep_delta20_signed_dkl.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.30; bt_config.MIN_OPEN_INTEREST = 100


def lg(x):
    return math.log(max(x, 1e-10), bt_config.LOG_BASE)


def probs(row, iv_s, iv_l):
    return hp.nd2_probs_for_spread(
        short_strike=float(row['short_strike']), long_strike=float(row['long_strike']),
        spot=float(row['entry_price']), iv_short=iv_s, iv_long=iv_l,
        dte_days=int(row['DTE']), spread_type=row['spread_type'])


def dkl_variants(row):
    """Return (dkl_unsigned_recomputed, dkl_onesided). NaN if inputs unusable."""
    rv = row.get('rv_30d'); iv_s = float(row['IV']); iv_l = float(row.get('long_IV', row['IV']))
    if rv is None or pd.isna(rv) or rv <= 0 or iv_s <= 0 or iv_l <= 0:
        return np.nan, np.nan
    rv = min(max(float(rv), 0.05), 2.0)          # same clamp as ground.py
    p_iv, q_iv, ro_iv, _ = probs(row, iv_s, iv_l)
    p_rv, q_rv, ro_rv, _ = probs(row, rv, rv)
    if None in (p_iv, p_rv):
        return np.nan, np.nan
    # canonical unsigned D(P_rv || Q_iv)
    d = 0.0
    if p_rv > 0 and p_iv > 0:  d += p_rv * lg(p_rv / p_iv)
    if q_rv > 0 and q_iv > 0:  d += q_rv * lg(q_rv / q_iv)
    if ro_rv > 0 and ro_iv > 0: d += ro_rv * lg(ro_rv / ro_iv)
    unsigned = max(0.0, d)
    # one-sided: only the seller-unfavorable direction
    r = 0.0
    if q_rv > q_iv and q_rv > 0 and q_iv > 0:  r += q_rv * lg(q_rv / q_iv)
    if p_rv < p_iv and p_rv > 0 and p_iv > 0:  r += p_iv * lg(p_iv / p_rv)
    return unsigned, max(0.0, r)


def build():
    if os.path.exists(CACHE):
        print(f'Loading {CACHE}'); return pd.read_parquet(CACHE)
    ivr = pd.read_parquet('output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr['DataDate'])
    rvt = pd.read_parquet('output/rv_table.parquet'); rvt['DataDate'] = pd.to_datetime(rvt['DataDate'])
    tdays = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    pool = er.load_master_pool(); print(f'pool {len(pool):,}', flush=True)
    out = []
    for y in [2020, 2021, 2022, 2023, 2024, 2025]:
        print(f'-- {y} --', flush=True)
        d = pd.read_parquet(f'output/{y}_sp500_last.parquet')
        d = d[d.Symbol.isin(SP100)]
        d['PutCall'] = d.PutCall.str.lower().str.strip()
        exp_close = (d[d.DataDate == d.ExpirationDate]
                     .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
        d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
        d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
        d = d[(d.LastPrice.astype(float) > 0) & d.DataDate.isin(tdays)].copy()
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
            if not er.install_window(pool, pd.Timestamp(dt)):
                hp._EMPIRICAL_TABLE = None
            parts.append(ground.score_candidates(s))
        sc = pd.concat(parts, ignore_index=True).dropna(subset=['G', 'DKL']).copy()
        sc['expiry_close'] = sc.apply(lambda r: exp_close.get((r['ticker'], r['expiry_date'])), axis=1)
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
        dv = sc.apply(dkl_variants, axis=1, result_type='expand')
        sc['DKL_unsigned'] = dv[0]; sc['DKL_onesided'] = dv[1]
        keep = ['entry_date','expiry_date','ticker','spread_type','short_strike','long_strike',
                'net_credit','max_loss','width','G','DKL','DKL_unsigned','DKL_onesided',
                'IV','long_IV','rv_30d','expiry_close','_outcome','pnl_80']
        out.append(sc[[c_ for c_ in keep if c_ in sc.columns]])
        print(f'   {len(sc):,} scored', flush=True)
    R = pd.concat(out, ignore_index=True)
    R.to_parquet(CACHE); print(f'wrote {CACHE}: {len(R):,}')
    return R


def metrics(sel, spy):
    s = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= pd.Timestamp('2025-12-31'))]
    tdi = pd.DatetimeIndex(s['Date'])
    daily = sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum()
    eq = START + daily.reindex(tdi, fill_value=0.0).cumsum()
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = wk.std(ddof=0)
    sh = float(wk.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    return dict(n=len(sel), win=100*(sel.pnl_80>0).mean(), per=sel.pnl_80.mean(),
                final=float(eq.iloc[-1]), sh=sh, dd=dd)


def sweep(R, dkl_col, label, spy):
    print(f'\n  {label}')
    print(f'    {"k":>4} {"picks":>6} {"win%":>6} {"P&L/ctr":>9} {"final(q1)":>11} {"Sh(wk)":>7} {"MaxDD":>7}')
    for k in KS:
        d = R.dropna(subset=[dkl_col]).copy()
        d['S'] = (np.exp(d.G) - 1.0) * np.exp(-k * d[dkl_col])
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
    clean = R[(R.IV > 0.05) & (R.IV <= 2.0) & (R.long_IV > 0.05) & (R.long_IV <= 2.0)]
    print(f'\npool n={len(R):,}   IV-clean (0.05<IV<=2.0 both legs) n={len(clean):,} ({100*len(clean)/len(R):.0f}%)')
    print(f'corr(unsigned DKL, pnl) = {R.DKL_unsigned.corr(R.pnl_80):+.4f}    '
          f'corr(one-sided DKL, pnl) = {R.DKL_onesided.corr(R.pnl_80):+.4f}')
    print(f'one-sided DKL is ZERO (merely overpaid) on {100*(R.DKL_onesided<=1e-9).mean():.1f}% of candidates')
    for name, sub in [('FULL POOL', R), ('IV-FILTERED (0.05<IV<=2.0)', clean)]:
        print(f'\n{"="*78}\n{name}  n={len(sub):,}\n{"="*78}')
        sweep(sub, 'DKL', 'CANONICAL unsigned DKL (ground.py value)', spy)
        sweep(sub, 'DKL_onesided', 'ONE-SIDED risk DKL', spy)
