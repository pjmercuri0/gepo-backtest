"""D_ent canon (2026-09-13) — single source of truth shared by the backtest report,
the live ranker and the webapp.

    strike selection   short leg = fitted delta nearest 0.55 within [0.50, 0.60]
    credit             smile-fit model credit (BS at the per-chain fitted IV of each leg),
                       fills booked at FILL_MULT x model, COMMISSION per spread
    belief (G)         P_real: the name's own realized DTE-matched moves over the trailing
                       WINDOW sessions, counted against the exact strikes -> (WIN, LOSS, PARTIAL)
    risk (DKL)         D_ent = D(Q_bs || U_3) = ln 3 - H(Q_bs)   (Mercurio, Wu, Xie 2020, eq. 19)
                       Q_bs = N(d2) triple at the fitted IVs
    score              GROUND = (exp(G) - 1) * exp(-K * D_ent),  qualified if GROUND >= THR, top-5/day
    execution          min credit/width = 1.04 x fair, target (1.06, 1.10) x fair; fair = model credit
                       when known, else fair(d, DTE) = 0.4022 + 2.3485(d-.5) - 12.464(d-.5)^2 + .0077(DTE-1)

Everything here is vectorised numpy/pandas; no vendor quote at a wing strike is ever used
directly (§0.24: those quotes are not fillable). Validation: model credit = IBKR mid within 2%
on unselected chains; 19 real fills averaged 1.08 x model (§0.30, §0.33).
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from math import erf

# ── canon parameters ────────────────────────────────────────────────────────
DELTA_TARGET, DELTA_MIN, DELTA_MAX = 0.55, 0.50, 0.60
K            = 1.0        # exp(-K * D_ent)
THR          = 0.01       # GROUND threshold (0.015 = conservative)
WINDOW       = 252        # sessions of realized moves behind P_real
MIN_OBS      = 1          # use whatever history the name has (user 2026-09-13); the 0.5 pseudo-count per
                          # state is the only regularisation, so a name with very few sessions scores near the prior
FILL_MULT    = 1.08       # measured on 19 real fills vs model credit
COMMISSION   = 1.30       # $ per spread per contract, opening only (IBKR ~$0.65/leg)
TOP_N        = 5
PRIOR        = 0.5        # pseudo-count per state in P_real

# execution targets, as multiples of fair (model) credit
MULT_BREAKEVEN, MULT_MIN, MULT_TARGET_LO, MULT_TARGET_HI = 0.965, 1.04, 1.06, 1.10
# cross-sectional fair credit/width by delta and DTE (fit on 43,479 candidates 2020-26, med |err| 0.025)
FAIR_COEF = (0.4022, 2.3485, -12.464, 0.0077)

# smile fit
MIN_Q, Y_MAX, IV_LO, IV_HI = 5, 2.5, 0.03, 3.0
LN3 = math.log(3.0)
_U3 = np.array([1 / 3, 1 / 3, 1 / 3])


def ncdf(x):
    x = np.asarray(x, dtype=float)
    return 0.5 * (1.0 + np.vectorize(erf)(x / math.sqrt(2.0)))


def bs_price(S, K_, iv, T, is_put):
    sT = iv * np.sqrt(T)
    d1 = (np.log(S / K_) + 0.5 * iv * iv * T) / sT
    d2 = d1 - sT
    call = S * ncdf(d1) - K_ * ncdf(d2)
    return np.where(is_put, call - S + K_, call)


# ── 1. per-chain smile fit ──────────────────────────────────────────────────
def _fit_one(y, iv, w):
    keep = np.ones(len(y), bool)
    coef = None
    for _ in range(3):
        if keep.sum() < MIN_Q:
            return None
        X = np.column_stack([np.ones(keep.sum()), y[keep], y[keep] ** 2]) * np.sqrt(w[keep])[:, None]
        try:
            coef = np.linalg.lstsq(X, iv[keep] * np.sqrt(w[keep]), rcond=None)[0]
        except np.linalg.LinAlgError:
            return None
        res = iv - (coef[0] + coef[1] * y + coef[2] * y * y)
        mad = np.median(np.abs(res[keep] - np.median(res[keep]))) * 1.4826 + 1e-6
        new = keep & (np.abs(res) <= 2.5 * mad)
        if new.sum() == keep.sum() or new.sum() < MIN_Q:
            break
        keep = new
    return coef, int(keep.sum()), float(np.sqrt(np.mean(res[keep] ** 2)))


def fit_smiles(chain: pd.DataFrame) -> pd.DataFrame:
    """Robust weighted quadratic IV smile per (Symbol, DataDate, ExpirationDate).

    chain columns: Symbol, DataDate, ExpirationDate, DTE, PutCall, StrikePrice, BidPrice,
    AskPrice, ImpliedVolatility, UnderlyingPrice. Puts and calls pooled (parity, r=q=0).
    Moneyness y = ln(K/S) / (sig0 * sqrt(T)) with sig0 = median IV of the 6 nearest strikes.
    Returns one row per chain: c0, c1, c2, sig0, fit_n, fit_rmse.
    """
    ch = chain.copy()
    ch['PutCall'] = ch['PutCall'].astype(str).str.lower().str.strip()
    ch = ch[(ch.BidPrice > 0) & (ch.AskPrice > ch.BidPrice) & (ch.ImpliedVolatility > 0) & (ch.UnderlyingPrice > 0)].copy()
    if ch.empty:
        return pd.DataFrame(columns=['Symbol', 'DataDate', 'ExpirationDate', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse'])
    T = np.clip(ch.DTE.values.astype(float), 1, None) / 365.0
    ch['lm'] = np.log(ch.StrikePrice / ch.UnderlyingPrice) / np.sqrt(T)
    g = ['Symbol', 'DataDate', 'ExpirationDate']
    ch['rank_atm'] = ch.groupby(g)['lm'].transform(lambda s: s.abs().rank(method='first'))
    atm = ch[ch.rank_atm <= 6].groupby(g)['ImpliedVolatility'].median().rename('sig0').reset_index()
    ch = ch.merge(atm, on=g, how='inner')
    ch['y'] = ch.lm / ch.sig0.clip(IV_LO, IV_HI)
    ch = ch[ch.y.abs() <= Y_MAX].copy()
    mid = (ch.BidPrice + ch.AskPrice) / 2
    ch['w'] = 1.0 / (1.0 + (ch.AskPrice - ch.BidPrice) / mid)
    rows = []
    for k, grp in ch.groupby(g, sort=False):
        r = _fit_one(grp.y.values, grp.ImpliedVolatility.values, grp.w.values)
        if r is not None:
            rows.append((*k, r[0][0], r[0][1], r[0][2], float(grp.sig0.iloc[0]), r[1], r[2]))
    return pd.DataFrame(rows, columns=['Symbol', 'DataDate', 'ExpirationDate', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse'])


# ── 2. price spreads off the fitted surface ─────────────────────────────────
def price_spreads(cands: pd.DataFrame, fits: pd.DataFrame) -> pd.DataFrame:
    """Adds iv_fit_short/long, dfit_short/long, model_credit, q_win/q_loss/q_part (Q_bs), D_ent.

    cands columns: ticker, entry_date, expiry_date, DTE, spread_type, entry_price, short_strike, long_strike.
    """
    C = cands.copy()
    F = fits.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date'})
    for c in ('entry_date', 'expiry_date'):
        C[c] = pd.to_datetime(C[c]).dt.normalize(); F[c] = pd.to_datetime(F[c]).dt.normalize()
    C = C.merge(F[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse']],
                on=['ticker', 'entry_date', 'expiry_date'], how='left')
    T = np.clip(C.DTE.values.astype(float), 1, None) / 365.0
    S = C.entry_price.values.astype(float)
    is_put = (C.spread_type == 'bull_put').values
    for leg in ('short', 'long'):
        Kx = C[f'{leg}_strike'].values.astype(float)
        y = np.clip(np.log(Kx / S) / np.sqrt(T) / C.sig0.values, -Y_MAX, Y_MAX)
        iv = np.clip(C.c0.values + C.c1.values * y + C.c2.values * y * y, IV_LO, IV_HI)
        d1 = (np.log(S / Kx) + 0.5 * iv * iv * T) / (iv * np.sqrt(T))
        C[f'iv_fit_{leg}'] = iv
        C[f'bs_{leg}'] = bs_price(S, Kx, iv, T, is_put)
        C[f'dfit_{leg}'] = np.where(is_put, ncdf(-d1), ncdf(d1))
    C['model_credit'] = (C.bs_short - C.bs_long).round(4)
    Q = market_triple(C, C.iv_fit_short.values, C.iv_fit_long.values)
    C['q_win'], C['q_loss'], C['q_part'] = Q[:, 0], Q[:, 1], Q[:, 2]
    C['D_ent'] = d_ent(Q)
    return C


def market_triple(C: pd.DataFrame, iv_short, iv_long) -> np.ndarray:
    """(WIN, LOSS, PARTIAL) under Black-Scholes N(d2) at the given leg IVs."""
    T = np.clip(C.DTE.values.astype(float), 1, None) / 365.0; sT = np.sqrt(T)
    S, Ks, Kl = C.entry_price.values.astype(float), C.short_strike.values.astype(float), C.long_strike.values.astype(float)
    iv_s = np.where(np.asarray(iv_short) > 0, iv_short, np.nan); iv_l = np.where(np.asarray(iv_long) > 0, iv_long, np.nan)
    d2s = (np.log(S / Ks) - 0.5 * iv_s ** 2 * T) / (iv_s * sT)
    d2l = (np.log(S / Kl) - 0.5 * iv_l ** 2 * T) / (iv_l * sT)
    bp = (C.spread_type == 'bull_put').values
    ps = np.where(bp, ncdf(-d2s), ncdf(d2s)); pl = np.minimum(np.where(bp, ncdf(-d2l), ncdf(d2l)), ps)
    t = np.column_stack([1 - ps, pl, ps - pl])
    with np.errstate(invalid='ignore'):
        t = t / t.sum(1, keepdims=True)
    return t


def d_ent(Q: np.ndarray) -> np.ndarray:
    """Paper eq. 19: D_KL(Q || U_3) = ln 3 - H(Q). NaN rows -> NaN."""
    Qc = np.clip(Q, 1e-9, None)
    H = -(Qc * np.log(Qc)).sum(1)
    out = LN3 - H
    out[~np.isfinite(Q).all(1)] = np.nan
    return out


# ── 3. realized belief from daily closes ────────────────────────────────────
def p_real(cands: pd.DataFrame, closes: pd.DataFrame, window: int = WINDOW) -> np.ndarray:
    """(p, q, ro) per candidate from the name's realized DTE-day moves vs the exact strikes.

    closes: columns ticker, date, close (daily). Strictly causal: only moves that COMPLETED
    before the entry date are used. Rows with < MIN_OBS valid moves get NaN.
    """
    out = np.full((len(cands), 3), np.nan)
    C = cands.reset_index(drop=True)
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
            r = np.where(ok, R[np.clip(idx, 0, len(R) - 1)], np.nan)
            bs_ = np.where(bp[:, None], r <= ths[:, None], r >= ths[:, None]) & ok
            bl = np.where(bp[:, None], r <= thl[:, None], r >= thl[:, None]) & ok
            n = ok.sum(1); ns = bs_.sum(1); nl = bl.sum(1)
            t = np.column_stack([n - ns + PRIOR, nl + PRIOR, ns - nl + PRIOR]).astype(float)
            t = t / t.sum(1, keepdims=True); t[n < MIN_OBS] = np.nan
            out[sub.index.values] = t
    return out


# ── 4. Kelly growth (identical FOC to ground._score_row) ────────────────────
def kelly(p, q, ro, b):
    """Returns (w_star, ell). ell = p ln(1+w b) + ro ln(1+w a b) + q ln(1-w), a=(b-1)/(2b) for b<1."""
    p, q, ro, b = (np.asarray(x, dtype=float) for x in (p, q, ro, b))
    a = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * np.where(b == 0, 1, b)))
    A = -a * b * b; B = a * b * b * (p + ro) - b * (p + ro * a + q * (1 + a)); Cc = p * b + ro * a * b - q
    w = np.full(len(p), np.nan); lin = A == 0
    with np.errstate(invalid='ignore', divide='ignore'):
        w[lin] = ((p * b - q) / (b * (p + q)))[lin]
        disc = B * B - 4 * A * Cc; s = np.sqrt(np.clip(disc, 0, None))
        r1 = (-B - s) / (2 * A); r2 = (-B + s) / (2 * A)
    ok1 = (r1 > 0) & (r1 < 1); ok2 = (r2 > 0) & (r2 < 1); quad = ~lin & (disc >= 0)
    w[quad & ok1] = r1[quad & ok1]; w[quad & ~ok1 & ok2] = r2[quad & ~ok1 & ok2]
    valid = np.isfinite(w) & (p > 0) & (q > 0) & (p + q <= 1.0)
    w = np.clip(w, 0.01, 0.99)
    with np.errstate(invalid='ignore', divide='ignore'):
        ell = (p * np.log(np.clip(1 + w * b, 1e-10, None)) + ro * np.log(np.clip(1 + w * a * b, 1e-10, None))
               + q * np.log(np.clip(1 - w, 1e-10, None)))
    ell[~valid] = np.nan; w[~valid] = np.nan
    return w, ell


# ── 5. score ────────────────────────────────────────────────────────────────
def score(C: pd.DataFrame, closes: pd.DataFrame, k: float = K, thr: float = THR, credit_col: str = 'model_credit') -> pd.DataFrame:
    """C must already carry model_credit + D_ent (price_spreads). Adds p,q,ro,w_star,G,EV,DKL,GROUND,qualified.

    credit_col: the credit the Kelly growth is computed on. 'model_credit' (backtest canon) or
    'net_credit' (live: IBKR quoted mid, user decision 2026-09-13 — uncapped)."""
    C = C.copy()
    P = p_real(C, closes)
    C['p'], C['q'], C['ro'] = P[:, 0], P[:, 1], P[:, 2]
    width = (C.short_strike - C.long_strike).abs().values.astype(float)
    cr = C[credit_col].values.astype(float)
    ml = width - cr
    with np.errstate(invalid='ignore', divide='ignore'):
        b = np.where(ml > 0, cr / ml, np.nan)
    w, ell = kelly(np.nan_to_num(C.p.values, nan=0), np.nan_to_num(C.q.values, nan=0), np.nan_to_num(C.ro.values, nan=0), np.nan_to_num(b, nan=0))
    bad = ~np.isfinite(b) | ~np.isfinite(C.p.values)
    ell[bad] = np.nan; w[bad] = np.nan
    C['w_star'] = w; C['G'] = ell; C['EV'] = np.exp(ell) - 1.0
    C['DKL'] = C['D_ent']
    C['GROUND'] = C.EV * np.exp(-k * C.D_ent)
    C['qualified'] = C.GROUND >= thr
    return C


# ── 6. execution targets ────────────────────────────────────────────────────
def fair_cw(delta, dte) -> float:
    d = min(max(float(delta), DELTA_MIN), DELTA_MAX) - 0.5
    a, b_, c, e = FAIR_COEF
    return a + b_ * d + c * d * d + e * (max(int(dte), 1) - 1)


def credit_targets(short_delta, dte, width=None, model_credit=None) -> dict:
    """Min / target credit for a spread. Uses the model credit when given (preferred),
    else the cross-sectional fair(d, DTE). Returns credit/width ratios and, when width is
    known, per-share credits, rounded to the cent."""
    d = abs(float(short_delta)) if short_delta is not None else DELTA_TARGET
    dte = int(dte) if dte is not None else 3
    if model_credit is not None and width:
        fair = float(model_credit) / float(width)
        basis = 'model'
    else:
        fair = fair_cw(d, dte)
        basis = 'formula'
    out = {'basis': basis, 'fair_cw': round(fair, 3),
           'breakeven_cw': round(fair * MULT_BREAKEVEN, 2), 'min_cw': round(fair * MULT_MIN, 2),
           'target_lo_cw': round(fair * MULT_TARGET_LO, 2), 'target_hi_cw': round(fair * MULT_TARGET_HI, 2)}
    if width:
        W = float(width)
        out.update({'width': round(W, 2), 'min_credit': round(out['min_cw'] * W, 2),
                    'target_lo_credit': round(out['target_lo_cw'] * W, 2), 'target_hi_credit': round(out['target_hi_cw'] * W, 2),
                    'breakeven_credit': round(out['breakeven_cw'] * W, 2)})
    return out


def grade_fill(actual_credit, width, targets: dict) -> str | None:
    """'below-min' | 'ok' | 'target' | 'great' for a recorded fill."""
    if actual_credit is None or not width:
        return None
    cw = float(actual_credit) / float(width)
    if cw < targets['min_cw']: return 'below-min'
    if cw < targets['target_lo_cw']: return 'ok'
    if cw <= targets['target_hi_cw']: return 'target'
    return 'great'


CANON_LABELS = {
    'delta':     f'{DELTA_TARGET:g}Δ short leg (fitted delta, band {DELTA_MIN:g}–{DELTA_MAX:g})',
    'dkl':       'D_ent = D(Q_bs‖U₃) = ln3 − H(Q_bs), Q_bs = N(d2) at the smile-fit IVs (Mercurio–Wu–Xie 2020 eq. 19)',
    'window':    f'{WINDOW} sessions of realized DTE-matched moves vs the exact strikes (P_real)',
    'selection': f'top-{TOP_N} per day, k={K:g}, GROUND ≥ {THR:g}',
    'scoring':   'G = Kelly log-growth on P_real at the smile-fit model credit; GROUND = (e^G−1)·e^(−k·D_ent)',
    'fill':      f'{FILL_MULT:.2f}× smile-fit model credit (19 real fills), ${COMMISSION:.2f} commission/spread',
    'targets':   f'min credit/width {MULT_MIN:.2f}×fair, target {MULT_TARGET_LO:.2f}–{MULT_TARGET_HI:.2f}×fair',
}
