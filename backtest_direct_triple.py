"""Direct 3-state empirical triple, keyed on the SHORT leg + spread WIDTH.

Design (user's, 2026-09-12): the long leg is not a free choice — it is whatever
the strike ladder gives ($0.50 / $1 / $2.50 / $5 depending on the name), so
delta-matching it is meaningless. A spread is fully specified by
    (DTE, short_delta_bucket, width_bucket)
and WIN / PARTIAL / LOSS are then COUNTED directly from realized history rather
than derived as ro = P(short ITM) - P(long ITM).

Fixes two defects of the canonical single-leg table at delta-20:
  * ro collapsed to exactly 0 on 19.5% of candidates because both legs fell in
    the same 0.1-wide delta bucket and returned the same p_itm.
  * 7.8% of rows have a BACKWARDS delta gap (long leg reports higher |delta|
    than the short leg — vendor artifact); the old code clamped those to ro=0
    regardless of bucket width. Keying on the short leg only makes the long
    leg's bad delta irrelevant.

IV is deliberately NOT in the key (user direction): spread-level samples are
~40x scarcer than leg-level rows, and width already carries most of what IV
was proxying for the pin zone.

Window: trailing N weekly expiries, strictly causal. Fallback hierarchy when a
cell has n < MIN_N: (DTE,sdb,wb) -> (sdb,wb) -> (wb) -> global.
RESEARCH ONLY — does not modify canon.
"""
import sys, os, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config

SPY_CSV = 'data/spy_us_d.csv'
START = 10_000.0
THR = 0.05
NEXP = 4
MIN_N = 30
K_GRID = [6, 8, 10, 12, 16]
POP = 'output/spread_outcomes.parquet'
BASE = 'output/sweep_emp_dkl_base.parquet'

# width as % of spot — the ladder is discrete in dollars but $1 on a $50 name
# is not $1 on a $500 name, so bucket the RELATIVE width.
W_EDGES = [0, 0.5, 0.8, 1.2, 1e9]
DELTA_MULT = 10          # short-leg bucket width 1/DELTA_MULT


def lg(x):
    return math.log(max(x, 1e-12), bt_config.LOG_BASE)


def add_keys(df, spot_col, width_col, delta_col):
    d = df.copy()
    d['sdb'] = (d[delta_col].abs() * DELTA_MULT).astype(int).clip(0, DELTA_MULT - 1)
    wpct = 100.0 * d[width_col] / d[spot_col]
    d['wb'] = pd.cut(wpct, bins=W_EDGES, labels=False, include_lowest=True)
    return d


def build_tables(sub):
    """Counted WIN/PARTIAL/LOSS frequencies at three key granularities."""
    out = {}
    for name, keys in [('full', ['DTE', 'sdb', 'wb']), ('nodte', ['sdb', 'wb']), ('w', ['wb'])]:
        g = sub.groupby(keys)['outcome'].value_counts().unstack(fill_value=0)
        for c in ['WIN', 'PARTIAL', 'LOSS']:
            if c not in g.columns: g[c] = 0
        g['n'] = g[['WIN', 'PARTIAL', 'LOSS']].sum(axis=1)
        out[name] = g
    glob = sub['outcome'].value_counts()
    tot = glob.sum()
    out['global'] = (glob.get('WIN', 0) / tot, glob.get('LOSS', 0) / tot, glob.get('PARTIAL', 0) / tot)
    return out


def lookup(tab, dte, sdb, wb):
    """(p,q,ro) counted directly; walk the fallback ladder until n >= MIN_N."""
    for name, key in [('full', (dte, sdb, wb)), ('nodte', (sdb, wb)), ('w', (wb,))]:
        g = tab[name]
        try:
            row = g.loc[key]
        except (KeyError, TypeError):
            continue
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        n = float(row['n'])
        if n >= MIN_N:
            return (float(row['WIN']) / n, float(row['LOSS']) / n, float(row['PARTIAL']) / n, name, n)
    p, q, ro = tab['global']
    return (p, q, ro, 'global', np.nan)


