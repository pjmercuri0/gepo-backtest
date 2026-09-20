"""Expected credit/max_loss ratio as a function of short-leg delta.

Purpose: the vendor quotes cannot be trusted as-is. 8.6% of delta-20 candidates
carry a mid-implied credit above 2x max_loss — economically impossible for a
~20-delta short (QCOM 2021-07-07: $0.995 credit on a $1.00 width, 6.4% OTM,
short-leg IV 103%). Those rows win ~99% of the time and contribute 31% of
backtest P&L. We need a defensible expected ratio per delta so a plausibility
band can be enforced at candidate construction.

Built on the UNGATED population: the canon pool is already truncated by
MIN_CREDIT_RATIO >= 0.30, which censors the low end and would bias any fit.

Robustness: the contaminated tail is one-sided (inflated credits), so all
central estimates are MEDIANS, and the regression is fitted on per-bucket
medians rather than raw rows. A theoretical reference curve is computed
alongside: for a vertical, fair credit/width ~ mean of the two legs' ITM
probabilities, so ratio_fair = m/(1-m) with m = (|d_short|+|d_long|)/2.
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE = [0, 1, 2, 3]
CACHE = 'output/ungated_delta_band_candidates.parquet'
YEARS = [2020, 2021, 2022, 2023, 2024, 2025]

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.0        # UNGATED — we need the true distribution
bt_config.MIN_OPEN_INTEREST = 100


def build():
    if os.path.exists(CACHE):
        print(f'Loading {CACHE}')
        return pd.read_parquet(CACHE)
    tdays = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0; spreads.REGIME_LOOKUP = None
    out = []
    for y in YEARS:
        print(f'-- {y} --', flush=True)
        d = pd.read_parquet(f'output/{y}_sp500_last.parquet')
        d = d[d.Symbol.isin(SP100)]
        d['PutCall'] = d.PutCall.str.lower().str.strip()
        d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
        d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
        d = d[(d.LastPrice.astype(float) > 0) & d.DataDate.isin(tdays)].copy()
        if d.empty: continue
        d['AbsDelta'] = d.Delta.abs(); d['MidPrice'] = (d.BidPrice + d.AskPrice) / 2
        c = spreads.build_candidates(d)
        if c.empty: continue
        keep = ['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price',
                'short_strike', 'long_strike', 'short_delta', 'long_delta', 'IV', 'long_IV',
                'net_credit', 'max_loss', 'short_bid', 'short_ask', 'long_bid', 'long_ask']
        out.append(c[[k for k in keep if k in c.columns]])
        print(f'   {len(out[-1]):,}', flush=True)
    R = pd.concat(out, ignore_index=True)
    R.to_parquet(CACHE)
    print(f'wrote {CACHE}: {len(R):,}')
    return R


if __name__ == '__main__':
    R = build()
    R['ad'] = R.short_delta.abs()
    R['ald'] = R.long_delta.abs()
    R['width'] = (R.short_strike - R.long_strike).abs()
    R['ratio'] = R.net_credit / R.max_loss
    R['cw'] = R.net_credit / R.width                 # credit as fraction of width
    R = R[(R.ad.between(0.10, 0.30)) & np.isfinite(R.ratio) & (R.ratio > 0)]
    print(f'\nUNGATED delta-band candidates: {len(R):,}  '
          f'({R.entry_date.min().date()} -> {R.entry_date.max().date()})')
    print(f'  ratio: median {R.ratio.median():.3f}  p25 {R.ratio.quantile(.25):.3f}  '
          f'p75 {R.ratio.quantile(.75):.3f}  p95 {R.ratio.quantile(.95):.3f}  max {R.ratio.max():.1f}')

    # ── per-delta-bucket robust stats ──────────────────────────────────────
    R['db'] = (R.ad * 100).round(0) / 100
    R['db'] = pd.cut(R.ad, bins=np.arange(0.10, 0.3251, 0.025), labels=False, include_lowest=True)
    edges = np.arange(0.10, 0.3251, 0.025)
    print(f'\n{"="*94}')
    print('EXPECTED CREDIT RATIO BY SHORT-LEG DELTA (medians — robust to the inflated tail)')
    print(f'{"="*94}')
    print(f'  {"delta band":>13} {"n":>7} {"p25":>7} {"MEDIAN":>8} {"p75":>7} {"p90":>7} '
          f'{"credit/width":>13} {"theory":>8} {"%>2x theory":>12}')
    rows = []
    for b, g in R.groupby('db', observed=True):
        b = int(b)
        if b + 1 >= len(edges): continue
        lo, hi = edges[b], edges[b + 1]
        mid = 0.5 * (lo + hi)
        m_fair = 0.5 * (g.ad.median() + g.ald.median())
        theory = m_fair / (1 - m_fair)
        med = g.ratio.median()
        bad = 100 * (g.ratio > 2 * theory).mean()
        rows.append((mid, len(g), med, theory, g.cw.median()))
        print(f'  {lo:.3f}-{hi:.3f} {len(g):>7,} {g.ratio.quantile(.25):>7.3f} {med:>8.3f} '
              f'{g.ratio.quantile(.75):>7.3f} {g.ratio.quantile(.90):>7.3f} '
              f'{g.cw.median():>13.3f} {theory:>8.3f} {bad:>11.1f}%')

    # ── regression on bucket medians: ratio ~ a + b*delta ──────────────────
    A = np.array([r[0] for r in rows]); Y = np.array([r[2] for r in rows])
    n = len(A)
    b1 = ((A - A.mean()) * (Y - Y.mean())).sum() / ((A - A.mean()) ** 2).sum()
    b0 = Y.mean() - b1 * A.mean()
    yhat = b0 + b1 * A
    ss_res = ((Y - yhat) ** 2).sum(); ss_tot = ((Y - Y.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float('nan')
    print(f'\n{"="*94}')
    print('REGRESSION (fitted on bucket medians, delta 0.10-0.30)')
    print(f'{"="*94}')
    print(f'  expected_ratio(delta) = {b0:+.4f} {b1:+.4f} * delta       R^2 = {r2:.3f}  (n={n} buckets)')
    print(f'\n  {"delta":>7} {"predicted":>10} {"observed":>9} {"theory":>8}')
    for d in [0.10, 0.125, 0.15, 0.175, 0.20, 0.225, 0.25, 0.275, 0.30]:
        pred = b0 + b1 * d
        obs = np.interp(d, A, Y)
        m = d * 0.85                                  # long leg ~0.85x short delta empirically
        th = m / (1 - m)
        print(f'  {d:>7.3f} {pred:>10.3f} {obs:>9.3f} {th:>8.3f}')
    print(f'\n  PLAUSIBILITY CAP suggestion: reject when ratio > C * expected_ratio(delta).')
    for C in [1.5, 2.0, 2.5, 3.0]:
        kept = (R.ratio <= C * (b0 + b1 * R.ad)).mean()
        print(f'    C={C:.1f}: keeps {100*kept:.1f}% of ungated candidates')
