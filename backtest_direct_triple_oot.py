"""2026 OUT-OF-TIME test of the 2026-09-12 scoring canon:
D(P_emp || Q_iv), outcomes counted directly, P_emp keyed on
(ticker, dollar_width_bucket), N=52 trailing expiries, k=12, delta-20.

Everything is held at the in-sample config so the comparison is like-for-like:
mid-basis selection, 0.80x fill, partial-WIN 50% haircut, MIN_CREDIT_RATIO 0.30,
top-5/day, GROUND threshold 0.05.

Causality: for an entry on date D the outcome population is restricted to
expiries strictly BEFORE D. Early-2026 entries therefore draw on 2025 history
(spread_outcomes.parquet) and later entries roll onto 2026 expiries as they
realize. No 2026 outcome is ever used to score a trade entered before it.
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
NEXP, K = 52, 12
DOLLAR_BINS = [0, 0.75, 1.75, 3.75, 1e9]
OOT_PARQUET = 'output/2026_sp500_last_oot_combined.parquet'
POP_IS = 'output/spread_outcomes.parquet'
POP_OOT = 'output/spread_outcomes_2026.parquet'
CAND_OOT = 'output/sweep_direct_oot2026_cand.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = 'mid'; bt_config.CREDIT_SCALE = 1.0
bt_config.MIN_OPEN_INTEREST = 100


def lg(x):
    return math.log(max(x, 1e-12), bt_config.LOG_BASE)


def _prep(ivr, rvt, tdays):
    d = pd.read_parquet(OOT_PARQUET)
    d = d[d.Symbol.isin(SP100)]
    d['PutCall'] = d.PutCall.str.lower().str.strip()
    ec = (d[d.DataDate == d.ExpirationDate]
          .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    d['dow'] = d.DataDate.dt.dayofweek; d['exp_dow'] = d.ExpirationDate.dt.dayofweek
    d = d[d.dow.isin(ACTIVE) & (d.exp_dow == 4) & d.DTE.between(1, 4)]
    d = d[(d.LastPrice.astype(float) > 0) & d.DataDate.isin(tdays)].copy()
    d['AbsDelta'] = d.Delta.abs(); d['MidPrice'] = (d.BidPrice + d.AskPrice) / 2
    d = d.merge(ivr[['Symbol', 'DataDate', 'iv_rank_bucket']], on=['Symbol', 'DataDate'], how='left')
    d = d.merge(rvt[['Symbol', 'DataDate', 'rv_30d']], on=['Symbol', 'DataDate'], how='left')
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    return d, ec


def _outcome(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
    return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')


def build_pop_2026(ivr, rvt, tdays):
    """2026 realized spreads, gate OFF — extends the outcome population forward."""
    if os.path.exists(POP_OOT):
        print(f'Loading {POP_OOT}'); return pd.read_parquet(POP_OOT)
    bt_config.MIN_CREDIT_RATIO = 0.0
    d, ec = _prep(ivr, rvt, tdays)
    c = spreads.build_candidates(d)
    c['expiry_close'] = c.apply(lambda r: ec.get((r['ticker'], r['expiry_date'])), axis=1)
    c = c.dropna(subset=['expiry_close']).copy()
    c['outcome'] = c.apply(_outcome, axis=1)
    c['abs_short_delta'] = c['short_delta'].abs()
    keep = ['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type',
            'short_strike', 'long_strike', 'abs_short_delta', 'IV', 'entry_price',
            'expiry_close', 'outcome']
    c = c[[k for k in keep if k in c.columns]]
    c.to_parquet(POP_OOT)
    print(f'wrote {POP_OOT}: {len(c):,} realized 2026 spreads')
    return c


def build_cand_2026(ivr, rvt, tdays):
    """2026 GATED candidates with G and Q_iv — the things we actually score."""
    if os.path.exists(CAND_OOT):
        print(f'Loading {CAND_OOT}'); return pd.read_parquet(CAND_OOT)
    bt_config.MIN_CREDIT_RATIO = 0.30
    d, ec = _prep(ivr, rvt, tdays)
    c = spreads.build_candidates(d)
    pool = er.load_master_pool()
    parts = []
    for dt in sorted(c.entry_date.unique()):
        sub = c[c.entry_date == dt]
        if sub.empty: continue
        hp._EMPIRICAL_TABLE = None          # G under canon does not need it
        parts.append(ground.score_candidates(sub))
    sc = pd.concat(parts, ignore_index=True).dropna(subset=['G', 'DKL']).copy()

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
    sc['width'] = sc['net_credit'] + sc['max_loss']
    sc['_outcome'] = sc.apply(_outcome, axis=1)
    cr = sc['net_credit'] * FILL; ml = sc['width'] - cr
    pnl = sc.apply(lambda r, c_=cr, m_=ml: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        c_.loc[r.name], m_.loc[r.name], r['spread_type']), axis=1) * 100
    msk = (sc['_outcome'] == 'PARTIAL') & (pnl > 0); pnl[msk] *= 0.5
    sc['pnl_80'] = pnl
    sc.to_parquet(CAND_OOT)
    print(f'wrote {CAND_OOT}: {len(sc):,} gated 2026 candidates')
    return sc


def rates(sub, keys):
    g = sub.groupby(keys)['outcome'].value_counts().unstack(fill_value=0)
    for c in ['WIN', 'PARTIAL', 'LOSS']:
        if c not in g.columns: g[c] = 0
    g['n'] = g[['WIN', 'PARTIAL', 'LOSS']].sum(axis=1)
    return g


def pooled_tables(sub):
    out = {}
    for name, keys in [('full', ['DTE', 'sdb', 'wb']), ('nodte', ['sdb', 'wb']), ('w', ['wb'])]:
        out[name] = rates(sub, keys)
    gl = sub['outcome'].value_counts(); t = gl.sum()
    out['global'] = (gl.get('WIN', 0)/t, gl.get('LOSS', 0)/t, gl.get('PARTIAL', 0)/t)
    return out


def pooled_lookup(tab, dte, sdb, wb):
    for name, key in [('full', (dte, sdb, wb)), ('nodte', (sdb, wb)), ('w', (wb,))]:
        try: row = tab[name].loc[key]
        except (KeyError, TypeError): continue
        if isinstance(row, pd.DataFrame): row = row.iloc[0]
        n = float(row['n'])
        if n >= 30:
            return row['WIN']/n, row['LOSS']/n, row['PARTIAL']/n
    return tab['global']


def metrics(sel, spy, end):
    s = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= end)]
    tdi = pd.DatetimeIndex(s['Date'])
    eq = START + sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum().reindex(tdi, fill_value=0).cumsum()
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = wk.std(ddof=0)
    sh = float(wk.mean()*np.sqrt(52)/sd) if sd > 0 else 0.0
    dd = 100*float(((eq-eq.cummax())/eq.cummax()).min())
    fin = float(eq.iloc[-1])
    ny = max((tdi[-1]-tdi[0]).days/365.25, 1e-9)
    return len(sel), 100*(sel.pnl_80>0).mean(), fin, 100*(fin-START)/START, sh, dd


if __name__ == '__main__':
    ivr = pd.read_parquet('output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr['DataDate'])
    rvt = pd.read_parquet('output/rv_table.parquet'); rvt['DataDate'] = pd.to_datetime(rvt['DataDate'])
    tdays = set(pd.read_csv(SPY_CSV, parse_dates=['Date'])['Date'])

    P_is = pd.read_parquet(POP_IS)
    P_oot = build_pop_2026(ivr, rvt, tdays)
    R = build_cand_2026(ivr, rvt, tdays)

    for df in (P_is, P_oot, R):
        df['entry_date'] = pd.to_datetime(df['entry_date'])
        df['expiry_date'] = pd.to_datetime(df['expiry_date'])
    P = pd.concat([P_is, P_oot], ignore_index=True)
    P['width'] = (P['short_strike'] - P['long_strike']).abs()
    P['sdb'] = (P['abs_short_delta']*10).astype(int).clip(0, 9)
    P['wb'] = pd.cut(P['width'], bins=DOLLAR_BINS, labels=False, include_lowest=True)
    R['width'] = (R['short_strike'] - R['long_strike']).abs()
    R['sdb'] = (R['short_delta'].abs()*10).astype(int).clip(0, 9)
    R['wb'] = pd.cut(R['width'], bins=DOLLAR_BINS, labels=False, include_lowest=True)

    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
    td = set(spy['Date'].dt.normalize()); R = R[R.entry_date.dt.normalize().isin(td)].copy()
    end = min(pd.Timestamp('2026-12-31'), spy['Date'].max())
    print(f'\nOOT candidates: {len(R):,}   entries {R.entry_date.min().date()} -> {R.entry_date.max().date()}')

    uniq = np.sort(P.expiry_date.unique()); ev = R.entry_date.values; tk = R.ticker.values
    res = {}
    for mode in ['ticker_w', 'pooled']:
        dkl = np.full(len(R), np.nan)
        for d in sorted(R.entry_date.unique()):
            asof = pd.Timestamp(d)
            prior = uniq[uniq < asof.to_datetime64()]      # CAUSAL
            if len(prior) == 0: continue
            sub = P[P.expiry_date.isin(prior[-NEXP:])]
            if sub.empty: continue
            pt = pooled_tables(sub)
            tw = rates(sub, ['ticker', 'wb']) if mode == 'ticker_w' else None
            for i in np.where(ev == d)[0]:
                r = R.iloc[i]
                if pd.isna(r['wb']): continue
                pe = None
                if tw is not None:
                    key = (tk[i], int(r['wb']))
                    if key in tw.index:
                        row = tw.loc[key]
                        if isinstance(row, pd.DataFrame): row = row.iloc[0]
                        n_t = float(row['n'])
                        if n_t > 0: pe, qe, roe = row['WIN']/n_t, row['LOSS']/n_t, row['PARTIAL']/n_t
                if pe is None:
                    pe, qe, roe = pooled_lookup(pt, int(r['DTE']), int(r['sdb']), int(r['wb']))
                s = pe+qe+roe
                if s <= 0: continue
                pe, qe, roe = pe/s, qe/s, roe/s
                pi, qi, roi = r['p_iv'], r['q_iv'], r['ro_iv']
                v = 0.0
                if pe > 0 and pi > 0:   v += pe*lg(pe/pi)
                if qe > 0 and qi > 0:   v += qe*lg(qe/qi)
                if roe > 0 and roi > 0: v += roe*lg(roe/roi)
                dkl[i] = max(0.0, v)
        R[f'D_{mode}'] = dkl
        res[mode] = dkl

    print(f'\n{"="*80}\n2026 OOT — D(P_emp||Q_iv) direct triple, N=52, delta-20, mid basis 0.80x fill\n{"="*80}')
    print(f'  {"mode":>9} {"k":>3} {"BETS":>6} {"win%":>6} {"final":>11} {"return":>9} {"Sh(wk)":>7} {"MaxDD":>7}')
    for mode in ['ticker_w', 'pooled']:
        for k in [8, 10, 12]:
            d0 = R.dropna(subset=[f'D_{mode}']).copy()
            d0['S'] = (np.exp(d0.G)-1.0)*np.exp(-k*d0[f'D_{mode}'])
            sel = (d0[d0.S >= THR].sort_values(['entry_date', 'S'], ascending=[True, False])
                   .groupby('entry_date').head(5))
            if sel.empty: print(f'  {mode:>9} {k:>3}  no trades'); continue
            n, w, f, ret, sh, dd = metrics(sel, spy, end)
            star = '  <- CHOSEN' if (mode == 'ticker_w' and k == 12) else ''
            print(f'  {mode:>9} {k:>3} {n:>6,} {w:>5.1f}% ${f:>10,.0f} {ret:>+8.1f}% {sh:>+7.2f} {dd:>6.1f}%{star}')
    # no-DKL control: does the penalty earn its place out of sample?
    d0 = R.copy(); d0['S'] = np.exp(d0.G)-1.0
    sel = (d0[d0.S >= THR].sort_values(['entry_date', 'S'], ascending=[True, False])
           .groupby('entry_date').head(5))
    if not sel.empty:
        n, w, f, ret, sh, dd = metrics(sel, spy, end)
        print(f'  {"k=0 ctrl":>9} {0:>3} {n:>6,} {w:>5.1f}% ${f:>10,.0f} {ret:>+8.1f}% {sh:>+7.2f} {dd:>6.1f}%')
    print('\n  in-sample 2020-25 reference (ticker_w N=52 k=12): $74,201  Sh +3.80  DD -3.6%  win 89.7%')
