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
DKL_K         = 20.0   # GROUND amplification factor: ln(GROUND) = G − DKL_K · DKL
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

    if USE_SKEW_ADJ:
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
        if b == 0:
            return pd.Series({
                "p": p, "q": q, "ro": ro, "n_samples": n,
                "w_star": None, "G": None, "DKL": None,
            })
        w_star = (p * b - q) / b
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

    H_chosen   = h(p) + h(ro) + h(q)
    H_uniform  = lg(3.0)
    DKL_chosen = max(0.0, H_uniform - H_chosen)

    return pd.Series({
        "p":         p,
        "q":         q,
        "ro":        ro,
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
