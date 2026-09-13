"""Delta-band sweep with the full method: fitted-delta strike selection, smile-fit credit, P_real belief
(name's realized DTE-matched moves vs the exact strikes, 252d), simple reward DKL D(Q_wing||Q_atm), thr 0.01."""
import sys, os, time, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, kl, kelly_ell, outcome_of
from diag import iv_triple
from synth_credit import bs, ncdf, Y_MAX, IV_LO, IV_HI
pd.set_option('display.width', 250); T0 = time.time()
BANDS = [(0.05, 0.15, 0.10), (0.10, 0.20, 0.15), (0.15, 0.25, 0.20), (0.20, 0.30, 0.25), (0.25, 0.35, 0.30), (0.30, 0.40, 0.35), (0.35, 0.45, 0.40), (0.40, 0.60, 0.50)]
def load_pairs(path, fits_path, expclose):
    A = pd.read_parquet(path)
    if 'Symbol' in A.columns:
        A = A.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date', 'StrikePrice': 'short_strike', 'L_StrikePrice': 'long_strike', 'ad': 'd_sh', 'L_ad': 'd_lg', 'UnderlyingPrice': 'entry_price', 'ImpliedVolatility': 'IV', 'L_ImpliedVolatility': 'long_IV'})
    A['entry_date'] = pd.to_datetime(A.entry_date); A['expiry_date'] = pd.to_datetime(A.expiry_date)
    F = pd.read_parquet(fits_path)[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0']].drop_duplicates(['ticker', 'entry_date', 'expiry_date'])
    F['entry_date'] = pd.to_datetime(F.entry_date); F['expiry_date'] = pd.to_datetime(F.expiry_date)
    A = A.merge(F, on=['ticker', 'entry_date', 'expiry_date'], how='inner')
    if expclose is not None:
        E = pd.read_parquet(expclose); E['expiry_date'] = pd.to_datetime(E.expiry_date); A = A.merge(E, on=['ticker', 'expiry_date'], how='inner')
    T = np.clip(A.DTE.values, 1, None) / 365.0; S = A.entry_price.values; is_put = (A.spread_type == 'bull_put').values
    for leg in ('short', 'long'):
        K = A[f'{leg}_strike'].values; y = np.clip(np.log(K / S) / np.sqrt(T) / A.sig0.values, -Y_MAX, Y_MAX)
        iv = np.clip(A.c0 + A.c1 * y + A.c2 * y * y, IV_LO, IV_HI).values; d1 = (np.log(S / K) + 0.5 * iv * iv * T) / (iv * np.sqrt(T))
        A[f'iv_fit_{leg}'] = iv; A[f'bs_{leg}'] = bs(S, K, iv, T, is_put); A[f'dfit_{leg}'] = np.where(is_put, ncdf(-d1), ncdf(d1))
    A['model_credit'] = (A.bs_short - A.bs_long).round(4); A['width'] = (A.short_strike - A.long_strike).abs()
    return A
IS_ALL = load_pairs('/tmp/gepo_pairs.parquet', f'{HERE}/is_synth.parquet', '/tmp/gepo_expclose.parquet')
OO_ALL = load_pairs(f'{HERE}/oot_pairs_q.parquet', f'{HERE}/oot_synth.parquet', None)
print(f'pairs IS {len(IS_ALL):,} OOT {len(OO_ALL):,}  [{time.time()-T0:.0f}s]', flush=True)
# daily closes for P_real
px = pd.concat([IS_ALL[['ticker', 'entry_date', 'entry_price']].rename(columns={'entry_date': 'date', 'entry_price': 'close'}),
                OO_ALL[['ticker', 'entry_date', 'entry_price']].rename(columns={'entry_date': 'date', 'entry_price': 'close'}),
                IS_ALL[['ticker', 'expiry_date', 'expiry_close']].rename(columns={'expiry_date': 'date', 'expiry_close': 'close'}),
                OO_ALL[['ticker', 'expiry_date', 'expiry_close']].rename(columns={'expiry_date': 'date', 'expiry_close': 'close'})]).dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
PX = {tk: g.set_index('date').close for tk, g in px.groupby('ticker')}
def p_real(C, W=252):
    out = np.full((len(C), 3), np.nan); C = C.reset_index(drop=True)
    for tk, g in C.groupby('ticker'):
        s = PX.get(tk)
        if s is None or len(s) < 60: continue
        dates = s.index.values; cl = s.values; pos = np.clip(np.searchsorted(dates, g.entry_date.values), 0, len(cl) - 1)
        for d in (1, 2, 3, 4):
            m = (g.DTE.clip(1, 4).astype(int) == d).values
            if not m.any(): continue
            R = cl[d:] / cl[:-d] - 1.0; p0 = pos[m]; sub = g[m]; bp = (sub.spread_type == 'bull_put').values
            ths = sub.short_strike.values / sub.entry_price.values - 1; thl = sub.long_strike.values / sub.entry_price.values - 1
            idx = (p0 - d - 1)[:, None] - np.arange(W)[None, :]; ok = idx >= 0
            r = np.where(ok, R[np.clip(idx, 0, len(R) - 1)], np.nan)
            bs_ = np.where(bp[:, None], r <= ths[:, None], r >= ths[:, None]) & ok; bl = np.where(bp[:, None], r <= thl[:, None], r >= thl[:, None]) & ok
            n = ok.sum(1); ns = bs_.sum(1); nl = bl.sum(1)
            t = np.column_stack([n - ns + 0.5, nl + 0.5, ns - nl + 0.5]); t = t / t.sum(1, keepdims=True); t[n < 120] = np.nan
            out[sub.index.values] = t
    return out
def select_reward(df, k):
    d = df.copy(); d['GR'] = d['EV'] * np.exp(+k * d['D']); d = d[d.GR >= de.THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(5)
rows = []
for lo, hi, tgt in BANDS:
    res = {}
    for lab, A, end in (('IS', IS_ALL, pd.Timestamp('2025-12-31')), ('OOT', OO_ALL, pd.Timestamp('2026-08-31'))):
        b = A[A.dfit_short.between(lo, hi) & (A.dfit_long < A.dfit_short) & (A.model_credit > 0.01)].copy(); b['dist'] = (b.dfit_short - tgt).abs()
        C = b.loc[b.groupby(['ticker', 'entry_date', 'expiry_date', 'spread_type'], sort=False)['dist'].idxmin()].copy()
        C['net_credit'] = C.model_credit; C['max_loss'] = (C.width - C.model_credit).round(4); C = C[C.max_loss > 0]
        P = p_real(C); C = C.reset_index(drop=True); C['p'], C['q'], C['ro'] = P[:, 0], P[:, 1], P[:, 2]
        bb = C.net_credit.values / C.max_loss.values
        ell = kelly_ell(C.p.fillna(0).values, C.q.fillna(0).values, C.ro.fillna(0).values, bb); C['EV'] = np.exp(ell) - 1; C.loc[C.p.isna(), 'EV'] = np.nan
        Qw = iv_triple(C, C.iv_fit_short.values, C.iv_fit_long.values); Qa = iv_triple(C, C.sig0.values, C.sig0.values)
        lsw = Qw[:, 1] + 0.5 * Qw[:, 2]; lsa = Qa[:, 1] + 0.5 * Qa[:, 2]; C['D'] = np.where(lsw > lsa, kl(Qw, Qa), 0.0)
        C['outcome'] = outcome_of(C)
        v = C.dropna(subset=['EV'])
        for fill in (1.1, 1.0):
            de.FILL, de.THR = fill, 0.01
            for k in (0, 8):
                r = book(select_reward(v, k), end); res[(lab, fill, k)] = r
        res[(lab, 'n_cand')] = len(C); res[(lab, 'valid')] = float(v.EV.notna().mean()) if len(C) else 0; res[(lab, 'cr')] = float(C.eval('net_credit/width').median()); res[(lab, 'win')] = float((C.outcome == 'WIN').mean())
    for fill in (1.1, 1.0):
        for k in (0, 8):
            a, o = res[('IS', fill, k)], res[('OOT', fill, k)]
            rows.append(dict(band=f'{lo:.2f}-{hi:.2f}', tgt=tgt, fill=fill, k=k, cands=res[('IS', 'n_cand')], cred_w=round(res[('IS', 'cr')], 3), raw_win=round(res[('IS', 'win')], 3),
                             is_n=a['n'], is_fin=round(a['final']), is_sh=round(a['sharpe'], 2), is_dd=round(a['dd'], 1), is_loss=round(a['loss'], 1),
                             oot_n=o['n'], oot_fin=round(o['final']), oot_sh=round(o['sharpe'], 2), oot_dd=round(o['dd'], 1)))
    print(f'band {lo:.2f}-{hi:.2f} done [{time.time()-T0:.0f}s]', flush=True)
R = pd.DataFrame(rows); R.to_csv(f'{HERE}/band_sweep.csv', index=False)
for fill in (1.1, 1.0):
    for k in (0, 8):
        print(f'\n===== fill {fill}x model, k={k} (thr 0.01, top-5/day) =====')
        print(R[(R.fill == fill) & (R.k == k)].drop(columns=['fill', 'k']).to_string(index=False))
