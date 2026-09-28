"""Drift-free P_real vs canon (2026-09-28).

Canon P_real counts raw d-day returns over the trailing 252 sessions vs the exact strikes, so a
name's past-year drift is baked into its win odds (picks tilt to last year's leaders). Here each
candidate's window returns are DEMEANED (r - mean_window(r)) before counting. Everything else in
report_ent_canon.select() is unchanged: same frame, fill, D_ent, k, thr, parity, regime, top-N.

Also reported: pick overlap with canon, and the momentum tilt of the picks (median trailing
252-session return of the underlying at entry) under both variants.

    python3 research/p_real_demean_2026_09_28/run.py
"""
import sys, io, contextlib, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec
import report_ent_canon as rec
import report_mid_canon as rmc

_canon_p_real = ec.p_real


def p_real_demeaned(cands, closes, window=ec.WINDOW, mu=None):
    out = np.full((len(cands), 3), np.nan)
    C = cands.reset_index(drop=True)
    MU = np.zeros(len(C)) if mu is None else np.nan_to_num(np.asarray(mu, dtype=float))
    C['entry_date'] = pd.to_datetime(C.entry_date).dt.normalize()
    px = closes.dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
    by = {tk: g for tk, g in px.groupby('ticker')}
    for tk, g in C.groupby('ticker'):
        s = by.get(tk)
        if s is None or len(s) < 60:
            continue
        dates = pd.to_datetime(s.date).values.astype('datetime64[ns]'); cl = s.close.values.astype(float)
        pos = np.clip(np.searchsorted(dates, g.entry_date.values.astype('datetime64[ns]')), 0, len(cl) - 1)
        for d in (1, 2, 3, 4):
            m = (g.DTE.clip(1, 4).astype(int) == d).values
            if not m.any():
                continue
            R = cl[d:] / cl[:-d] - 1.0
            p0 = pos[m]; sub = g[m]; bp = (sub.spread_type == 'bull_put').values
            ths = sub.short_strike.values / sub.entry_price.values - 1; thl = sub.long_strike.values / sub.entry_price.values - 1
            idx = (p0 - d - 1)[:, None] - np.arange(window)[None, :]; ok = idx >= 0
            raw = np.where(ok, R[np.clip(idx, 0, len(R) - 1)], np.nan)
            raw = raw - np.nanmean(raw, axis=1, keepdims=True)          # <-- the only change: remove the window drift
            r = raw + MU[sub.index.values][:, None]
            bs_ = np.where(bp[:, None], r <= ths[:, None], r >= ths[:, None]) & ok
            bl = np.where(bp[:, None], r <= thl[:, None], r >= thl[:, None]) & ok
            n = ok.sum(1); ns = bs_.sum(1); nl = bl.sum(1)
            t = np.column_stack([n - ns + ec.PRIOR, nl + ec.PRIOR, ns - nl + ec.PRIOR]).astype(float)
            t = t / t.sum(1, keepdims=True); t[n < ec.MIN_OBS] = np.nan
            out[sub.index.values] = t
    return out


def trailing_ret(picks, closes, n=252):
    px = closes.dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
    by = {tk: g for tk, g in px.groupby('ticker')}
    out = np.full(len(picks), np.nan)
    for i, r in enumerate(picks.itertuples()):
        s = by.get(r.ticker)
        if s is None: continue
        dates = pd.to_datetime(s.date).values.astype('datetime64[ns]'); cl = s.close.values
        p = np.searchsorted(dates, np.datetime64(pd.Timestamp(r.entry_date_dt).normalize(), 'ns')) - 1
        if p - n >= 0: out[i] = cl[p] / cl[p - n] - 1
    return out


def key(df):
    return set(zip(df.ticker, pd.to_datetime(df.entry_date_dt).dt.strftime('%Y-%m-%d'), df.short_strike.round(2), df.long_strike.round(2)))


CLS = ec.backtest_closes()
rows = []
for win, end_year in (('IS', 2025), ('OOT', 2026)):
    res = {}
    for name, fn in (('canon', _canon_p_real), ('demeaned', p_real_demeaned)):
        ec.p_real = fn
        picks = rec.select(win)
        with contextlib.redirect_stdout(io.StringIO()):
            s = rmc.build_payload(picks, end_year, f'{win} {name}')['summary']
        tr = trailing_ret(picks, CLS)
        res[name] = picks
        rows.append(dict(win=win, variant=name, n=s['n_trades'], win_rate=round((picks._outcome == 'WIN').mean() * 100, 1),
                         qty1_final=s['qty1_final'], qty1_ret=s['qty1_total_return'], sh_wk=s['qty1_sharpe_weekly'],
                         sh_dollar=s['qty1_sharpe_dollar'], max_dd=s['qty1_max_dd'],
                         pnl_per_trade=round(picks.pnl_per_contract.mean(), 1),
                         med_1y_ret=round(np.nanmedian(tr) * 100, 1), n_tickers=picks.ticker.nunique(),
                         top5_share=round(picks.ticker.value_counts(normalize=True).head(5).sum() * 100, 1)))
    a, b = key(res['canon']), key(res['demeaned'])
    rows[-1]['overlap_pct'] = round(len(a & b) / max(len(a), 1) * 100, 1)
    rows[-2]['overlap_pct'] = 100.0
    # per-year P&L
    for name in ('canon', 'demeaned'):
        p = res[name]; yr = p.groupby(pd.to_datetime(p.entry_date_dt).dt.year).pnl_per_contract.agg(['sum', 'count'])
        print(f'{win} {name} per-year $/contract:', {int(y): (round(v['sum']), int(v['count'])) for y, v in yr.iterrows()}, flush=True)
    ec.p_real = _canon_p_real

T = pd.DataFrame(rows)
pd.set_option('display.width', 250)
print(T.to_string(index=False))
T.to_csv('research/p_real_demean_2026_09_28/results.csv', index=False)
