"""
ground.py
Implements GROUND (Growth Rate Over UNiform Divergence) scoring
from Mercurio, Wu & Xie (2020), equations 6, 7, 22, 25.

For each credit spread candidate:
  1. Estimate p (full win prob), q (full loss prob), ro (partial prob)
  2. Compute optimal Kelly-style bet size w* (eq 7)
  3. Compute growth rate G(w*) (eq 6)
  4. Compute relative entropy DKL vs uniform (eq 22)
  5. Compute GROUND ratio (eq 25)

The candidate with the highest GROUND score per (ticker, week) is selected.
If GROUND < threshold, no trade is taken (PASS).
"""

import math
import numpy as np
import pandas as pd
import config


def score_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Add GROUND score columns to the candidates DataFrame.

    Parameters
    ----------
    candidates : DataFrame from spreads.build_candidates()

    Returns
    -------
    Same DataFrame with added columns:
        p, q, ro, w_star, G, DKL, GROUND
    """
    if candidates.empty:
        return candidates

    results = candidates.apply(_score_row, axis=1, result_type="expand")
    return pd.concat([candidates, results], axis=1)


def _score_row(row: pd.Series) -> pd.Series:
    """Score a single spread candidate."""
    delta = row["short_delta"]

    # ── PROBABILITY ESTIMATES ─────────────────────────────────────────────
    # p  = prob full win  = (1 - delta): price stays OTM
    # q  = prob full loss = delta * LOSS_FACTOR: price goes fully ITM
    # ro = prob partial   = remainder
    p  = round(1.0 - delta, 4)
    q  = round(delta * config.LOSS_FACTOR, 4)
    ro = round(max(0.0, 1.0 - p - q), 4)

    # Validity check
    if p <= 0 or q <= 0 or p + q > 1.0:
        return pd.Series({
            "p": p, "q": q, "ro": ro,
            "w_star": None, "G": None, "DKL": None, "GROUND": None
        })

    # ── OPTIMAL BET SIZE w* (eq 7) ────────────────────────────────────────
    a = config.ALPHA
    numer_a      = a*p - a*q - p - q
    discriminant = numer_a**2 + 4*a*(p - q + a - a*p - a*q)

    if discriminant < 0 or a == 0:
        return pd.Series({
            "p": p, "q": q, "ro": ro,
            "w_star": None, "G": None, "DKL": None, "GROUND": None
        })

    w_star = (numer_a + math.sqrt(discriminant)) / (2.0 * a)
    w_star = float(np.clip(w_star, 0.01, 0.99))

    # ── GROWTH RATE G(w*) (eq 6) ─────────────────────────────────────────
    def lg(x):
        return math.log(max(x, 1e-10), config.LOG_BASE)

    G = (p  * lg(1.0 + w_star) +
         ro * lg(1.0 + a * w_star) +
         q  * lg(1.0 - w_star))

    # ── SHANNON ENTROPY of this spread's return distribution ──────────────
    def h(prob):
        return -prob * lg(prob) if prob > 0 else 0.0

    H_chosen = h(p) + h(ro) + h(q)

    # ── DKL vs uniform (3 states) (eq 22) ────────────────────────────────
    H_uniform   = lg(3.0)          # log_3(3) = 1.0
    DKL_chosen  = H_uniform - H_chosen
    DKL_uniform = 0.0              # uniform has DKL = 0

    # ── MINIMUM RISK PORTFOLIO growth rate ────────────────────────────────
    # Equal weight across all 3 outcome states → blended p = 1/3
    p_min = 1.0 / 3.0
    q_min = 1.0 / 3.0
    ro_min = 1.0 / 3.0
    G_min = (p_min  * lg(1.0 + w_star) +
             ro_min * lg(1.0 + a * w_star) +
             q_min  * lg(1.0 - w_star))

    # ── GROUND RATIO (eq 25) ──────────────────────────────────────────────
    denom = DKL_chosen - DKL_uniform
    if denom <= 0:
        ground = 0.0
    else:
        ground = (G - G_min) / denom

    return pd.Series({
        "p":      p,
        "q":      q,
        "ro":     ro,
        "w_star": round(w_star, 4),
        "G":      round(G, 6),
        "DKL":    round(DKL_chosen, 4),
        "GROUND": round(ground, 6),
    })


def select_trades(scored: pd.DataFrame) -> pd.DataFrame:
    """
    Per (ticker, entry_date): pick the higher-GROUND spread.
    If max GROUND < threshold, mark as PASS.

    Returns a DataFrame with one row per (ticker, entry_date),
    either a selected trade or a PASS.
    """
    if scored.empty:
        return pd.DataFrame()

    selected = []

    for (ticker, entry_date), grp in scored.groupby(["ticker", "entry_date"]):
        # Drop rows where GROUND couldn't be computed
        valid = grp[grp["GROUND"].notna()]
        if valid.empty:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS", "reason": "no valid GROUND score"
            })
            continue

        best = valid.loc[valid["GROUND"].idxmax()]

        if best["GROUND"] < config.GROUND_THRESHOLD:
            selected.append({
                "ticker": ticker, "entry_date": entry_date,
                "decision": "PASS",
                "reason": f"GROUND {best['GROUND']:.4f} < threshold {config.GROUND_THRESHOLD}",
                "best_ground": best["GROUND"],
            })
            continue

        row = best.to_dict()
        row["decision"] = best["spread_type"]
        selected.append(row)

    return pd.DataFrame(selected)
