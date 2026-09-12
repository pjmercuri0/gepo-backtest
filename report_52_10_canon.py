"""Regenerate live/data/backtest_equity.json + oot_equity.json under 52:10 canon.

52:10 = D(P_emp || Q_iv) with P_emp counted directly from realized spreads,
keyed on the name's own (ticker, dollar-width) history with a pooled geometry
fallback, 52-expiry trailing window, k=10, delta-20 short leg, mid-basis
selection at 0.80x fill, top-5/day, GROUND threshold 0.05.

Reuses report_mid_canon.build_payload so the payload shape (summary / points /
weeks / trades, qty1 / strategy / sixteenk / spy) is byte-identical to what the
webapp already renders.
"""
import sys, os, math, json
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import historical_probs as hp
import spread_triple as st
import report_mid_canon as rmc

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE = [0, 1, 2, 3]
K_VAL, THR, FILL = 10.0, 0.05, 0.80

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_CREDIT_RATIO = 0.30; bt_config.MIN_OPEN_INTEREST = 100

IS_CACHE = 'output/canon5210_is.parquet'
OOT_CACHE = 'output/canon5210_oot.parquet'


def _outcome(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
    return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')


def score_frame(df, expiry_close):
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    cand = spreads.build_candidates(df)
    if cand.empty:
        return pd.DataFrame()
    parts = []
    for dt in sorted(cand['entry_date'].unique()):
        sub = cand[cand['entry_date'] == dt]
        if sub.empty: continue
        if not st.install_window(pd.Timestamp(dt)):   # causal 52-expiry window
            continue
        parts.append(ground.score_candidates(sub))
    if not parts:
        return pd.DataFrame()
    sc = pd.concat(parts, ignore_index=True).dropna(subset=['G', 'DKL']).copy()
    sc['expiry_close'] = sc.apply(lambda r: expiry_close.get((r['ticker'], r['expiry_date'])), axis=1)
    sc = sc.dropna(subset=['expiry_close']).copy()
    if sc.empty: return sc
    sc['width'] = sc['net_credit'] + sc['max_loss']
    sc['_outcome'] = sc.apply(_outcome, axis=1)
    return sc


def load_year(path, min_date):
    d = pd.read_parquet(path)
    d = d[d.Symbol.isin(SP100)]
    d['PutCall'] = d.PutCall.str.lower().str.strip()
    ec = (d[d.DataDate == d.ExpirationDate]
          .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
    d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
    d = d[d.LastPrice.astype(float) > 0]
    d = d[d.DataDate >= min_date]
    tdays = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])
    d = d[d.DataDate.isin(tdays)].copy()
    return d, ec


def build(cache, paths, min_date):
    if os.path.exists(cache):
        print(f'Loading {cache}')
        return pd.read_parquet(cache)
    ivr = pd.read_parquet('output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr['DataDate'])
    rvt = pd.read_parquet('output/rv_table.parquet'); rvt['DataDate'] = pd.to_datetime(rvt['DataDate'])
    out = []
    for p in paths:
        if not os.path.exists(p):
            print(f'  skip missing {p}'); continue
        print(f'-- {p} --', flush=True)
        d, ec = load_year(p, min_date)
        d = d.merge(ivr[['Symbol', 'DataDate', 'iv_rank_bucket']], on=['Symbol', 'DataDate'], how='left')
        d = d.merge(rvt[['Symbol', 'DataDate', 'rv_30d']], on=['Symbol', 'DataDate'], how='left')
        sc = score_frame(d, ec)
        print(f'   {len(sc):,} scored', flush=True)
        if not sc.empty: out.append(sc)
    R = pd.concat(out, ignore_index=True)
    R.to_parquet(cache)
    print(f'wrote {cache}: {len(R):,}')
    return R


def select(R):
    R = R.copy()
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    R['GROUND'] = (np.exp(R['G']) - 1.0) * np.exp(-K_VAL * R['DKL'])
    sel = (R[R['GROUND'] >= THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False])
           .groupby('entry_date').head(5)).copy()
    sel['credit'] = (sel['net_credit'] * FILL).round(4)
    sel['max_loss_adj'] = (sel['width'] - sel['credit']).round(4)
    pnl = sel.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    msk = (sel['_outcome'] == 'PARTIAL') & (pnl > 0); pnl[msk] *= 0.5
    sel['pnl_per_contract'] = pnl
    sel['max_loss_dollar'] = sel['max_loss_adj'] * 100
    sel['realize_date'] = pd.to_datetime(sel['expiry_date'])
    sel['entry_date_dt'] = sel['entry_date']
    return sel.sort_values('entry_date_dt').reset_index(drop=True)


DELTA_LABEL = (f'{bt_config.DELTA_TARGET:g}Δ short leg '
               f'(band {bt_config.DELTA_MIN:g}–{bt_config.DELTA_MAX:g})')
DKL_LABEL = (f'D(P_emp‖Q_iv) — outcomes counted directly; key (ticker, $width) '
             f'with pooled fallback; {st.N_EXPIRIES}-expiry window')


def patch_config(payload, oot=False):
    c = payload['config']
    c['delta'] = DELTA_LABEL
    c['dkl'] = DKL_LABEL
    c['window'] = f'{st.N_EXPIRIES} weekly expiries (~1yr), trailing & causal'
    c['selection'] = (f'top-5 per day, k={K_VAL:g}, GROUND threshold {THR:g}'
                      + (' — FROZEN canon, no 2026 tuning' if oot else ' (all days)'))
    c['scoring'] = ('G_rv: RV-implied N(d2) probs in G; DKL = D(P_empirical‖Q_iv) '
                    'from realized spread outcomes (52:10 canon 2026-09-12); raw combo MID credit')
    return payload


if __name__ == '__main__':
    st.load_population()
    IS = build(IS_CACHE, [f'output/{y}_sp500_last.parquet' for y in range(2020, 2026)],
               pd.Timestamp('2020-01-01'))
    bt = select(IS)
    payload = patch_config(rmc.build_payload(bt, 2025, 'backtest 2020-25'))
    with open('live/data/backtest_equity.json', 'w') as f:
        json.dump(payload, f, indent=2)
    print('Wrote live/data/backtest_equity.json')

    OOT = build(OOT_CACHE, ['output/2026_sp500_last_oot_combined.parquet'],
                pd.Timestamp('2026-01-01'))
    oot = select(OOT)
    payload = patch_config(rmc.build_payload(oot, 2026, 'OOT 2026'), oot=True)
    with open('live/data/oot_equity.json', 'w') as f:
        json.dump(payload, f, indent=2)
    print('Wrote live/data/oot_equity.json')
