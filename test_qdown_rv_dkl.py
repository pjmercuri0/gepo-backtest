"""Asymmetric DKL test on the mid-basis canon: 'rv_vs_iv_qdown'.

Construction (mirrors the old q_down_ro_sym, but P=RV-implied, Q=IV-implied):
  - p leg: NO penalty (win-prob disagreement is the VRP edge G_rv monetizes)
  - q leg: penalize ONLY when q_rv > q_iv (RV says full loss MORE likely than
    the market prices -> genuine danger signal)
  - ro leg: symmetric |ro_rv * ln(ro_rv/ro_iv)|

G is DKL-flavor-independent, so we reuse the sweep caches (G, pnl_80) and only
recompute DKL by rejoining candidates to the vendor parquets for spot/IV/DTE.
Sanity check: recomputed SYMMETRIC DKL must match the cached DKL.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import historical_probs as hp

SPY_CSV = 'data/spy_us_d.csv'
START = 10_000.0
K_VALS = [4, 6, 8, 10, 12, 16, 20, 25, 30]
THRESHOLDS = [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.075, 0.09, 0.10]

_spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')

RV = pd.read_parquet('output/rv_table.parquet')
RV['DataDate'] = pd.to_datetime(RV['DataDate'])
RV_IDX = RV.set_index(['Symbol','DataDate'])['rv_30d']


def attach_probs(cache_path, years):
    C = pd.read_parquet(cache_path)
    C['entry_date'] = pd.to_datetime(C['entry_date'])
    C['expiry_date'] = pd.to_datetime(C['expiry_date'])
    C['pc'] = np.where(C['spread_type']=='bull_put', 'put', 'call')
    leg_parts = []
    for year in years:
        df = pd.read_parquet(f'output/{year}_sp500_last.parquet',
                             columns=['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall',
                                      'ImpliedVolatility','UnderlyingPrice','DTE'])
        df['DataDate'] = pd.to_datetime(df['DataDate'])
        df['ExpirationDate'] = pd.to_datetime(df['ExpirationDate'])
        leg_parts.append(df)
    legs = pd.concat(leg_parts, ignore_index=True).drop_duplicates(
        subset=['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall'])
    print(f'  leg table: {len(legs):,} rows', flush=True)
    short_legs = legs.rename(columns={'ImpliedVolatility':'iv_s','UnderlyingPrice':'spot','DTE':'dte'})
    C = C.merge(short_legs,
                left_on=['entry_date','ticker','expiry_date','short_strike','pc'],
                right_on=['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall'],
                how='left').drop(columns=['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall'])
    long_legs = legs[['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall','ImpliedVolatility']].rename(
        columns={'ImpliedVolatility':'iv_l'})
    C = C.merge(long_legs,
                left_on=['entry_date','ticker','expiry_date','long_strike','pc'],
                right_on=['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall'],
                how='left').drop(columns=['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall'])
    C = C.merge(RV.rename(columns={'Symbol':'ticker','DataDate':'entry_date','rv_30d':'rv'})[['ticker','entry_date','rv']],
                on=['ticker','entry_date'], how='left')
    for col in ('iv_s','iv_l','spot','rv'):
        C[col] = pd.to_numeric(C[col], errors='coerce')
    C = C.dropna(subset=['iv_s','iv_l','spot','rv','dte']).copy()
    print(f'  joined: {len(C):,} candidates with legs+rv', flush=True)

    def triplets(r):
        rv = min(max(r['rv'], 0.05), 2.0)
        piv = hp.nd2_probs_for_spread(short_strike=r['short_strike'], long_strike=r['long_strike'],
                                      spot=r['spot'], iv_short=r['iv_s'], iv_long=r['iv_l'],
                                      dte_days=int(r['dte']), spread_type=r['spread_type'])
        prv = hp.nd2_probs_for_spread(short_strike=r['short_strike'], long_strike=r['long_strike'],
                                      spot=r['spot'], iv_short=rv, iv_long=rv,
                                      dte_days=int(r['dte']), spread_type=r['spread_type'])
        return prv[:3] + piv[:3]
    C[['p_rv','q_rv','ro_rv','p_iv','q_iv','ro_iv']] = pd.DataFrame(
        C.apply(triplets, axis=1).tolist(), index=C.index)
    C = C.dropna(subset=['p_rv','p_iv']).copy()

    def dkl_sym(r):
        d = 0.0
        for a, b in ((r['p_rv'],r['p_iv']), (r['q_rv'],r['q_iv']), (r['ro_rv'],r['ro_iv'])):
            if a > 0 and b > 0: d += a*math.log(a/b)
        return max(0.0, d)
    def dkl_qdown(r):
        d = 0.0
        if r['q_rv'] > 0 and r['q_iv'] > 0 and r['q_rv'] > r['q_iv']:
            d += r['q_rv']*math.log(r['q_rv']/r['q_iv'])
        if r['ro_rv'] > 0 and r['ro_iv'] > 0:
            d += abs(r['ro_rv']*math.log(r['ro_rv']/r['ro_iv']))
        return max(0.0, d)
    C['DKL_sym_check'] = C.apply(dkl_sym, axis=1)
    C['DKL_qdown'] = C.apply(dkl_qdown, axis=1)
    corr = C[['DKL','DKL_sym_check']].corr().iloc[0,1]
    md = (C['DKL'] - C['DKL_sym_check']).abs().median()
    print(f'  sanity: recomputed symmetric DKL vs cached — corr {corr:.4f}, median |diff| {md:.5f} (n={len(C):,})')
    return C


def evaluate(R, k, thr, start, end, dkl_col):
    g = (np.exp(R['G'])-1.0)*np.exp(-k*R[dkl_col])
    sub = R[g >= thr].copy()
    if sub.empty: return None
    sub['GAM'] = g[g >= thr]
    sel = sub.sort_values(['entry_date','GAM'],ascending=[True,False]).groupby('entry_date').head(5)
    spy = _spy[(_spy['Date']>=start)&(_spy['Date']<=end)]
    td = pd.DatetimeIndex(spy['Date'])
    daily = sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum()
    eq = START + daily.reindex(td, fill_value=0.0).cumsum()
    ret = eq.diff().fillna(0)/eq.shift(1).fillna(START)
    sd = ret.std(ddof=0); sh = float(ret.mean()*np.sqrt(252)/sd) if sd>0 else 0
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna()
    wsd = wk.std(ddof=0); wsh = float(wk.mean()*np.sqrt(52)/wsd) if wsd>0 else 0
    dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return dict(k=k, thr=thr, n=len(sel), final=float(eq.iloc[-1]), sh=sh, wsh=wsh,
                dd=100*dd, win=100*(sel['pnl_80']>0).mean())


if __name__ == '__main__':
    print('Attaching RV/IV prob triplets to 2020-25 cache...')
    BT = attach_probs('output/sweep_midmkt_2020_25.parquet', [2020,2021,2022,2023,2024,2025])
    BT.to_parquet('output/sweep_midmkt_2020_25_probs.parquet')
    s, e = BT['entry_date'].min(), pd.Timestamp('2025-12-31')

    rows = []
    for k in K_VALS:
        for thr in THRESHOLDS:
            r = evaluate(BT, k, thr, s, e, 'DKL_qdown')
            if r: rows.append(r)
    res = pd.DataFrame(rows)
    res.to_csv('output/sweep_qdown_results.csv', index=False)
    print('\n=== rv_vs_iv_qdown: top 10 by final (0.80 fills, qty=1) ===')
    best = res.sort_values('final', ascending=False).head(10)
    for _, x in best.iterrows():
        print(f"  k={int(x.k):<3} thr={x.thr:<6} n={int(x.n):<5} final ${x.final:>8,.0f}  "
              f"Sh(wk) {x.wsh:+.2f}  DD {x.dd:.1f}%  win {x.win:.1f}%")
    print('\n  reference (symmetric, same cache): k=10 thr=0.07 -> n=1668 $50,003 Sh(wk) +2.53 DD -3.5%')
    print('  reference growth peak:              k=12 thr=0.05 -> n=2089 $51,308 Sh(wk) +2.49 DD -4.5%')

    print('\nAttaching probs to 2026 OOT cache...')
    OOT = attach_probs('output/sweep_midmkt_oot2026.parquet', [2026])
    OOT.to_parquet('output/sweep_midmkt_oot2026_probs.parquet')
    os_, oe = OOT['entry_date'].min(), pd.Timestamp('2026-12-31')
    print('\n=== 2026 OOT at qdown top-3 cells ===')
    for _, x in best.head(3).iterrows():
        r = evaluate(OOT, int(x.k), x.thr, os_, oe, 'DKL_qdown')
        if r:
            print(f"  k={int(x.k):<3} thr={x.thr:<6} n={r['n']:<4} final ${r['final']:>8,.0f}  "
                  f"Sh(wk) {r['wsh']:+.2f}  DD {r['dd']:.1f}%")
        else:
            print(f"  k={int(x.k):<3} thr={x.thr:<6} -- no trades")
