"""D(P_delta || Q_credit) — greeks-implied belief vs price-implied belief.

RESEARCH ONLY. Does not modify ground.py or canon.

Motivation: canon's D(P_rv || Q_iv) fails at delta-20 because the RV-implied
triple saturates to (1,0,0) on ~51% of candidates. Delta and credit are both
bounded and non-degenerate in the 0.10-0.30 band we actually select from.

Two triples, both 3-state (p=WIN, q=LOSS, ro=PARTIAL):

  DELTA-implied (model / greeks view):
      p  = 1 - |delta_short|          (finishes OTM of the short strike)
      q  = |delta_long|               (finishes beyond the long strike)
      ro = |delta_short| - |delta_long|

  CREDIT-implied (price view) — inverted from the spread's own price:
      Under the 3-state partition with partial loss at the midpoint,
      risk-neutral fair value gives   C/W = q + ro/2.
      Root-find the vol sigma_c satisfying that, then take the N(d2) triple
      at sigma_c. Uses ONLY bid/ask-derived credit — never the IV column.

DKL = D(P_delta || Q_credit): weights by the greeks view, penalises where the
PRICE assigns little probability to an outcome the GREEKS think is likely.
Large => you are being paid as if risk is low while the greeks say it is not
=> underpaid => genuine risk. Correct penalty direction for exp(-k*DKL).
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
CACHE = 'output/sweep_delta_credit_dkl.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.30; bt_config.MIN_OPEN_INTEREST = 100


def lg(x):
    return math.log(max(x, 1e-12), bt_config.LOG_BASE)


def triple_at_vol(row, sigma):
    return hp.nd2_probs_for_spread(
        short_strike=float(row['short_strike']), long_strike=float(row['long_strike']),
        spot=float(row['entry_price']), iv_short=sigma, iv_long=sigma,
        dte_days=int(row['DTE']), spread_type=row['spread_type'])


def credit_implied_triple(row):
    """Invert C/W = q + ro/2 for sigma via bisection; return (p,q,ro,sigma)."""
    W = float(row['net_credit']) + float(row['max_loss'])
    if W <= 0:
        return None
    target = float(row['net_credit']) / W
    if not (0.0 < target < 1.0):
        return None

    def f(sig):
        p, q, ro, _ = triple_at_vol(row, sig)
        if p is None:
            return None
        return (q + ro / 2.0) - target

    lo, hi = 0.01, 5.0
    flo, fhi = f(lo), f(hi)
    if flo is None or fhi is None:
        return None
    if flo > 0 or fhi < 0:          # target outside achievable range
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm is None:
            return None
        if fm < 0:
            lo = mid
        else:
            hi = mid
    sig = 0.5 * (lo + hi)
    p, q, ro, _ = triple_at_vol(row, sig)
    return (p, q, ro, sig)


def delta_triple(row):
    ds, dl = row.get('short_delta'), row.get('long_delta')
    if ds is None or dl is None or pd.isna(ds) or pd.isna(dl):
        return None
    ds, dl = abs(float(ds)), abs(float(dl))
    if not (0 < dl < ds < 1):
        return None
    return (1.0 - ds, dl, ds - dl)


def dkl_delta_credit(row):
    dt = delta_triple(row)
    ct = credit_implied_triple(row)
    if dt is None or ct is None:
        return np.nan, np.nan, np.nan
    p_d, q_d, ro_d = dt
    p_c, q_c, ro_c, sig_c = ct
    if None in (p_c, q_c, ro_c):
        return np.nan, np.nan, np.nan
    d = 0.0
    if p_d > 0 and p_c > 0:   d += p_d * lg(p_d / p_c)
    if q_d > 0 and q_c > 0:   d += q_d * lg(q_d / q_c)
    if ro_d > 0 and ro_c > 0: d += ro_d * lg(ro_d / ro_c)
    unsigned = max(0.0, d)
    # one-sided: only where the PRICE is more optimistic than the GREEKS
    r = 0.0
    if q_d > q_c and q_c > 0:  r += q_d * lg(q_d / q_c)
    if p_d < p_c and p_d > 0:  r += p_c * lg(p_c / p_d)
    return unsigned, max(0.0, r), sig_c


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
            if not er.install_window(pool, pd.Timestamp(dt)):
                hp._EMPIRICAL_TABLE = None
            parts.append(ground.score_candidates(s))
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
        res = sc.apply(dkl_delta_credit, axis=1, result_type='expand')
        sc['DKL_dc'] = res[0]; sc['DKL_dc_onesided'] = res[1]; sc['sigma_credit'] = res[2]
        keep = ['entry_date','expiry_date','ticker','spread_type','short_strike','long_strike',
                'short_delta','long_delta','net_credit','max_loss','width','G','DKL',
                'DKL_dc','DKL_dc_onesided','sigma_credit','IV','rv_30d','expiry_close','_outcome','pnl_80']
        out.append(sc[[k for k in keep if k in sc.columns]])
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


def sweep(R, col, label, spy):
    print(f'\n  {label}')
    print(f'    {"k":>4} {"picks":>6} {"win%":>6} {"P&L/ctr":>9} {"final(q1)":>11} {"Sh(wk)":>7} {"MaxDD":>7}')
    d0 = R.dropna(subset=[col])
    for k in KS:
        d = d0.copy()
        d['S'] = (np.exp(d.G) - 1.0) * np.exp(-k * d[col])
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
    ok = R.DKL_dc.notna()
    print(f'\nn={len(R):,}  DKL(delta||credit) computable on {100*ok.mean():.1f}%')
    print(f'  sigma_credit: median {R.sigma_credit.median():.3f}  (vs vendor IV median {R.IV.median():.3f})')
    print(f'  DKL_dc: median {R.DKL_dc.median():.4f}  p90 {R.DKL_dc.quantile(.9):.4f}  zero-rate {100*(R.DKL_dc<=1e-9).mean():.1f}%')
    print(f'  one-sided zero-rate {100*(R.DKL_dc_onesided<=1e-9).mean():.1f}%')
    print(f'  corr(DKL_dc, pnl)        = {R.DKL_dc.corr(R.pnl_80):+.4f}')
    print(f'  corr(DKL_dc_onesided,pnl)= {R.DKL_dc_onesided.corr(R.pnl_80):+.4f}')
    print(f'  corr(canon DKL, pnl)     = {R.DKL.corr(R.pnl_80):+.4f}')
    sweep(R, 'DKL', 'CANON D(P_rv||Q_iv)', spy)
    sweep(R, 'DKL_dc', 'NEW D(P_delta||Q_credit)', spy)
    sweep(R, 'DKL_dc_onesided', 'NEW one-sided D(P_delta||Q_credit)', spy)
