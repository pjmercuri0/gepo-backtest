"""Fair-value pricing stress test — remove dependence on contaminated quotes.

Motivation: 8.6% of delta-20 candidates carry a mid-implied credit above 2x
max_loss, which is impossible for a ~20-delta short (QCOM: $0.995 credit on a
$1.00 width, 6.4% OTM, short-leg IV 103%). Those rows win ~99% and supply 31%
of backtest P&L. The quotes cannot be trusted, so price the book from DELTA
instead of from bid/ask.

Fair odds are q:p on the leg that actually produces max loss — the LONG strike,
not the short. Max loss requires breaching the long strike; between the strikes
is the partial zone.

    ratio_fair = d_L / (1 - d_L)
    width      = credit + max_loss
    => credit_fair = width * d_L          (the algebra collapses)

Verified against market: observed median / fair(long delta) = 1.04x across
129,102 ungated candidates, i.e. the market prices these almost exactly at fair
odds on the correct leg. (Against fair(SHORT delta) it looks like 0.53x, which
is what made the quotes appear systematically cheap — wrong leg.)

Each multiplier m prices fills at m x fair. Selection is redone at each m,
because credit feeds b = credit/max_loss -> Kelly -> G -> GROUND.

MIN_CREDIT_RATIO is set to 0 here: fair ratios at this delta run ~0.15, so the
canonical 0.30 floor would reject essentially the whole book. That floor was
calibrated against inflated quotes and does not survive fair-value pricing.
"""
import sys, os, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import spread_triple as st

SPY_CSV = 'data/spy_us_d.csv'
START = 10_000.0
THR, K_VAL = 0.05, 10.0
MULTS = [0.6, 0.7, 0.8, 0.9, 1.0]
CAND = 'output/ungated_delta_band_candidates.parquet'
POPS = ['output/spread_outcomes.parquet', 'output/spread_outcomes_2026.parquet']

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.MIN_CREDIT_RATIO = 0.0


def load():
    R = pd.read_parquet(CAND)
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    R['expiry_date'] = pd.to_datetime(R['expiry_date'])
    R['ad'] = R.short_delta.abs(); R['ald'] = R.long_delta.abs()
    R['width'] = (R.short_strike - R.long_strike).abs()
    R = R[R.ad.between(0.10, 0.30) & (R.width > 0)].copy()
    # long delta must be strictly inside (0, short) or fair value is meaningless;
    # 7.8% of vendor rows report a BACKWARDS gap (long |delta| >= short).
    R = R[(R.ald > 0) & (R.ald < R.ad)].copy()

    # realized settlement price, from the spread-outcome population
    frames = [pd.read_parquet(p) for p in POPS if os.path.exists(p)]
    P = pd.concat(frames, ignore_index=True)
    P['entry_date'] = pd.to_datetime(P['entry_date']); P['expiry_date'] = pd.to_datetime(P['expiry_date'])
    P = P[['ticker', 'entry_date', 'expiry_date', 'short_strike', 'long_strike', 'expiry_close']]
    R = R.merge(P, on=['ticker', 'entry_date', 'expiry_date', 'short_strike', 'long_strike'], how='inner')

    rv = pd.read_parquet('output/rv_table.parquet'); rv['DataDate'] = pd.to_datetime(rv['DataDate'])
    R = R.merge(rv.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date'}),
                on=['ticker', 'entry_date'], how='left')
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    R = R[R.entry_date.dt.normalize().isin(set(spy['Date'].dt.normalize()))]
    return R.reset_index(drop=True), spy.sort_values('Date')


def price_at(R, m):
    d = R.copy()
    d['net_credit'] = (m * d['width'] * d['ald']).round(4)
    d['max_loss'] = (d['width'] - d['net_credit']).round(4)
    return d[(d.net_credit > 0) & (d.max_loss > 0)].copy()


def outcome(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
    return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')


def run(d, spy):
    parts = []
    for dt in sorted(d.entry_date.unique()):
        sub = d[d.entry_date == dt]
        if sub.empty: continue
        if not st.install_window(pd.Timestamp(dt)):
            continue
        parts.append(ground.score_candidates(sub))
    if not parts: return None
    sc = pd.concat(parts, ignore_index=True).dropna(subset=['G', 'DKL']).copy()
    sc['GROUND'] = (np.exp(sc.G) - 1.0) * np.exp(-K_VAL * sc.DKL)
    sel = (sc[sc.GROUND >= THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False])
           .groupby('entry_date').head(5)).copy()
    if sel.empty: return None
    sel['_outcome'] = sel.apply(outcome, axis=1)
    pnl = sel.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['net_credit'], r['max_loss'], r['spread_type']), axis=1) * 100
    msk = (sel['_outcome'] == 'PARTIAL') & (pnl > 0); pnl[msk] *= 0.5
    sel['pnl'] = pnl
    s = spy[(spy.Date >= sel.entry_date.min()) & (spy.Date <= pd.Timestamp('2025-12-31'))]
    tdi = pd.DatetimeIndex(s.Date)
    eq = START + sel.groupby(pd.to_datetime(sel.expiry_date))['pnl'].sum().reindex(tdi, fill_value=0).cumsum()
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = wk.std(ddof=0)
    sh = float(wk.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    return dict(n=len(sel), win=100 * (sel.pnl > 0).mean(), per=sel.pnl.mean(),
                final=float(eq.iloc[-1]), sh=sh, dd=dd,
                ratio=(sel.net_credit / sel.max_loss).median())


if __name__ == '__main__':
    st.load_population()
    R, spy = load()
    print(f'candidates priced from delta: {len(R):,}  '
          f'({R.entry_date.min().date()} -> {R.entry_date.max().date()})')
    print(f'  median short delta {R.ad.median():.3f}  median long delta {R.ald.median():.3f}')
    print(f'  implied fair ratio  {(R.ald/(1-R.ald)).median():.3f}')
    print(f'\n{"="*82}')
    print('FAIR-VALUE FILL STRESS — credit = m x width x delta_long (quotes ignored)')
    print(f'{"="*82}')
    print(f'  {"m":>5} {"BETS":>6} {"win%":>6} {"med ratio":>10} {"P&L/ctr":>9} '
          f'{"final(q1)":>11} {"Sh(wk)":>7} {"MaxDD":>7}')
    for m in MULTS:
        res = run(price_at(R, m), spy)
        if res is None:
            print(f'  {m:>5.1f}  no trades'); continue
        print(f'  {m:>5.1f} {res["n"]:>6,} {res["win"]:>5.1f}% {res["ratio"]:>10.3f} '
              f'{res["per"]:>9.2f} ${res["final"]:>10,.0f} {res["sh"]:>+7.2f} {res["dd"]:>6.1f}%')
    print('\n  reference — quoted-credit book at 0.80x mid: $77,417  Sh +3.69  DD -5.8%')
    print('  (that book includes the inflated-quote tail: 31% of its P&L came from ratio>1.0)')
