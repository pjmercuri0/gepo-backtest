"""
ground.py
Paper-faithful GROUND ratio (Mercurio, Wu, Xie 2020, eq. 25), with a
denominator regularization to handle ties:

    Γ_m = (G_a − G_b) / (1 + D_KL(R_a||U) − D_KL(R_b||U))

Where R_b is the MINIMUM-RISK REFERENCE: the candidate with the smallest
D_KL from uniform that week. The "+1" in the denominator handles the case
where multiple candidates share the minimum DKL (denominator → 0 → blow-up).
At ties, GROUND reduces cleanly to (G_a − G_b), the pure growth advantage
over the reference.

PATCH (2026-05-06):
  - _score_row computes G and DKL only; no GROUND yet.
  - GROUND is computed in select_trades, per week, against R_b = min-DKL.
  - Denominator is 1 + (DKL_a − DKL_b) to avoid 0 at ties.
  - R_b itself is excluded from selection.
  - Greek-based (p, q, ro) estimator from prior patch retained.
"""

import math
import numpy as np
import pandas as pd

import config
import historical_probs as hp


# Module-level toggle and lookup, set by backtest.py before scoring
USE_DRIFT     = False
DRIFT_LOOKUP  = {}   # {(Symbol, Timestamp): drift_annualized}
USE_RV_BLEND  = False
RV_LOOKUP     = {}   # {(Symbol, Timestamp): rv_annualized}
IV_WEIGHT     = 0.5  # weight on IV when blending; (1 - IV_WEIGHT) goes to RV
USE_SKEW_ADJ  = False
SKEW_ALPHA    = 0.5  # scale factor for the skew adjustment to p
USE_ND2       = False  # if True, use N(d2) instead of delta for P(ITM). More correct than N(d1)=delta.
USE_EMPIRICAL = False  # if True, use historical lookup table (build via build_empirical_probs.py).
DKL_REFERENCE = "rv_vs_iv"  # canonical 2026-06-05: tenor-matched 10d RV vs IV via BS d2.
PROB_BASIS    = "rv"   # canonical 2026-06-09: RV-implied N(d2) (p,q,ro) in G — same P as DKL's belief side.
                       # Full 2020-25 count-matched: $26.37 vs $22.76/pick, WR 48.4% vs 45.7%, Sh 2.17 vs 2.04;
                       # robust to k∈[5,20], RV window/estimator, fill 0.70-0.90 (backtest_g_probs*.py).
                       # (The 2026-06-04 "rv worse" result was under OLD canon: pre-rv_vs_iv DKL + regime gate.)
DKL_K         = 10.0   # Canonical 2026-06-12 (corrected solver): k=10 w/ thr=0.05 = the GROWTH-OPTIMAL cell on the plateau — selection criterion matches the framework objective (max expected growth). Was briefly 16 (Calmar pick, reverted same day: DD is a path statistic, criterion-shopped).
PROB_PARTITION = "3-state"  # canonical: α-weighted partial-zone. "2-state-loss" Sh 1.89/DD -13.6%; "2-state-win" Sh 0.65/DD -73.6%. Both reverted.
SCORE_B_CAP   = None   # If set (e.g. 1.0), score uses b = min(b_actual, cap); realized P&L uses b_actual.
RANKING_MODE  = "GROUND"  # "GROUND" (canonical Kelly-EV-with-DKL-discount) | "G_only" | "DKL_only" | "Jk_legacy" (pre-2026-05-13 J_k)


def score_candidates(candidates: pd.DataFrame,
                     history: pd.DataFrame = None,
                     lookback_days: int = None) -> pd.DataFrame:
    """
    history and lookback_days kept for run.py compatibility.
    Greek-based estimator ignores them.
    """
    if candidates.empty:
        return candidates
    results = candidates.apply(_score_row, axis=1, result_type="expand")
    return pd.concat([candidates, results], axis=1)


