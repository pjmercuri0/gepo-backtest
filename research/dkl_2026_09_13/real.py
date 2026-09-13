"""REAL belief + REAL divergence.  No IV, no delta, no smile in G or DKL; smile only sets the fill credit.
Belief P_real(candidate): fraction of the name's realized DTE-matched moves in the trailing W trading days
that would have (a) breached the short strike, (b) breached the long strike -> (WIN, LOSS, PARTIAL) counts.
DKL: D(P_real over recent window || P_real over the year), signed: fires when recent behaviour is worse.
Also: D(P_year || P_3yr) and market-wide median of the recent divergence."""
import sys, os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, select, kl, kelly_ell, quintile_table
pd.set_option('display.width', 230); fmt = lambda x: f'{x:.2f}'
W_REC, W_YR, W_LONG = 20, 252, 756
C = pd.read_parquet(f'{HERE}/featC.parquet'); C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
# ---- daily closes from REAL underlying prices (entry days from all pairs, Fridays from expiry closes)
px = [pd.read_parquet('/tmp/gepo_pairs.parquet', columns=['Symbol', 'DataDate', 'UnderlyingPrice']).rename(columns={'Symbol': 'ticker', 'DataDate': 'date', 'UnderlyingPrice': 'close'}),
      pd.read_parquet(f'{HERE}/oot_pairs_q.parquet', columns=['ticker', 'entry_date', 'entry_price']).rename(columns={'entry_date': 'date', 'entry_price': 'close'}),
      pd.read_parquet('/tmp/gepo_expclose.parquet').rename(columns={'expiry_date': 'date', 'expiry_close': 'close'}),
      C[['ticker', 'expiry_date', 'expiry_close']].rename(columns={'expiry_date': 'date', 'expiry_close': 'close'})]
px = pd.concat(px, ignore_index=True); px['date'] = pd.to_datetime(px.date)
px = px.dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
print(f'daily closes: {len(px):,} rows, {px.ticker.nunique()} names, {px.date.min().date()}..{px.date.max().date()}')
out = []
for tk, g in C.groupby('ticker'):
    s = px[px.ticker == tk].set_index('date').close
    if len(s) < 60: continue
    dates = s.index.values; cl = s.values
    pos = np.searchsorted(dates, g.entry_date.values)               # position of entry date in the close series
    pos = np.clip(pos, 0, len(cl) - 1)
    res = {}
    for d in (1, 2, 3, 4):
        m = (g.DTE.clip(1, 4).astype(int) == d).values
        if not m.any(): continue
        R = cl[d:] / cl[:-d] - 1.0                                   # R[i] = move from close i to close i+d (completes at i+d)
        p0 = pos[m]; sub = g[m]
        bp = (sub.spread_type == 'bull_put').values
        ths = sub.short_strike.values / sub.entry_price.values - 1; thl = sub.long_strike.values / sub.entry_price.values - 1
        for name, W in (('rec', W_REC), ('yr', W_YR), ('lg', W_LONG)):
            last = p0 - d - 1                                         # last start index whose move completed before entry
            idx = last[:, None] - np.arange(W)[None, :]
            ok = idx >= 0
            r = np.where(ok, R[np.clip(idx, 0, len(R) - 1)], np.nan)
            bs = np.where(bp[:, None], r <= ths[:, None], r >= ths[:, None]) & ok
            bl = np.where(bp[:, None], r <= thl[:, None], r >= thl[:, None]) & ok
            n = ok.sum(1); ns = bs.sum(1); nl = bl.sum(1)
            res[f'n_{name}'] = res.get(f'n_{name}', np.zeros(len(g))); res[f'n_{name}'][m] = n
            res[f's_{name}'] = res.get(f's_{name}', np.zeros(len(g))); res[f's_{name}'][m] = ns
            res[f'l_{name}'] = res.get(f'l_{name}', np.zeros(len(g))); res[f'l_{name}'][m] = nl
    gg = g[['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike', 'long_strike']].copy()
    for k, v in res.items(): gg[k] = v
    out.append(gg)
F = pd.concat(out, ignore_index=True)
C = C.merge(F, on=['ticker', 'entry_date', 'expiry_date', 'spread_type', 'short_strike', 'long_strike'], how='inner')
def triple(pref, prior=0.5):
    n, s, l = C[f'n_{pref}'].values, C[f's_{pref}'].values, C[f'l_{pref}'].values
    win, loss, par = n - s + prior, l + prior, s - l + prior
    t = np.column_stack([win, loss, par]); return t / t.sum(1, keepdims=True)
