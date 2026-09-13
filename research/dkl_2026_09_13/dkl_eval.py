"""DKL candidates that measure EPISTEMIC risk (model error G cannot see), evaluated in
the real canon world: market mid credit, 0.80x fill, delta-20 band 0.10-0.30, caps
(OTM<=5%, width<=2.5, OI>=100, bid>0), G on the empirical triple (ticker,$width) with
pooled fallback, 52-expiry causal window, top-5/day, thr 0.05.

Every window quantity is computed from cumulative per-expiry count tensors, so all
dates vectorise. Strictly causal: only expiries < entry_date enter any window.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '/Users/mercurio/Downloads/gepo-backtest')
SCR = '/private/tmp/claude-501/-Users-mercurio-Downloads-gepo-backtest/74cb077b-eb69-4075-bfe1-766ea8fc2c15/scratchpad'
ROOT = '/Users/mercurio/Downloads/gepo-backtest'
T0 = time.time()
FILL, THR, TOP, NW = 0.80, 0.05, 5, 52
DOLLAR_BINS = [0, 0.75, 1.75, 3.75, 1e9]
OUTC = ['WIN', 'LOSS', 'PARTIAL']
EPS = 1e-12


def wb_of(width):
    w = np.digitize(np.abs(width), DOLLAR_BINS) - 1
    return np.clip(w, 0, len(DOLLAR_BINS) - 2)


# ── population + cumulative count tensors ───────────────────────────────────
def load_pop():
    P = pd.concat([pd.read_parquet(f'{ROOT}/output/spread_outcomes.parquet'),
                   pd.read_parquet(f'{ROOT}/output/spread_outcomes_2026.parquet')], ignore_index=True)
    P['entry_date'] = pd.to_datetime(P['entry_date']); P['expiry_date'] = pd.to_datetime(P['expiry_date'])
    P['width'] = (P.short_strike - P.long_strike).abs()
    P['wb'] = wb_of(P.width.values)
    P['sdb'] = (P.abs_short_delta.abs() * 10).astype(int).clip(0, 9)
    P['dte_i'] = P.DTE.clip(1, 4).astype(int)
    P['ret'] = P.expiry_close / P.entry_price - 1.0
    ex = np.sort(P.expiry_date.unique())
    P['eidx'] = np.searchsorted(ex, P.expiry_date.values)
    return P, ex


class Cum:
    """Cumulative counts C[key, e+1, outcome] = counts over expiries 0..e (C[:,0]=0)."""
    def __init__(self, P, keys, nE):
        if keys:
            kf = P[keys].drop_duplicates().reset_index(drop=True)
            kf['kid'] = np.arange(len(kf))
            Pk = P.merge(kf, on=keys, how='left')
        else:
            kf = None; Pk = P.copy(); Pk['kid'] = 0
        nk = len(kf) if kf is not None else 1
        C = np.zeros((nk, nE + 1, 3))
        for j, o in enumerate(OUTC):
            sub = Pk[Pk.outcome == o]
            np.add.at(C[:, :, j], (sub.kid.values, sub.eidx.values + 1), 1)
        self.C = np.cumsum(C, axis=1); self.kf = kf; self.keys = keys

    def kid_for(self, df):
        if self.kf is None:
            return np.zeros(len(df), dtype=int)
        m = df[self.keys].merge(self.kf, on=self.keys, how='left')
        return m.kid.fillna(-1).astype(int).values

    def counts(self, kid, hi, lo):
        """counts over expiries with index in [lo, hi) for each row; kid=-1 -> zeros"""
        ok = kid >= 0
        out = np.zeros((len(kid), 3))
        k = np.where(ok, kid, 0)
        out[ok] = (self.C[k, hi] - self.C[k, lo])[ok]
        return out


def triple(cnt):
    n = cnt.sum(1)
    with np.errstate(invalid='ignore', divide='ignore'):
        t = cnt / n[:, None]
    return t, n


def kl(P, Q):
    """row-wise D(P||Q) in nats; terms with P=0 contribute 0; Q floored."""
    Q = np.clip(Q, EPS, None)
    with np.errstate(invalid='ignore', divide='ignore'):
        t = np.where(P > 0, P * np.log(np.clip(P, EPS, None) / Q), 0.0)
    return np.clip(t.sum(1), 0, None)


# ── Kelly G exactly as ground.py (vectorised) ───────────────────────────────
def kelly_ell(p, q, ro, b):
    a = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * np.where(b == 0, 1, b)))
    A = -a * b * b
    B = a * b * b * (p + ro) - b * (p + ro * a + q * (1 + a))
    C = p * b + ro * a * b - q
    w = np.full(len(p), np.nan)
    lin = A == 0
    with np.errstate(invalid='ignore', divide='ignore'):
        w[lin] = ((p * b - q) / (b * (p + q)))[lin]
        disc = B * B - 4 * A * C
        s = np.sqrt(np.clip(disc, 0, None))
        r1 = (-B - s) / (2 * A); r2 = (-B + s) / (2 * A)
    ok1 = (r1 > 0) & (r1 < 1); ok2 = (r2 > 0) & (r2 < 1)
    quad = ~lin & (disc >= 0)
    w[quad & ok1] = r1[quad & ok1]
    w[quad & ~ok1 & ok2] = r2[quad & ~ok1 & ok2]
    valid = np.isfinite(w) & (p > 0) & (q > 0) & (p + q <= 1.0)
    w = np.clip(w, 0.01, 0.99)
    with np.errstate(invalid='ignore', divide='ignore'):
        ell = (p * np.log(np.clip(1 + w * b, 1e-10, None)) + ro * np.log(np.clip(1 + w * a * b, 1e-10, None))
               + q * np.log(np.clip(1 - w, 1e-10, None)))
    ell[~valid] = np.nan
    return ell


# ── P&L / book ──────────────────────────────────────────────────────────────
def outcome_of(df):
    sp, ss, ls = df.expiry_close.values, df.short_strike.values, df.long_strike.values
    bp = (df.spread_type == 'bull_put').values
    win = np.where(bp, sp > ss, sp < ss); loss = np.where(bp, sp <= ls, sp >= ls)
    return np.where(win, 'WIN', np.where(loss, 'LOSS', 'PARTIAL'))


def pnl_of(df, credit, ml):
    sp, ss, ls = df.expiry_close.values, df.short_strike.values, df.long_strike.values
    bp = (df.spread_type == 'bull_put').values
    intr = np.where(bp, np.clip(ss - sp, 0, None), np.clip(sp - ss, 0, None))
    pnl = np.clip(credit - intr, -ml, credit)
    pnl = np.where(bp, np.where(sp >= ss, credit, np.where(sp <= ls, -ml, pnl)),
                   np.where(sp <= ss, credit, np.where(sp >= ls, -ml, pnl)))
    pnl = pnl * 100
    partial = outcome_of(df) == 'PARTIAL'
    pnl = np.where(partial & (pnl > 0), pnl * 0.5, pnl)     # harness convention (partials pay half)
    return pnl


def book(sel, end):
    if len(sel) == 0:
        return dict(n=0, final=10000.0, sharpe=0.0, dd=0.0, loss=0.0, win=0.0)
    s = sel.copy()
    s['credit'] = (s.net_credit * FILL).round(4); s['ml'] = (s.width - s.credit).round(4)
    s = s[s.ml > 0].copy()
    s['pnl'] = pnl_of(s, s.credit.values, s.ml.values)
    cal = pd.bdate_range(s.entry_date.min(), end)
    eq = 10000.0 + s.groupby('expiry_date')['pnl'].sum().reindex(cal, fill_value=0).cumsum()
    w = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = w.std(ddof=0)
    sh = float(w.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    oc = outcome_of(s)
    return dict(n=len(s), final=float(eq.iloc[-1]), sharpe=sh, dd=dd,
                loss=100 * float((oc == 'LOSS').mean()), win=100 * float((oc == 'WIN').mean()))


def select(df, score_col, k, dcol):
    d = df.copy()
    d['GR'] = d[score_col] * np.exp(-k * d[dcol])
    d = d[d.GR >= THR]
    return d.sort_values(['entry_date', 'GR'], ascending=[True, False]).groupby('entry_date').head(TOP)


# ── feature construction ────────────────────────────────────────────────────
def build_features(C, P, ex, tag, R_SHORT=4, R_LONG=8, ALPHA=20.0, BETA=2.0):
    C = C.copy()
    C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
    C['wb'] = wb_of(C.width.values)
    C['sdb'] = (C.d_sh.abs() * 10).astype(int).clip(0, 9)
    C['dte_i'] = C.DTE.clip(1, 4).astype(int)
    nE_all = len(ex)
    nE = np.searchsorted(ex, C.entry_date.values, side='left')   # expiries strictly before entry
    hiW = nE; loW = np.clip(nE - NW, 0, None)
    P['tk'] = P['ticker']; C['tk'] = C['ticker']
    cums = {
        'tw':    Cum(P, ['tk', 'wb'], nE_all),
        'full':  Cum(P, ['dte_i', 'sdb', 'wb'], nE_all),
        'nodte': Cum(P, ['sdb', 'wb'], nE_all),
        'w':     Cum(P, ['wb'], nE_all),
        'g':     Cum(P, [], nE_all),
        't':     Cum(P, ['tk'], nE_all),
    }
    kids = {k: v.kid_for(C) for k, v in cums.items()}
    W = {k: cums[k].counts(kids[k], hiW, loW) for k in cums}
    # canon lookup chain (spread_triple.lookup)
    T_tw, n_tw = triple(W['tw'])
    T_full, n_full = triple(W['full']); T_nodte, n_nodte = triple(W['nodte'])
    T_w, n_w = triple(W['w']); T_g, n_g = triple(W['g'])
    use = np.where((n_tw > 0)[:, None], T_tw,
          np.where((n_full >= 30)[:, None], T_full,
          np.where((n_nodte >= 30)[:, None], T_nodte,
          np.where((n_w >= 30)[:, None], T_w, T_g))))
    src = np.where(n_tw > 0, 'ticker', np.where(n_full >= 30, 'full', np.where(n_nodte >= 30, 'nodte',
                   np.where(n_w >= 30, 'w', 'global'))))
    n_use = np.where(n_tw > 0, n_tw, np.where(n_full >= 30, n_full, np.where(n_nodte >= 30, n_nodte,
                     np.where(n_w >= 30, n_w, n_g))))
    C['p'], C['q'], C['ro'] = use[:, 0], use[:, 1], use[:, 2]
    C['src'] = src; C['n_use'] = n_use; C['n_tw'] = n_tw
    # base rate reference (pooled width cell, global if thin)
    ref = np.where((n_w >= 30)[:, None], T_w, T_g)
    C['p_ref'], C['q_ref'], C['ro_ref'] = ref[:, 0], ref[:, 1], ref[:, 2]

    b = C.net_credit.values / C.max_loss.values
    C['ell'] = kelly_ell(C.p.values, C.q.values, C.ro.values, b)
    C['EV'] = np.exp(C.ell) - 1.0

    # ── D1 shrink: D(P_use || P_post), P_post = (n P_use + a P_ref)/(n+a). Zero when not ticker-sourced.
    n_t = np.where(src == 'ticker', n_tw, 0.0)
    post = (n_t[:, None] * use + ALPHA * ref) / (n_t[:, None] + ALPHA)
    C['D_shrink'] = np.where(src == 'ticker', kl(use, post), 0.0)
    C['p_post'], C['q_post'], C['ro_post'] = post[:, 0], post[:, 1], post[:, 2]
    # control: G computed on the shrunk posterior instead
    C['ell_post'] = kelly_ell(post[:, 0], post[:, 1], post[:, 2], b); C['EV_post'] = np.exp(C.ell_post) - 1.0
    # ── D_est: expected KL of a 3-cell frequency estimate ~ (K-1)/(2n) = 1/n
    C['D_est'] = 1.0 / np.clip(n_use, 1, None)

    # ── D2 shift (ticker, all widths): recent R expiries vs 52 window, smoothed with BETA pseudo-counts of window
    def shift_block(cum, kid, R, name):
        cW = cum.counts(kid, hiW, loW); TW_, nW_ = triple(cW)
        cR = cum.counts(kid, hiW, np.clip(nE - R, 0, None)); nR = cR.sum(1)
        TWf = np.where(np.isfinite(TW_), TW_, 1 / 3)
        TR = (cR + BETA * TWf) / (nR[:, None] + BETA)
        d = np.where(nW_ > 0, kl(TR, TWf), 0.0)
        worse = (TR[:, 1] + 0.5 * TR[:, 2]) > (TWf[:, 1] + 0.5 * TWf[:, 2])
        C[f'D_{name}_u'] = d
        C[f'D_{name}'] = np.where(worse, d, 0.0)
        C[f'n_{name}'] = nR
    shift_block(cums['t'], kids['t'], R_SHORT, f'shiftT{R_SHORT}')
    shift_block(cums['t'], kids['t'], R_LONG, f'shiftT{R_LONG}')
    shift_block(cums['g'], kids['g'], R_SHORT, f'shiftG{R_SHORT}')
    shift_block(cums['g'], kids['g'], 2, 'shiftG2')

    # ── D3 breach-at-strike: last R matched-DTE realized moves of this name vs this spread's strikes
    ret = P.drop_duplicates(['tk', 'dte_i', 'eidx', 'entry_date'])[['tk', 'dte_i', 'eidx', 'ret']]
    tks = {t: i for i, t in enumerate(sorted(P.tk.unique()))}
    RET = np.full((len(tks), 4, nE_all), np.nan)
    RET[ret.tk.map(tks).values, ret.dte_i.values - 1, ret.eidx.values] = ret.ret.values
    tki = C.tk.map(tks).fillna(-1).astype(int).values
    for R in (13, 26):
        idx = nE[:, None] - np.arange(1, R + 1)[None, :]            # last R expiry indices before entry
        okc = (idx >= 0) & (tki[:, None] >= 0)
        r = RET[np.clip(tki, 0, None)[:, None], (C.dte_i.values - 1)[:, None], np.clip(idx, 0, None)]
        r = np.where(okc, r, np.nan)
        s_pct = C.short_strike.values / C.entry_price.values - 1.0
        l_pct = C.long_strike.values / C.entry_price.values - 1.0
        bp = (C.spread_type == 'bull_put').values
        breach_s = np.where(bp[:, None], r <= s_pct[:, None], r >= s_pct[:, None]) & np.isfinite(r)
        breach_l = np.where(bp[:, None], r <= l_pct[:, None], r >= l_pct[:, None]) & np.isfinite(r)
        n = np.isfinite(r).sum(1)
        cnt = np.stack([n - breach_s.sum(1), breach_l.sum(1), breach_s.sum(1) - breach_l.sum(1)], 1).astype(float)
        TR = (cnt + BETA * use) / (n[:, None] + BETA)
        d = np.where(n >= 5, kl(TR, use), 0.0)
        worse = (TR[:, 1] + 0.5 * TR[:, 2]) > (use[:, 1] + 0.5 * use[:, 2])
        C[f'D_breach{R}_u'] = d; C[f'D_breach{R}'] = np.where(worse, d, 0.0); C[f'n_breach{R}'] = n

    C['outcome'] = outcome_of(C)
    C['is_loss'] = (C.outcome == 'LOSS').astype(float)
    C['loss_share'] = np.where(C.outcome == 'LOSS', 1.0, np.where(C.outcome == 'PARTIAL', 0.5, 0.0))
    C['pred_loss_share'] = C.q + 0.5 * C.ro
    print(f'[{tag}] {len(C):,} candidates, valid G {C.EV.notna().mean():.1%}, src mix '
          f'{C.src.value_counts(normalize=True).round(3).to_dict()}  [{time.time()-T0:.0f}s]', flush=True)
    return C


def quintile_table(C, dcol):
    d = C.dropna(subset=['EV']).copy()
    nz = d[dcol] > 0
    out = {'zero_frac': float((~nz).mean()), 'corr_EV': float(np.corrcoef(d[dcol], d.EV)[0, 1]),
           'corr_q': float(np.corrcoef(d[dcol], d.q)[0, 1])}
    if nz.sum() > 500:
        dd = d[nz]
        try:
            qq = pd.qcut(dd[dcol].rank(method='first'), 5, labels=False)
            g = dd.groupby(qq)
            out['loss_q'] = g.is_loss.mean().round(4).tolist()
            out['excess_q'] = (g.loss_share.mean() - g.pred_loss_share.mean()).round(4).tolist()
            out['ev_q'] = g.EV.mean().round(4).tolist()
        except ValueError:
            pass
    return out


def sweep(IS, OO, dcol, score='EV', ks=(0, 1, 2, 4, 8, 12, 16, 24, 32)):
    rows = []
    for k in ks:
        a = book(select(IS.dropna(subset=[score]), score, k, dcol), pd.Timestamp('2025-12-31'))
        b = book(select(OO.dropna(subset=[score]), score, k, dcol), pd.Timestamp('2026-08-31'))
        rows.append(dict(k=k, is_n=a['n'], is_fin=a['final'], is_sh=a['sharpe'], is_dd=a['dd'], is_loss=a['loss'],
                         oot_n=b['n'], oot_fin=b['final'], oot_sh=b['sharpe'], oot_dd=b['dd'], oot_loss=b['loss']))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    P, ex = load_pop()
    print(f'population {len(P):,} spreads, {len(ex)} expiries {ex[0].astype("datetime64[D]")}..{ex[-1].astype("datetime64[D]")}')
    ISc = pd.read_parquet('/tmp/gepo_sel_10_30.parquet')
    E = pd.read_parquet('/tmp/gepo_expclose.parquet'); E['expiry_date'] = pd.to_datetime(E.expiry_date)
    ISc['expiry_date'] = pd.to_datetime(ISc.expiry_date)
    ISc = ISc.merge(E, on=['ticker', 'expiry_date'], how='inner')
    OOc = pd.read_parquet(f'{SCR}/oot_sel_10_30.parquet')
    IS = build_features(ISc, P, ex, 'IS'); OO = build_features(OOc, P, ex, 'OOT')
    IS.to_parquet(f'{SCR}/feat_IS.parquet'); OO.to_parquet(f'{SCR}/feat_OOT.parquet')
    pd.set_option('display.width', 220)
    fmt = lambda x: f'{x:.2f}'
    print('\n=== k=0 baseline and shrunk-G control (no DKL) ===')
    IS['Z'] = 0.0; OO['Z'] = 0.0
    print('G on ticker cell :', sweep(IS, OO, 'Z', 'EV', ks=(0,)).to_string(index=False, float_format=fmt))
    print('G on shrunk post :', sweep(IS, OO, 'Z', 'EV_post', ks=(0,)).to_string(index=False, float_format=fmt))
    dcols = [c for c in IS.columns if c.startswith('D_')]
    for dc in dcols:
        print(f'\n=== {dc} ===')
        print('IS  risk-monotonicity:', quintile_table(IS, dc))
        print('OOT risk-monotonicity:', quintile_table(OO, dc))
        print(sweep(IS, OO, dc).to_string(index=False, float_format=fmt))
    print(f'\nDONE {time.time()-T0:.0f}s')