def _score_row(row: pd.Series) -> pd.Series:
    """Compute (p, q, ro), w*, ℓ(w*), DKL for one candidate. GROUND comes later.

    The "G" column returned here is the Kelly log-growth ℓ(w*) =
    E_p[ln(1+w*X)] (paper notation). The canonical Kelly EV is
    E = exp(ℓ) − 1 (returned as "EV"); the growth signal in J_k = g − k·DKL
    is g := ln E.

    Canonical scoring uses per-spread payoffs:
        b = net_credit / max_loss
        α(b) = (b-1) / (2b)   (partial = uniform mean between +b and -1)
    so the Kelly outcomes are {+b, +αb, −1}.
    """

    key = (row["ticker"], pd.Timestamp(row["entry_date"]))

    if USE_EMPIRICAL:
        # Historical lookup from training years
        p, q, ro, n = hp.empirical_lookup_probs(
            short_delta = row["short_delta"],
            long_delta  = row["long_delta"],
            iv_short    = float(row["IV"]),
            iv_long     = float(row.get("long_IV", row["IV"])),
            dte_days    = int(row["DTE"]),
        )
    elif USE_ND2:
        # N(d2)-based P(ITM) — textbook risk-neutral probability vs the
        # delta-as-probability approximation (which is really N(d1)).
        p, q, ro, n = hp.nd2_probs_for_spread(
            short_strike = float(row["short_strike"]),
            long_strike  = float(row["long_strike"]),
            spot         = float(row["entry_price"]),
            iv_short     = float(row["IV"]),
            iv_long      = float(row.get("long_IV", row["IV"])),
            dte_days     = int(row["DTE"]),
            spread_type  = row["spread_type"],
        )
        if p is None:
            # Fallback for missing IV data
            p, q, ro, n = hp.empirical_probs_from_deltas(
                short_delta = row["short_delta"],
                long_delta  = row["long_delta"],
            )
    elif USE_SKEW_ADJ:
        p, q, ro, n = hp.skew_adjusted_probs(
            short_delta = row["short_delta"],
            long_delta  = row["long_delta"],
            iv_short    = row["IV"],
            iv_long     = row.get("long_IV", row["IV"]),
            alpha       = SKEW_ALPHA,
        )
    elif USE_RV_BLEND:
        # IV-RV blended vol with optional drift adjustment
        rv    = RV_LOOKUP.get(key, None)
        drift = DRIFT_LOOKUP.get(key, 0.0) if USE_DRIFT else 0.0
        if drift is None or pd.isna(drift):
            drift = 0.0
        if rv is None or pd.isna(rv):
            # No RV yet (early in series) — fall back to Greek
            p, q, ro, n = hp.empirical_probs_from_deltas(
                short_delta = row["short_delta"],
                long_delta  = row["long_delta"],
            )
        else:
            p, q, ro, n = hp.vol_blended_probs(
                short_strike = float(row["short_strike"]),
                long_strike  = float(row["long_strike"]),
                spot         = float(row["entry_price"]),
                iv           = float(row["IV"]),
                rv           = float(rv),
                dte_days     = int(row["DTE"]),
                drift        = float(drift),
                spread_type  = row["spread_type"],
                iv_weight    = IV_WEIGHT,
            )
    elif USE_DRIFT:
        # Drift-adjusted real-world probabilities (IV only)
        drift = DRIFT_LOOKUP.get(key, None)
        if drift is None or pd.isna(drift):
            p, q, ro, n = hp.empirical_probs_from_deltas(
                short_delta = row["short_delta"],
                long_delta  = row["long_delta"],
            )
        else:
            p, q, ro, n = hp.drift_adjusted_probs(
                short_strike = float(row["short_strike"]),
                long_strike  = float(row["long_strike"]),
                spot         = float(row["entry_price"]),
                iv           = float(row["IV"]),
                dte_days     = int(row["DTE"]),
                drift        = float(drift),
                spread_type  = row["spread_type"],
            )
    else:
        # Original Greek-based estimator
        p, q, ro, n = hp.empirical_probs_from_deltas(
            short_delta = row["short_delta"],
            long_delta  = row["long_delta"],
        )

    # PROB_BASIS='rv' override: substitute RV-implied (p, q, ro) computed
    # via BS d2 using realized vol per leg instead of implied vol. Since
    # RV < IV on average (variance risk premium), RV-implied p_win is
    # HIGHER than IV-implied → G higher → more permissive selection.
    # Bets the VRP gap persists ~80% of the time.
    if PROB_BASIS == "rv" and 'rv_30d' in row.index:
        rv = row.get('rv_30d')
        if rv is not None and not pd.isna(rv) and rv > 0:
            rv = min(max(float(rv), 0.05), 2.0)  # clip pathological values
            p_rv, q_rv, ro_rv, _ = hp.nd2_probs_for_spread(
                short_strike = float(row["short_strike"]),
                long_strike  = float(row["long_strike"]),
                spot         = float(row["entry_price"]),
                iv_short     = rv,
                iv_long      = rv,
                dte_days     = int(row["DTE"]),
                spread_type  = row["spread_type"],
            )
            if p_rv is not None and p_rv > 0 and q_rv > 0 and p_rv + q_rv <= 1.0:
                p, q, ro = p_rv, q_rv, ro_rv

    # PROB_PARTITION collapse: optionally fold partial-zone mass into win or loss.
    # "2-state-loss" → q += ro (conservative; tested → worse Sharpe + 2× DD)
    # "2-state-win"  → p += ro (aggressive; assumes partial outcomes pay like wins)
    # Canonical "3-state" keeps the α-weighted partial-zone math intact.
    if PROB_PARTITION == "2-state-loss" and p is not None and q is not None and ro is not None:
        q = q + ro
        ro = 0.0
    elif PROB_PARTITION == "2-state-win" and p is not None and q is not None and ro is not None:
        p = p + ro
        ro = 0.0

    if p is None or p <= 0 or q <= 0 or p + q > 1.0:
        return pd.Series({
            "p": p, "q": q, "ro": ro, "n_samples": n,
            "w_star": None, "G": None, "DKL": None,
        })

    # Per-spread payoffs from the actual credit/risk geometry:
    #   b   = net_credit / max_loss
    #   α(b)= (b-1) / (2b)   from uniform-in-strikes linear-payoff geometry
    # so partial-zone P&L per unit at risk averages αb = (b-1)/2 between
    # +b (at K_short) and -1 (at K_long).
    nc = float(row["net_credit"])
    ml = float(row["max_loss"])
    if ml <= 0 or nc <= 0:
        return pd.Series({
            "p": p, "q": q, "ro": ro, "n_samples": n,
            "w_star": None, "G": None, "DKL": None,
        })
    b_actual = nc / ml
    # Optional score-side cap: when SCORE_B_CAP is set (e.g. 1.0), the
    # scoring quantities (Kelly w*, G, EV, DKL) are computed as if the
    # spread had b = min(b_actual, SCORE_B_CAP), even though realized
    # P&L still uses b_actual. The intuition: Kelly's prediction at
    # very high b is dominated by a rare-but-huge-win regime that
    # flat-sized trading does not realize, so it shouldn't drive the
    # ranking. Production has SCORE_B_CAP = None (use actual b).
    b = b_actual if SCORE_B_CAP is None else min(b_actual, SCORE_B_CAP)
    a = 0.0 if b >= 1.0 else (b - 1.0) / (2.0 * b)

    # Kelly FOC with outcomes {+b, +αb, −1} → quadratic Aw² + Bw + C = 0.
    A = -a * b * b
    B = a * b * b * (p + ro) - b * (p + ro * a + q * (1 + a))
    C = p * b + ro * a * b - q

    if A == 0:
        if b == 0 or (p + q) <= 0:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        # α = 0 (b ≥ 1): the partial state pays zero, the FOC is LINEAR:
        #   pb/(1+wb) = q/(1−w)  →  w* = (pb − q) / (b(p + q)).
        # The old (pb − q)/b here was the TWO-outcome Kelly formula, wrong
        # whenever r0 > 0 — it understated w* (and hence G) on every b ≥ 1
        # candidate (error #75, caught 2026-06-11 via the paper's worked
        # example failing its own first-order condition).
        w_star = (p * b - q) / (b * (p + q))
    else:
        disc = B * B - 4 * A * C
        if disc < 0:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        s = math.sqrt(disc)
        r1 = (-B - s) / (2 * A)
        r2 = (-B + s) / (2 * A)
        cands = [r for r in (r1, r2) if 0 < r < 1]
        if not cands:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        w_star = cands[0]
    w_star = float(np.clip(w_star, 0.01, 0.99))

    def lg(x):
        return math.log(max(x, 1e-10), config.LOG_BASE)

    # ℓ(w*) — the classical Kelly log-growth at the Kelly-optimal sizing
    # fraction. In paper notation this is ℓ(w*; p, x) = E_p[ln(1+w*X)].
    # Stored as the "G" column for backward compatibility with downstream
    # consumers (results.py, ranker.py, mock_data.py, webapp). The
    # canonical Kelly EV is then E = exp(ℓ) − 1 (column "EV" below) and
    # the growth signal in paper notation is g := ln E.
    ell = (p  * lg(1.0 + w_star * b) +
           ro * lg(1.0 + w_star * a * b) +
           q  * lg(1.0 - w_star))

    # EV per dollar wagered (size-independent): linear expected return
    # under the canonical per-spread payoffs {+b, +αb, -1}.
    EV = p * b + ro * a * b - q

    def h(prob):
        return -prob * lg(prob) if prob > 0 else 0.0

    p_emp = q_emp = ro_emp = None  # filled only by empirical_vs_delta branch
    if DKL_REFERENCE == "maxent_ro":
        # Reference = max-entropy given ro fixed: (0.5(1-ro), ro, 0.5(1-ro)).
        half = 0.5 * (1.0 - ro) if (1.0 - ro) > 0 else 1e-12
        dkl = 0.0
        if p > 0:  dkl += p * lg(p / half)
        if q > 0:  dkl += q * lg(q / half)
        DKL_chosen = max(0.0, dkl)
    elif DKL_REFERENCE == "empirical_vs_delta":
        # P = empirical bucket-conditioned realized frequency (historical truth)
        # Q = current spread's delta-derived probabilities (market pricing claim)
        # Full forward DKL(P_hist || Q_delta) — symmetric on all three legs.
        # iv_rank_bucket (if present on the row) narrows the lookup to cells
        # matching the underlying's name-specific vol regime — captures vol
        # mean-reversion edge that the cross-sectional iv_bucket misses.
        p_emp, q_emp, ro_emp, _ = hp.empirical_lookup_probs(
            short_delta = row["short_delta"],
            long_delta  = row["long_delta"],
            iv_short    = float(row["IV"]),
            iv_long     = float(row.get("long_IV", row["IV"])),
            dte_days    = int(row["DTE"]),
            spread_type = row.get("spread_type"),
            iv_rank_bucket = row.get("iv_rank_bucket"),
        )
        if p_emp is None or p_emp <= 0:
            H_chosen = h(p) + h(ro) + h(q)
            DKL_chosen = max(0.0, lg(3.0) - H_chosen)
        else:
            dkl = 0.0
            if p_emp  > 0 and p  > 0: dkl += p_emp  * lg(p_emp  / p)
            if ro_emp > 0 and ro > 0: dkl += ro_emp * lg(ro_emp / ro)
            if q_emp  > 0 and q  > 0: dkl += q_emp  * lg(q_emp  / q)
            DKL_chosen = max(0.0, dkl)
    elif DKL_REFERENCE == "q_down_ro_sym":
        # Canonical 2026-06-04: one-sided downside semi-divergence.
        # - p leg: NO penalty (we don't care if empirical wins more or less than delta)
        # - q leg: penalize ONLY when q_emp > q_delta (history says loss is MORE likely)
        # - ro leg: symmetric (mass shift in either direction is signal)
        # Threshold tuned at 0.003; gives best Sharpe + good trade volume on 2023-25 sweep.
        p_emp, q_emp, ro_emp, _ = hp.empirical_lookup_probs(
            short_delta = row["short_delta"],
            long_delta  = row["long_delta"],
            iv_short    = float(row["IV"]),
            iv_long     = float(row.get("long_IV", row["IV"])),
            dte_days    = int(row["DTE"]),
            spread_type = row.get("spread_type"),
        )
        if p_emp is None or q_emp is None:
            H_chosen = h(p) + h(ro) + h(q)
            DKL_chosen = max(0.0, lg(3.0) - H_chosen)
        else:
            dkl = 0.0
            if q_emp > 0 and q > 0 and q_emp > q:
                dkl += q_emp * lg(q_emp / q)
            if ro_emp > 0 and ro > 0:
                dkl += abs(ro_emp * lg(ro_emp / ro))
            DKL_chosen = max(0.0, dkl)
    elif DKL_REFERENCE in ("rv_vs_iv", "iv_vs_rv"):
        # 2026-06-05 experiment: DKL between RV-implied and IV-implied (p,q,ro),
        # both via BS d2 on the SAME spread. Captures per-spread VRP intensity.
        # "rv_vs_iv" = D(P_rv || Q_iv)  → dominated by WIN-leg disagreement
        # "iv_vs_rv" = D(P_iv || Q_rv)  → dominated by LOSS-leg disagreement
        # Canonical (penalty) sign: high DKL = downweight (VRP gap = priced-in
        # risk that RV doesn't see). Won't catch real VRP-as-edge if that's the
        # right read — would need sign flip. Test both empirically.
        rv = row.get("rv_30d")
        iv_short = float(row["IV"])
        iv_long  = float(row.get("long_IV", row["IV"]))
        if (rv is None or pd.isna(rv) or rv <= 0
                or iv_short <= 0 or iv_long <= 0):
            H_chosen = h(p) + h(ro) + h(q)
            DKL_chosen = max(0.0, lg(3.0) - H_chosen)
            p_emp = q_emp = ro_emp = None
        else:
            rv = min(max(float(rv), 0.05), 2.0)
            p_iv, q_iv, ro_iv, _ = hp.nd2_probs_for_spread(
                short_strike=float(row["short_strike"]),
                long_strike=float(row["long_strike"]),
                spot=float(row["entry_price"]),
                iv_short=iv_short, iv_long=iv_long,
                dte_days=int(row["DTE"]),
                spread_type=row["spread_type"],
            )
            p_rv, q_rv, ro_rv, _ = hp.nd2_probs_for_spread(
                short_strike=float(row["short_strike"]),
                long_strike=float(row["long_strike"]),
                spot=float(row["entry_price"]),
                iv_short=rv, iv_long=rv,
                dte_days=int(row["DTE"]),
                spread_type=row["spread_type"],
            )
            if None in (p_iv, p_rv):
                H_chosen = h(p) + h(ro) + h(q)
                DKL_chosen = max(0.0, lg(3.0) - H_chosen)
                p_emp = q_emp = ro_emp = None
            else:
                dkl = 0.0
                if DKL_REFERENCE == "rv_vs_iv":
                    # D(P_rv || Q_iv): weights by P_rv
                    if p_rv  > 0 and p_iv  > 0: dkl += p_rv  * lg(p_rv  / p_iv)
                    if q_rv  > 0 and q_iv  > 0: dkl += q_rv  * lg(q_rv  / q_iv)
                    if ro_rv > 0 and ro_iv > 0: dkl += ro_rv * lg(ro_rv / ro_iv)
                else:  # iv_vs_rv
                    # D(P_iv || Q_rv): weights by P_iv
                    if p_iv  > 0 and p_rv  > 0: dkl += p_iv  * lg(p_iv  / p_rv)
                    if q_iv  > 0 and q_rv  > 0: dkl += q_iv  * lg(q_iv  / q_rv)
                    if ro_iv > 0 and ro_rv > 0: dkl += ro_iv * lg(ro_iv / ro_rv)
                DKL_chosen = max(0.0, dkl)
                # Display: surface IV-implied probs as p_hat/q_hat/ro_hat (the
                # market's Q reference). Under PROB_BASIS="rv" (canon 2026-06-09)
                # p/q/ro already ARE the RV-implied P, so surfacing RV here would
                # duplicate them and hide Q entirely.
                p_emp, q_emp, ro_emp = p_iv, q_iv, ro_iv
    else:
        # Canonical: uniform (1/3 each).
        H_chosen   = h(p) + h(ro) + h(q)
        H_uniform  = lg(3.0)
        DKL_chosen = max(0.0, H_uniform - H_chosen)
        p_emp = q_emp = ro_emp = None

    return pd.Series({
        "p":         p,
        "q":         q,
        "ro":        ro,
        "p_hat":     round(p_emp, 4)  if p_emp  is not None else None,
        "q_hat":     round(q_emp, 4)  if q_emp  is not None else None,
        "ro_hat":    round(ro_emp, 4) if ro_emp is not None else None,
        "n_samples": n,
        "w_star":    round(w_star, 4),
        "G":         round(ell, 6),  # ℓ(w*), the Kelly log-growth
        "EV":        round(EV, 6),
        "DKL":       round(DKL_chosen, 6),
    })


