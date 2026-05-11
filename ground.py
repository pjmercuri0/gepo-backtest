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
DKL_K         = 20.0   # GROUND v3 amplification factor: GROUND = G / 3 ** (DKL_K * DKL)
RANKING_MODE  = "GROUND"  # "GROUND" | "G_only" | "DKL_only" — for single-factor baselines


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
    """Compute (p, q, ro), w*, G, DKL for one candidate. GROUND comes later."""

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

    a = config.ALPHA
    numer_a      = a*p - a*q - p - q
    discriminant = numer_a**2 + 4*a*(p - q + a - a*p - a*q)

    if discriminant < 0 or a == 0:
        return pd.Series({
            "p": p, "q": q, "ro": ro, "n_samples": n,
            "w_star": None, "G": None, "DKL": None,
        })

    w_star = (numer_a + math.sqrt(discriminant)) / (2.0 * a)
    w_star = float(np.clip(w_star, 0.01, 0.99))

    def lg(x):
        return math.log(max(x, 1e-10), config.LOG_BASE)

    G = (p  * lg(1.0 + w_star) +
         ro * lg(1.0 + a * w_star) +
         q  * lg(1.0 - w_star))

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
        "G":         round(G, 6),
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

    # GROUND v3 (intrinsic): each candidate is scored on its own G and DKL
    # — "growth divided by risk-concentration": GROUND = G / 3 ** (k * DKL).
    # Module-level DKL_K is overridable for parameter sweeps (default 20).
    for idx in valid.index:
        G_a   = out.loc[idx, "G"]
        DKL_a = out.loc[idx, "DKL"]
        if RANKING_MODE == "G_only":
            score = G_a
        elif RANKING_MODE == "DKL_only":
            # Lower DKL is preferred → store -DKL so the existing "max" selection works.
            score = -DKL_a
        else:
            # Base-3 canon: 3-state outcome space (p, q, r), DKL in trits ∈ [0, 1].
            # Denominator is 3 ** (k · DKL) so units match the log base used for G/DKL.
            denom = 3.0 ** (DKL_K * DKL_a)
            score = G_a / denom
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
