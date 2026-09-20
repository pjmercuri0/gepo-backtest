"""D_ent canon (2026-09-13) — single source of truth shared by the backtest report,
the live ranker and the webapp.

    strike selection   short leg = fitted delta nearest 0.55 within [0.50, 0.60]
    credit             smile-fit model credit (BS at the per-chain fitted IV of each leg),
                       fills booked at FILL_MULT x model, COMMISSION per spread
    belief (G)         P_real: the name's own realized DTE-matched moves over the trailing
                       WINDOW sessions, counted against the exact strikes -> (WIN, LOSS, PARTIAL)
    risk (DKL)         D_ent = D(Q_bs || U_3) = ln 3 - H(Q_bs)   (Mercurio, Wu, Xie 2020, eq. 19)
                       Q_bs = N(d2) triple at the fitted IVs
    score              GROUND = (exp(G) - 1) * exp(-K * D_ent), qualified if GROUND >= THR
    direction          bull puts only when prior-session SPY close > its 100-session SMA; cash otherwise
    option veto        daily same-strike call-IV minus put-IV percentile > 12%, then top-10/day
    execution          minimum = 1.00 x fair, target = (1.04, 1.10) x fair; fair = model credit
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
K            = 4.0        # exp(-K * D_ent). 2026-09-15: k=4 / thr=0.005, middle of the k=3-6 plateau on full-session P_real (handoff §0.43). Was 1.0 / 0.01.
THR          = 0.005      # GROUND threshold
WINDOW       = 252        # sessions of realized moves behind P_real
MIN_OBS      = 1          # use whatever history the name has (user 2026-09-13); the 0.5 pseudo-count per
                          # state is the only regularisation, so a name with very few sessions scores near the prior
FILL_MULT    = 1.08       # measured on 19 real fills vs model credit
COMMISSION   = 0.0        # $ per spread per contract. 2026-09-17 (user: "ignore all
                          # commission everywhere"). Was 1.30. Every book and every live
                          # P&L reads this constant, so setting it here zeroes it
                          # throughout; the published OOT payload already omitted it, and
                          # the in-sample payload still has 1.30 baked in until
                          # report_ent_canon.py is re-run on the MacBook.
TOP_N        = 10
PRIOR        = 0.5        # pseudo-count per state in P_real

# Direction overlay (2026-09-16).  For each chain, pair calls and puts at the
# same strike in the 35-65 absolute-delta band.  The raw bullish signal is the
# median(call IV - put IV).  Its sign is fit using years strictly before the
# entry year; the fitted sign has been +1 from 2021 through 2026.  There is no
# pre-2020 training sample, so 2020 is neutral (all percentiles become 0.5).
# Missing parity data is also neutral/fail-open.  The veto itself is strict:
# parity_pct must be > 0.12.
PARITY_MIN_PCT = 0.12
PARITY_SIGN_BY_YEAR = {
    2020: 0.0,
    2021: 1.0,
    2022: 1.0,
    2023: 1.0,
    2024: 1.0,
    2025: 1.0,
    2026: 1.0,
}

# own-gap drift in P_real (2026-09-16, handoff §0.45). Each candidate uses ONLY its own stock's opening gap,
# (open / prior close - 1) / (prior 14d Wilder ATR / prior close); no cross-name or market average (user decision).
# z = (gap - mean) / sd clipped +-GAP_CLIP; drift = beta * z * sigma_d * sqrt(clip(DTE,1,4)) shifts every historical
# move before P_real counts it. WALK-FORWARD: an entry in year Y uses GAP_FIT[Y], fitted only on stock-days before
# Y (fit_gap.py). Refit each January and add the year. A year with no key uses the latest earlier key; years before
# the first key get no drift. GAP_GAMMA = 0 turns it off and reproduces the pre-gap canon exactly.
GAP_GAMMA    = 1.0
GAP_FIT      = {          # entry year: (mean, sd, beta) of the own-gap z-score and slope -- fit_gap.py
    2021: (0.08660548083709516, 0.5059363532185642, 0.11278562841601494),   # 1,782 stock-days before 2021
    2022: (0.035928232842257046, 0.4373507621328155, 0.02198104994277543),   # 6,727 stock-days before 2022
    2023: (0.012304431622303366, 0.4334108357277347, 0.01409746365227111),   # 13,307 stock-days before 2023
    2024: (0.008988780889347925, 0.4366855243117909, 0.020259040409082763),   # 18,199 stock-days before 2024
    2025: (0.009307131876248102, 0.44369750398120916, 0.012393613268710595),   # 23,461 stock-days before 2025
    2026: (0.007973777505515946, 0.44659901060187274, 0.02597796046830985),   # 29,383 stock-days before 2026
}
GAP_CLIP     = 4.0
GAP_ATR_N    = 14
GAP_SIGMA_N  = 20         # sessions of log close changes, ending the session before entry

# execution targets, as multiples of fair (model) credit
MULT_BREAKEVEN = 0.965
# Min and target are multiples of THIS spread's model credit, not absolute
# credit/width levels. A flat level cannot work: model c/w ranges 0.379-0.564
# across a single snapshot (HON 0.379, GS 0.564) because it depends on where
# the strikes sit relative to spot, not just on delta. A 0.50 flat min was 32%
# ABOVE fair on HON and BELOW fair on GS — unreachable on cheap spreads and
# free on rich ones.
# min = the model credit itself, 1.00x (user 2026-09-13, reaffirmed late the same day
# after a brief 1.04x): below fair we are selling the spread for less than it is
# worth, so that is the floor AND the execution gate (MULT_WALKAWAY, same number).
# Target 1.04-1.10x: 1.04x is roughly break-even after the $1.30 commission, 1.10x
# is where the backtest edge lives. 3dp on the ratios so no two levels round together.
MULT_MIN, MULT_TARGET_LO, MULT_TARGET_HI = 1.00, 1.04, 1.10
MULT_WALKAWAY = 1.00   # fair value: below this the trade is a coin flip that pays the broker (break-even ~1.03x after commission)
# cross-sectional fair credit/width by delta and DTE (fit on 43,479 candidates 2020-26, med |err| 0.025)
FAIR_COEF = (0.4022, 2.3485, -12.464, 0.0077)

# smile fit
MIN_Q, Y_MAX, IV_LO, IV_HI = 5, 2.5, 0.03, 3.0
LN3 = math.log(3.0)
_U3 = np.array([1 / 3, 1 / 3, 1 / 3])


def vendor_year_parquet(year: int) -> str:
    """Path to the newest vendor chain file for *year*.

    2026 was rebuilt several times; `output/2026_sp500_last.parquet` is the June
    snapshot (ends 2026-06-05) and is SUPERSEDED by `_oot_combined` (ends 09-16).
    Scripts that hardcoded the plain name silently produced short frames --
    the parity feature died at 2026-06-04 that way and the fail-open 0.5
    default hid it for three months (2026-09-19).  Always resolve through here.
    """
    import os
    for suffix in ("_oot_combined", "_oot_refresh", ""):
        cand = f"output/{year}_sp500_last{suffix}.parquet"
        if os.path.exists(cand):
            return cand
    return f"output/{year}_sp500_last.parquet"


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


def parity_sign(year: int) -> float:
    """Return the latest causal same-strike parity sign known for *year*."""
    eligible = [y for y in PARITY_SIGN_BY_YEAR if y <= int(year)]
    return float(PARITY_SIGN_BY_YEAR[max(eligible)]) if eligible else 0.0


def chain_parity_signal(chain: pd.DataFrame) -> pd.DataFrame:
    """Build the canonical chain-level bullish parity signal from raw options.

    Formula (per Symbol/DataDate/ExpirationDate):
        median_K[IV_call(K) - IV_put(K)]
    using exact matched strikes whose absolute delta is in [0.35, 0.65].
    Rows require a positive, finite IV and a non-crossed quote.  This matches
    the historical option-direction research feature.
    """
    keys = ['Symbol', 'DataDate', 'ExpirationDate']
    out_cols = keys + ['parity_bull_raw', 'parity_pairs']
    required = keys + ['PutCall', 'StrikePrice', 'ImpliedVolatility',
                       'BidPrice', 'AskPrice']
    delta_col = 'Delta' if 'Delta' in chain.columns else 'AbsDelta'
    if (chain.empty or not set(required).issubset(chain.columns)
            or delta_col not in chain.columns):
        return pd.DataFrame(columns=out_cols)

    d = chain[required + [delta_col]].copy()
    d['PutCall'] = d['PutCall'].astype(str).str.lower().str.strip()
    d['iv'] = pd.to_numeric(d['ImpliedVolatility'], errors='coerce')
    d['abs_delta'] = pd.to_numeric(d[delta_col], errors='coerce').abs()
    d['bid'] = pd.to_numeric(d['BidPrice'], errors='coerce')
    d['ask'] = pd.to_numeric(d['AskPrice'], errors='coerce')
    d = d[
        d['PutCall'].isin(['put', 'call'])
        & d['abs_delta'].between(0.35, 0.65)
        & d['iv'].between(IV_LO, IV_HI, inclusive='neither')
        & (d['ask'] > d['bid'])
    ]
    if d.empty:
        return pd.DataFrame(columns=out_cols)

    piv = d.pivot_table(
        index=keys + ['StrikePrice'], columns='PutCall', values='iv', aggfunc='median'
    ).reset_index()
    if 'call' not in piv.columns or 'put' not in piv.columns:
        return pd.DataFrame(columns=out_cols)
    piv = piv.dropna(subset=['call', 'put']).copy()
    if piv.empty:
        return pd.DataFrame(columns=out_cols)
    piv['parity_bull_raw'] = piv['call'] - piv['put']
    return (
        piv.groupby(keys, sort=False)
        .agg(parity_bull_raw=('parity_bull_raw', 'median'),
             parity_pairs=('parity_bull_raw', 'size'))
        .reset_index()
    )


def warn_coverage(label: str, ok: 'pd.Series | np.ndarray', dates=None, tol: float = 0.02) -> float:
    """Warn when a fail-open feature is missing for more than *tol* of rows.

    Every gate in this file fails OPEN on missing data: parity defaults to the neutral 0.5,
    gap drift to 0, regime to cash.  That is right for one absent name on one day and wrong
    for an outage.  The parity feature died for three months in 2026 because nobody was told
    (audit 2026-09-19, weakness #6).  Call this wherever a fallback is applied.
    """
    ok = np.asarray(ok, dtype=bool)
    if ok.size == 0:
        return 0.0
    miss = 1.0 - ok.mean()
    if miss > tol:
        msg = f"WARNING: {label} missing for {miss:.1%} of {ok.size:,} rows (fail-open default applied)"
        if dates is not None:
            d = pd.to_datetime(pd.Series(np.asarray(dates))[~ok])
            if len(d):
                msg += f"; first {d.min().date()}, last {d.max().date()}"
                bad = d.dt.to_period('M').value_counts()
                full = sorted(str(m) for m, n in bad.items()
                              if n == pd.to_datetime(pd.Series(np.asarray(dates))).dt.to_period('M').value_counts().get(m, 0))
                if full:
                    msg += f"; ENTIRE months absent: {', '.join(full[:12])}"
        print(msg, flush=True)
    return miss


def add_parity_percentile(cands: pd.DataFrame,
                          raw_col: str = 'parity_bull_raw') -> pd.DataFrame:
    """Add causal signed parity and its daily bull-candidate percentile.

    Percentiles are formed before the GROUND threshold and regime filters.
    Non-bull rows and missing parity observations are assigned neutral 0.5.
    """
    C = cands.copy()
    if C.empty:
        C['parity_bull_signed'] = pd.Series(dtype=float)
        C['parity_pct'] = pd.Series(dtype=float)
        return C
    if raw_col not in C:
        C[raw_col] = np.nan
    years = pd.to_datetime(C['entry_date']).dt.year
    signs = years.map(parity_sign)
    C['parity_bull_signed'] = pd.to_numeric(C[raw_col], errors='coerce') * signs
    C['parity_pct'] = 0.5
    bull = C['spread_type'].eq('bull_put')
    active = bull & signs.ne(0) & C['parity_bull_signed'].notna()
    ranked = C.loc[active].groupby('entry_date', sort=False)['parity_bull_signed'].rank(
        method='average', pct=True
    )
    C.loc[active, 'parity_pct'] = ranked
    warn_coverage('parity (cp_iv_gap)', (~bull) | C['parity_bull_signed'].notna(), C['entry_date'])
    return C


def add_bear_parity_percentile(cands: pd.DataFrame, raw_col: str = 'parity_bull_raw') -> pd.DataFrame:
    """Mirrored parity for bear calls: percentile of (cp_iv_gap x causal sign) among the day's
    bear-call candidates.  High = more bearish.  Non-bear rows and missing data are neutral 0.5.
    Same construction as research/bear_regime_sweep.prepare(); parity_bull_raw = -cp_iv_gap."""
    C = cands.copy()
    C['bear_parity_pct'] = 0.5
    if C.empty or raw_col not in C:
        return C
    signs = pd.to_datetime(C['entry_date']).dt.year.map(parity_sign)
    raw = -pd.to_numeric(C[raw_col], errors='coerce') * signs
    active = C['spread_type'].eq('bear_call') & signs.ne(0) & raw.notna()
    C.loc[active, 'bear_parity_pct'] = raw[active].groupby(C.loc[active, 'entry_date'], sort=False).rank(method='average', pct=True)
    warn_coverage('bear parity (cp_iv_gap)', (~C['spread_type'].eq('bear_call')) | raw.notna(), C['entry_date'])
    return C


# ── 3. realized belief from daily closes ────────────────────────────────────
def p_real(cands: pd.DataFrame, closes: pd.DataFrame, window: int = WINDOW, mu=None) -> np.ndarray:
    """(p, q, ro) per candidate from the name's realized DTE-day moves vs the exact strikes.

    closes: columns ticker, date, close (daily). Strictly causal: only moves that COMPLETED
    before the entry date are used. Rows with < MIN_OBS valid moves get NaN.
    mu: optional per-candidate drift (return units, aligned to cands' row order) added to every historical
    move before it is counted (gap_drift). None or 0 = no drift.
    """
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
            r = np.where(ok, R[np.clip(idx, 0, len(R) - 1)] + MU[sub.index.values][:, None], np.nan)
            bs_ = np.where(bp[:, None], r <= ths[:, None], r >= ths[:, None]) & ok
            bl = np.where(bp[:, None], r <= thl[:, None], r >= thl[:, None]) & ok
            n = ok.sum(1); ns = bs_.sum(1); nl = bl.sum(1)
            t = np.column_stack([n - ns + PRIOR, nl + PRIOR, ns - nl + PRIOR]).astype(float)
            t = t / t.sum(1, keepdims=True); t[n < MIN_OBS] = np.nan
            out[sub.index.values] = t
    return out


def backtest_closes(store: str = 'output/daily_closes.parquet', spy_csv: str = 'data/spy_us_d.csv') -> pd.DataFrame:
    """The backtest's P_real close series: the vendor-seeded store restricted to SPY sessions.
    The store carries ~9 vendor holiday republishes a year (stale duplicate closes); dropping
    them is what makes this series match what the live IBKR store holds (sessions only)."""
    d = pd.read_parquet(store).dropna().drop_duplicates(['ticker', 'date'])
    d['date'] = pd.to_datetime(d['date']).dt.normalize()
    spy = set(pd.to_datetime(pd.read_csv(spy_csv, parse_dates=['Date']).Date).dt.normalize())
    return d[d.date.isin(spy)].sort_values(['ticker', 'date']).reset_index(drop=True)


# ── 3b. own-gap drift (§0.45) ───────────────────────────────────────────────
def name_gaps(bars: pd.DataFrame) -> pd.DataFrame:
    """Per-name opening gap in ATR units from daily OHLC bars (columns ticker, date, open, high, low, close).
    gap_t = (open_t / close_{t-1} - 1) / (ATR14_{t-1} / close_{t-1}), ATR = Wilder EWM of true range.
    A name's first 60 bars carry no gap (ATR warm-up)."""
    out = []
    for tk, y in bars.groupby('ticker'):
        y = y.dropna(subset=['open', 'high', 'low', 'close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)
        c, h, l, o = y.close, y.high, y.low, y.open
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / GAP_ATR_N, adjust=False).mean()
        g = (o / c.shift(1) - 1) / (atr.shift(1) / c.shift(1))
        g[y.index < 60] = np.nan
        out.append(pd.DataFrame({'ticker': tk, 'date': pd.to_datetime(y.date).dt.normalize(), 'gap': g.values}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=['ticker', 'date', 'gap'])


def gap_fit(year: int) -> tuple:
    """(mean, sd, beta) for an entry year: GAP_FIT[year], else the latest earlier key, else no drift (beta 0)."""
    ks = [k for k in GAP_FIT if k <= year]
    return GAP_FIT[max(ks)] if ks else (0.0, 1.0, 0.0)


def sigma_unit(cands: pd.DataFrame, closes: pd.DataFrame) -> np.ndarray:
    """sigma_d * sqrt(clip(DTE,1,4)) per candidate (row order), sigma_d = std of the name's last GAP_SIGMA_N daily
    log close changes ending the session before entry. NaN where the name lacks history."""
    C = cands.reset_index(drop=True)
    ed = pd.to_datetime(C.entry_date).dt.normalize()
    px = closes.dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
    sd = np.full(len(C), np.nan)
    for tk, g in C.groupby('ticker'):
        s = px[px.ticker == tk]
        if len(s) <= GAP_SIGMA_N:
            continue
        dates = pd.to_datetime(s.date).dt.normalize().values.astype('datetime64[ns]')
        lr = np.diff(np.log(s.close.values.astype(float)))                 # lr[i] = change into session i+1
        cs1, cs2 = np.r_[0, np.cumsum(lr)], np.r_[0, np.cumsum(lr * lr)]
        k = np.searchsorted(dates, ed[g.index].values.astype('datetime64[ns]'))  # sessions strictly before entry: 0..k-1
        hi = k - 1; lo = hi - GAP_SIGMA_N                                     # changes lr[lo..hi-1] end at session k-1
        okk = lo >= 0; n = GAP_SIGMA_N
        m1 = np.where(okk, (cs1[np.clip(hi, 0, None)] - cs1[np.clip(lo, 0, None)]) / n, np.nan)
        m2 = np.where(okk, (cs2[np.clip(hi, 0, None)] - cs2[np.clip(lo, 0, None)]) / n, np.nan)
        sd[g.index.values] = np.sqrt(np.clip((m2 - m1 * m1) * n / (n - 1), 0, None))
    return sd * np.sqrt(np.clip(pd.to_numeric(C.DTE, errors='coerce').fillna(1).values, 1, 4))


def gap_drift(cands: pd.DataFrame, closes: pd.DataFrame, gaps: pd.DataFrame, gamma: float | None = None, fit: tuple | None = None) -> np.ndarray:
    """Per-candidate drift mu (return units) for p_real, aligned to cands' row order, from the candidate's OWN gap.
    gaps: columns ticker, date, gap (name_gaps). mu = gamma * beta * clip((gap - mean) / sd, +-GAP_CLIP) * sigma_unit,
    (mean, sd, beta) = gap_fit(entry year) unless `fit` is given. No gap for the name that day, or no sigma -> 0."""
    gamma = GAP_GAMMA if gamma is None else gamma                         # read at call time, not import time
    C = cands.reset_index(drop=True)
    if gamma == 0 or gaps is None or len(gaps) == 0:
        return np.zeros(len(C))
    ed = pd.to_datetime(C.entry_date).dt.normalize()
    G = gaps.assign(date=pd.to_datetime(gaps.date).dt.normalize()).drop_duplicates(['ticker', 'date']).set_index(['ticker', 'date']).gap
    x = pd.MultiIndex.from_arrays([C.ticker.values, ed.values]).map(G).values.astype(float)
    m_, s_, b_ = (np.array([gap_fit(y)[j] for y in ed.dt.year.values]) for j in range(3)) if fit is None else (np.full(len(C), fit[j]) for j in range(3))
    z = np.clip((x - m_) / s_, -GAP_CLIP, GAP_CLIP)
    su = sigma_unit(C, closes)
    warn_coverage('own-gap drift', np.isfinite(x) & np.isfinite(su), ed)
    return np.nan_to_num(gamma * b_ * z * su)


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
def score(C: pd.DataFrame, closes: pd.DataFrame, k: float = K, thr: float = THR, credit_col: str = 'model_credit', mu=None) -> pd.DataFrame:
    """C must already carry model_credit + D_ent (price_spreads). Adds p,q,ro,w_star,G,EV,DKL,GROUND,qualified.

    credit_col: the credit the Kelly growth is computed on. 'model_credit' (backtest canon) or
    'net_credit' (live: IBKR quoted mid, user decision 2026-09-13 — uncapped)."""
    C = C.copy()
    P = p_real(C, closes, mu=mu)
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
    # min/target are absolute credit/width levels; breakeven stays relative to
    # fair because it is a property of THIS spread, not an execution target.
    # 3dp on the ratios so two adjacent levels can never round together.
    out = {'basis': basis, 'fair_cw': round(fair, 3),
           'breakeven_cw': round(fair * MULT_BREAKEVEN, 3), 'walkaway_cw': round(fair * MULT_WALKAWAY, 3), 'min_cw': round(fair * MULT_MIN, 3),
           'target_lo_cw': round(fair * MULT_TARGET_LO, 3), 'target_hi_cw': round(fair * MULT_TARGET_HI, 3)}
    if width:
        W = float(width)
        out.update({'width': round(W, 2), 'min_credit': round(out['min_cw'] * W, 2), 'walkaway_credit': round(out['walkaway_cw'] * W, 2),
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
    'window':    f'{WINDOW} full sessions of realized DTE-matched moves vs the exact strikes (P_real, every trading day)',
    'selection': (f'bull puts only; prior-close SPY>100d SMA; '
                  f'parity percentile > {PARITY_MIN_PCT:.0%}; top-{TOP_N}/day; '
                  f'k={K:g}, GROUND ≥ {THR:g}'),
    'gap':       f'P_real drift = {GAP_GAMMA:g} × β_year σ × z(the stock\'s OWN opening gap, ATR units) × √DTE; no market average; β walk-forward, fitted on years before entry (§0.45)',
    'scoring':   'G = Kelly log-growth on P_real at the smile-fit model credit; GROUND = (e^G−1)·e^(−k·D_ent)',
    'fill':      f'{FILL_MULT:.2f}× smile-fit model credit (19 real fills), no commission',
    'targets':   f'min {MULT_MIN:.2f}× model credit, target {MULT_TARGET_LO:.2f}–{MULT_TARGET_HI:.2f}×',
}