def _compute_ground_for_week(week_df: pd.DataFrame) -> pd.DataFrame:
    """
    For a single entry-date's worth of scored candidates, identify
    the minimum-risk reference R_b (smallest DKL > 0) and compute
    paper-faithful GROUND for every other candidate.
    """
    out = week_df.copy()
    out["GROUND"]     = np.nan
    out["is_ref_Rb"]  = False
    out["G_ref"]      = np.nan
    out["DKL_ref"]    = np.nan
    out["DKL_diff"]   = np.nan   # = DKL_a − DKL_b, the meaningful piece of the denominator
    out["G_diff"]     = np.nan   # = G_a − G_b, the meaningful piece of the numerator

    valid = out[(out["G"].notna()) & (out["DKL"].notna()) & (out["DKL"] > 0)]
    if len(valid) < 2:
        # Need at least two candidates: one reference + one to score
        return out

    # R_b = candidate with smallest DKL this week
    ref_idx = valid["DKL"].idxmin()
    G_b   = valid.loc[ref_idx, "G"]
    DKL_b = valid.loc[ref_idx, "DKL"]
    out.loc[ref_idx, "is_ref_Rb"] = True

    # Canonical GROUND ratio (2026-05-12 revision):
    #   GROUND = exp(G) / exp(k · DKL) = exp(G − k · DKL)
    # which is the entropy-regularized expected utility of
    # Hansen-Sargent / Maccheroni-Marinacci in multiplicative form. The
    # numerator is the per-trade wealth multiplier, the denominator is
    # the entropic risk multiplier. We store the log in the GROUND column
    # (J_k = G − k · DKL, additive form) for ranking convenience; the
    # display layer exponentiates to show the ratio. Module-level
    # DKL_K is the amplification factor (default 1, the in-sample
    # optimum on the 2020-2024 candidate pool).
    for idx in valid.index:
        G_a   = out.loc[idx, "G"]
        DKL_a = out.loc[idx, "DKL"]
        if RANKING_MODE == "G_only":
            score = G_a
        elif RANKING_MODE == "DKL_only":
            # Lower DKL is preferred → store -DKL so the existing "max" selection works.
            score = -DKL_a
        elif RANKING_MODE == "Jk_legacy":
            # Pre-2026-05-13 canon: J_k = G − k·DKL. Hansen-Sargent multiplier
            # preferences functional. Retained for paper baselines and the
            # k-sweep / single-factor tables. Selection by argmax J_k.
            score = G_a - DKL_K * DKL_a
        else:
            # Canonical (2026-05-13+): Γᵢ = Kelly EV · exp(−k·DKL).
            # The "G" column stores ℓ(w*), the Kelly log-growth. Kelly EV
            # E = exp(ℓ) − 1 is the expected wealth gain per trade at
            # Kelly-optimal sizing (variance-adjusted via the log-utility
            # expectation inside ℓ). Define the growth signal g := ln E;
            # then Γᵢ = exp(g) · exp(−k·DKL) = exp(J_k) exactly with
            # J_k = g − k·DKL, the Hansen-Sargent multiplier-preferences
            # functional. The displayed score reads as a per-trade %
            # risk-adjusted return.
            #
            # Filter ℓ > 0 (Kelly EV > 0) — growth-negative candidates
            # have no risk-adjusted return and are dropped (NaN score).
            if G_a is None or pd.isna(G_a) or G_a <= 0:
                score = float("nan")
            else:
                score = (math.exp(G_a) - 1.0) * math.exp(-DKL_K * DKL_a)
        out.loc[idx, "GROUND"]   = round(score, 6)
        out.loc[idx, "G_ref"]    = G_b
        out.loc[idx, "DKL_ref"]  = DKL_b
        out.loc[idx, "G_diff"]   = round(G_a - G_b, 6)
        out.loc[idx, "DKL_diff"] = round(DKL_a - DKL_b, 6)

    return out