P_rec, P_yr, P_lg = triple('rec'), triple('yr'), triple('lg')
C['p_r'], C['q_r'], C['ro_r'] = P_yr[:, 0], P_yr[:, 1], P_yr[:, 2]
C['ls_r'] = C.q_r + 0.5 * C.ro_r
ok = C.n_yr >= 120
b = C.net_credit.values / C.max_loss.values
C['ell_r'] = np.where(ok, kelly_ell(C.p_r.values, C.q_r.values, C.ro_r.values, b), np.nan); C['EV_r'] = np.exp(C.ell_r) - 1
ls_rec = P_rec[:, 1] + 0.5 * P_rec[:, 2]; ls_lg = P_lg[:, 1] + 0.5 * P_lg[:, 2]
C['D_rec_u'] = np.where(C.n_rec >= 10, kl(P_rec, P_yr), 0.0); C['D_rec'] = np.where(ls_rec > C.ls_r, C.D_rec_u, 0.0)
C['D_lg_u'] = np.where(C.n_lg >= 400, kl(P_yr, P_lg), 0.0); C['D_lg'] = np.where(C.ls_r > ls_lg, C.D_lg_u, 0.0)
C['D_mkt'] = C.groupby('entry_date').D_rec.transform('median')          # market-wide: median recent divergence across names
C['Z'] = 0.0
print(f'candidates {len(C):,}; belief coverage {ok.mean():.1%}; realized loss share {C.loss_share.mean():.3f} vs P_real {C.ls_r.mean():.3f} (market {C.ls_iv.mean():.3f}, 52:10 P_emp {C.pred_loss_share.mean():.3f})')
v = C[ok]; print(f'corr(EV_real, EV_52:10) {np.corrcoef(v.EV_r.fillna(0), v.EV.fillna(0))[0,1]:.3f}; corr(EV_real, EV_cal) {np.corrcoef(v.EV_r.fillna(0), v.EV_cal.fillna(0))[0,1]:.3f}')
ISc, OOc = C[C.win == 'IS'], C[C.win == 'OOT']
def qt(df, col):
    d = df.dropna(subset=[col, 'EV_r']); d = d[d[col] > 0] if (d[col] > 0).mean() < 0.9 else d
    q = pd.qcut(d[col].rank(method='first'), 5, labels=False)
    g = d.groupby(q).agg(n=('is_loss', 'size'), loss=('is_loss', 'mean'), excess=('loss_share', 'mean'), EV=('EV_r', 'mean')); g['excess'] -= d.groupby(q).ls_r.mean()
    return f"n0={int((df[col]<=0).sum())} loss {g.loss.round(3).tolist()} excess {g.excess.round(3).tolist()} EV {g.EV.round(4).tolist()}"
def sw(dc, score, ks, fill, thr):
    de.FILL, de.THR = fill, thr; rows = []
    for k in ks:
        a = book(select(ISc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2025-12-31')); o = book(select(OOc.dropna(subset=[score]), score, k, dc), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'], oot_n=o['n'], oot_fin=o['final'], oot_sh=o['sharpe'], oot_dd=o['dd'], oot_loss=o['loss']))
    return pd.DataFrame(rows)
for fill in (1.1, 1.0):
    print(f'\n########## fill {fill}x model credit — G on P_real (name\'s realized moves vs these strikes, 252d) ##########')
    for thr in (0.01, 0.02, 0.05):
        print(f'k=0 thr {thr}:'); print(sw('Z', 'EV_r', (0,), fill, thr).to_string(index=False, float_format=fmt))
    for dc, ks in (('D_rec', (0, 2, 4, 8, 16, 32)), ('D_rec_u', (0, 2, 4, 8, 16, 32)), ('D_lg', (0, 2, 4, 8, 16, 32)), ('D_mkt', (0, 4, 8, 16, 32, 64))):
        print(f'\n=== DKL = {dc}, thr 0.02 ===')
        if fill == 1.1: print(' IS :', qt(ISc, dc)); print(' OOT:', qt(OOc, dc))
        print(sw(dc, 'EV_r', ks, fill, 0.02).to_string(index=False, float_format=fmt))
C.to_parquet(f'{HERE}/featR.parquet')