def metrics(sel, spy):
    s = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= pd.Timestamp('2025-12-31'))]
    tdi = pd.DatetimeIndex(s['Date'])
    eq = START + sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum().reindex(tdi, fill_value=0).cumsum()
    wk = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = wk.std(ddof=0)
    sh = float(wk.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    return len(sel), 100 * (sel.pnl_80 > 0).mean(), float(eq.iloc[-1]), sh, dd


if __name__ == '__main__':
    if not os.path.exists(POP):
        sys.exit(f'missing {POP} — run build_spread_outcome_table.py first')
    P = pd.read_parquet(POP)
    P['entry_date'] = pd.to_datetime(P['entry_date']); P['expiry_date'] = pd.to_datetime(P['expiry_date'])
    P['width'] = (P['short_strike'] - P['long_strike']).abs()
    P = add_keys(P, 'entry_price', 'width', 'abs_short_delta')
    print(f'population: {len(P):,} realized spreads  {P.entry_date.min().date()} -> {P.entry_date.max().date()}')
    print('outcome mix:', (100 * P.outcome.value_counts(normalize=True)).round(2).to_dict())

    R = pd.read_parquet(BASE)
    R['entry_date'] = pd.to_datetime(R['entry_date']); R['expiry_date'] = pd.to_datetime(R['expiry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
    td = set(spy['Date'].dt.normalize()); R = R[R.entry_date.dt.normalize().isin(td)].copy()
    R['width'] = (R['short_strike'] - R['long_strike']).abs()
    R = add_keys(R, 'entry_price', 'width', 'short_delta')

    uniq = np.sort(P.expiry_date.unique())
    dkl = np.full(len(R), np.nan); tier = np.empty(len(R), dtype=object); ro_z = np.full(len(R), np.nan)
    ev = R.entry_date.values
    for d in sorted(R.entry_date.unique()):
        asof = pd.Timestamp(d)
        prior = uniq[uniq < asof.to_datetime64()]
        if len(prior) == 0: continue
        take = prior[-NEXP:]
        sub = P[P.expiry_date.isin(take)]
        if sub.empty: continue
        tab = build_tables(sub)
        for i in np.where(ev == d)[0]:
            r = R.iloc[i]
            if pd.isna(r['wb']): continue
            p_e, q_e, ro_e, tn, _ = lookup(tab, int(r['DTE']), int(r['sdb']), int(r['wb']))
            p_i, q_i, ro_i = r['p_iv'], r['q_iv'], r['ro_iv']
            v = 0.0
            if p_e > 0 and p_i > 0:   v += p_e * lg(p_e / p_i)
            if q_e > 0 and q_i > 0:   v += q_e * lg(q_e / q_i)
            if ro_e > 0 and ro_i > 0: v += ro_e * lg(ro_e / ro_i)
            dkl[i] = max(0.0, v); tier[i] = tn; ro_z[i] = 1.0 if ro_e <= 1e-9 else 0.0
    R['DKL_d'] = dkl
    ok = np.isfinite(dkl)
    print(f'\ncomputable on {100*ok.mean():.1f}%   ro==0 on {100*np.nanmean(ro_z):.1f}%  (single-leg table: 19.5%)')
    print('fallback tier used:', pd.Series(tier[ok]).value_counts(normalize=True).mul(100).round(1).to_dict())
    print(f'corr(DKL_direct, pnl) = {R.DKL_d.corr(R.pnl_80):+.4f}   corr(canon rv_vs_iv, pnl) = {R.DKL.corr(R.pnl_80):+.4f}')
    print(f'\n{"="*78}\nDIRECT 3-STATE triple — key (DTE, short_delta_bucket, width_bucket), no IV\n{"="*78}')
    print(f'  {"k":>4} {"BETS":>6} {"win%":>6} {"final(q1)":>11} {"Sh(wk)":>7} {"MaxDD":>7}')
    for k in K_GRID:
        d0 = R.dropna(subset=['DKL_d']).copy()
        d0['S'] = (np.exp(d0.G) - 1.0) * np.exp(-k * d0['DKL_d'])
        sel = (d0[d0.S >= THR].sort_values(['entry_date', 'S'], ascending=[True, False])
               .groupby('entry_date').head(5))
        if sel.empty: print(f'  {k:>4}  no trades'); continue
        n, w, f, sh, dd = metrics(sel, spy)
        print(f'  {k:>4} {n:>6,} {w:>5.1f}% ${f:>10,.0f} {sh:>+7.2f} {dd:>6.1f}%')
    print('\n  reference — single-leg empirical (4-exp, k=10): $72,762  Sh +3.92  DD -1.9%')
    print('  reference — canon rv_vs_iv       (210d,  k=10): $68,891  Sh +3.53  DD -2.6%')