def select_trades(scored: pd.DataFrame, top_n: int = None) -> pd.DataFrame:
    """
    1. Compute paper-faithful GROUND per week (vs. min-DKL reference R_b).
    2. For each (ticker, entry_date), pick the spread_type with higher GROUND.
    3. Apply GROUND_THRESHOLD filter.
    4. Keep top_n per week.
    """
    if scored.empty:
        return pd.DataFrame()

    # Compute GROUND week by week (per entry_date)
    weeks = []
    for entry_date, week_df in scored.groupby("entry_date"):
        weeks.append(_compute_ground_for_week(week_df))
    scored = pd.concat(weeks, ignore_index=True)

    # Select best per ticker
    selected = []
    for (ticker, entry_date), grp in scored.groupby(["ticker", "entry_date"]):
        valid = grp[grp["GROUND"].notna()]
        if valid.empty:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS", "reason": "no GROUND (reference or invalid)"
            })
            continue

        best = valid.loc[valid["GROUND"].idxmax()]

        if best["GROUND"] < config.GROUND_THRESHOLD:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS",
                "reason": f"GROUND {best['GROUND']:.4f} < {config.GROUND_THRESHOLD}",
                "best_ground": best["GROUND"],
            })
            continue

        row = best.to_dict()
        row["decision"] = best["spread_type"]
        selected.append(row)

    out = pd.DataFrame(selected)
    if out.empty or top_n is None:
        return out

    trades  = out[out["decision"] != "PASS"].copy()
    passes  = out[out["decision"] == "PASS"].copy()

    if trades.empty:
        return out

    keep_idx = (
        trades.sort_values("GROUND", ascending=False)
              .groupby("entry_date")
              .head(top_n)
              .index
    )
    demoted = trades.drop(keep_idx).copy()
    if not demoted.empty:
        demoted["decision"]    = "PASS"
        demoted["best_ground"] = demoted["GROUND"]
        demoted["reason"]      = f"below top {top_n}"

    kept = trades.loc[keep_idx]
    return pd.concat([kept, demoted, passes], ignore_index=True)


def select_trades_by_edge(scored: pd.DataFrame, top_n: int = None) -> pd.DataFrame:
    """
    Edge-based selection (experimental):
    EDGE = Implied Odds Return - GROUND
    where Implied Odds Return = (net_credit / max_loss - 1) * 100

    For each (ticker, entry_date), pick the spread_type with highest EDGE.
    Keep top_n per week by EDGE.
    """
    if scored.empty:
        return pd.DataFrame()

    # Calculate implied odds return and edge
    scored = scored.copy()
    scored["implied_odds_ret"] = (
        (scored["net_credit"] / scored["max_loss"] - 1.0) * 100
    ).round(2)

    # Compute GROUND week by week (same as canonical)
    weeks = []
    for entry_date, week_df in scored.groupby("entry_date"):
        weeks.append(_compute_ground_for_week(week_df))
    scored = pd.concat(weeks, ignore_index=True)

    # Recalculate implied odds and EDGE after GROUND is computed
    scored["implied_odds_ret"] = (
        (scored["net_credit"] / scored["max_loss"] - 1.0) * 100
    ).round(2)
    scored["EDGE"] = (scored["implied_odds_ret"] - scored["GROUND"]).round(4)

    # Select best per ticker by EDGE
    selected = []
    for (ticker, entry_date), grp in scored.groupby(["ticker", "entry_date"]):
        valid = grp[grp["EDGE"].notna()]
        if valid.empty:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS", "reason": "no EDGE (invalid)"
            })
            continue

        best = valid.loc[valid["EDGE"].idxmax()]

        row = best.to_dict()
        row["decision"] = best["spread_type"]
        selected.append(row)

    out = pd.DataFrame(selected)
    if out.empty or top_n is None:
        return out

    trades  = out[out["decision"] != "PASS"].copy()
    passes  = out[out["decision"] == "PASS"].copy()

    if trades.empty:
        return out

    keep_idx = (
        trades.sort_values("EDGE", ascending=False)
              .groupby("entry_date")
              .head(top_n)
              .index
    )
    demoted = trades.drop(keep_idx).copy()
    if not demoted.empty:
        demoted["decision"]    = "PASS"
        demoted["EDGE_val"]    = demoted["EDGE"]
        demoted["reason"]      = f"below top {top_n} by EDGE"

    kept = trades.loc[keep_idx]
    return pd.concat([kept, demoted, passes], ignore_index=True)
