"""
historical_probs.py
Closed-form (p, q, ro) estimator from Black-Scholes deltas.

For a credit spread:
    p   = 1 - delta_short    (short expires OTM, full credit kept)
    q   =     delta_long     (underlying past long strike, full loss)
    ro  = delta_short - delta_long  (price between strikes, partial)

Delta is interpreted as the risk-neutral probability of finishing ITM.
This is the market-implied forward distribution — no historical
calibration, no rolling window, no bucket boundaries.

PATCH (2026-05-06): Replaces per-ticker empirical lookback with
closed-form Greek-based estimator. The build_historical_outcomes
function is retained for compatibility with run.py but is no longer
required by empirical_probs_from_deltas.
"""

import numpy as np
import pandas as pd

import config
import spreads


def build_historical_outcomes(df_options: pd.DataFrame,
                              ep_lookup: dict) -> pd.DataFrame:
    """
    Retained for backwards compatibility / diagnostic use.
    The Greek-based empirical_probs_from_deltas() does not consume this.
    """
    print("Building historical outcomes universe (diagnostic only)...")
    candidates = spreads.build_candidates(df_options)
    if candidates.empty:
        return pd.DataFrame()

    candidates = candidates.rename(columns={"ticker": "Symbol"})
    candidates["entry_date"]  = pd.to_datetime(candidates["entry_date"])
    candidates["expiry_date"] = pd.to_datetime(candidates["expiry_date"])

    def _outcome(row):
        ep = ep_lookup.get((row["Symbol"], row["expiry_date"]), np.nan)
        if pd.isna(ep):
            return np.nan
        return spreads.calc_outcome(ep, row["short_strike"],
                                    row["long_strike"], row["spread_type"])

    candidates["outcome"] = candidates.apply(_outcome, axis=1)
    candidates = candidates.dropna(subset=["outcome"])

    print(f"Historical universe: {len(candidates):,} resolved spread-outcomes")
    print(f"  Unique tickers:    {candidates['Symbol'].nunique()}")
    print(f"  Date range:        {candidates['entry_date'].min().date()} "
          f"to {candidates['entry_date'].max().date()}")

    return candidates[["Symbol", "entry_date", "expiry_date", "spread_type",
                       "short_strike", "long_strike", "outcome"]]


def empirical_probs_from_deltas(short_delta: float,
                                long_delta: float) -> tuple:
    """
    Greek-based (p, q, ro) estimator.

        p  = 1 - delta_short
        q  =     delta_long
        ro = delta_short - delta_long

    Both deltas should be passed as absolute values in [0, 1].

    Returns (p, q, ro, n_samples) where n_samples is None
    (no historical sample is involved).
    """
    sd = float(short_delta)
    ld = float(long_delta)

    # Long delta should be smaller than short delta for a credit spread.
    # If not (data anomaly), clip ro to zero.
    if ld > sd:
        ld = sd

    p  = 1.0 - sd
    q  = ld
    ro = max(0.0, sd - ld)

    # Renormalize for any floating-point drift
    s = p + q + ro
    if s > 0:
        p, q, ro = p/s, q/s, ro/s

    return round(p, 4), round(q, 4), round(ro, 4), None


# ─────────────────────────────────────────────────────────────────────────────
# DRIFT-ADJUSTED PROBABILITIES
# ─────────────────────────────────────────────────────────────────────────────

from math import erf, sqrt, log


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using erf — no scipy dependency."""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def build_drift_table(df_options: pd.DataFrame,
                      window_days: int = 60) -> dict:
    """
    Build per-(ticker, date) annualized realized log-drift table.

    The input parquet is Monday-only sampled (per preprocess.ENTRY_DOW),
    so successive rows per ticker are weekly. We compute weekly log
    returns and annualize with ×52 (NOT ×252).

        μ_annual = mean(weekly_log_returns over last N weeks) × 52

    `window_days` is interpreted as a window expressed in trading days,
    converted to weeks via /5 for the rolling sample window.

    Returns dict: {(Symbol, Timestamp): drift_annualized_float}
    """
    sma_weeks = max(1, round(window_days / 5))
    print(f"Building drift table (trailing {window_days} trading days "
          f"≈ {sma_weeks} weeks of weekly log returns per ticker)...")

    spot = (
        df_options[["Symbol", "DataDate", "UnderlyingPrice"]]
        .drop_duplicates(subset=["Symbol", "DataDate"])
        .sort_values(["Symbol", "DataDate"])
        .reset_index(drop=True)
    )

    # Weekly log returns within each ticker (Monday-to-Monday)
    spot["log_ret"] = (
        spot.groupby("Symbol")["UnderlyingPrice"]
            .transform(lambda s: np.log(s / s.shift(1)))
    )

    # Trailing N-week mean × 52 = annualized drift
    spot["drift"] = (
        spot.groupby("Symbol")["log_ret"]
            .transform(lambda s: s.rolling(window=sma_weeks,
                                           min_periods=max(4, sma_weeks // 3)).mean() * 52.0)
    )

    drift_lookup = {
        (row["Symbol"], pd.Timestamp(row["DataDate"])): row["drift"]
        for _, row in spot.dropna(subset=["drift"]).iterrows()
    }

    print(f"  Drift entries: {len(drift_lookup):,} "
          f"(unique tickers: {spot['Symbol'].nunique()})")
    return drift_lookup


def build_rv_table(df_options: pd.DataFrame,
                   window_days: int = 30) -> dict:
    """
    Build per-(ticker, date) annualized realized volatility table.

    Same Monday-only input as build_drift_table. Computes std of weekly
    log returns over the trailing window, annualized with ×√52.

        σ_annual = std(weekly_log_returns over last N weeks) × √52

    Returns dict: {(Symbol, Timestamp): rv_annualized_float}
    """
    sma_weeks = max(2, round(window_days / 5))
    print(f"Building RV table (trailing {window_days} trading days "
          f"≈ {sma_weeks} weeks of weekly log-return std per ticker)...")

    spot = (
        df_options[["Symbol", "DataDate", "UnderlyingPrice"]]
        .drop_duplicates(subset=["Symbol", "DataDate"])
        .sort_values(["Symbol", "DataDate"])
        .reset_index(drop=True)
    )

    spot["log_ret"] = (
        spot.groupby("Symbol")["UnderlyingPrice"]
            .transform(lambda s: np.log(s / s.shift(1)))
    )

    sqrt52 = np.sqrt(52.0)
    spot["rv"] = (
        spot.groupby("Symbol")["log_ret"]
            .transform(lambda s: s.rolling(window=sma_weeks,
                                           min_periods=max(4, sma_weeks // 3)).std() * sqrt52)
    )

    rv_lookup = {
        (row["Symbol"], pd.Timestamp(row["DataDate"])): row["rv"]
        for _, row in spot.dropna(subset=["rv"]).iterrows()
    }

    print(f"  RV entries: {len(rv_lookup):,} "
          f"(unique tickers: {spot['Symbol'].nunique()})")
    return rv_lookup


def drift_adjusted_probs(short_strike: float,
                         long_strike:  float,
                         spot:         float,
                         iv:           float,
                         dte_days:     int,
                         drift:        float,
                         spread_type:  str) -> tuple:
    """
    Real-world (p, q, ro) for a credit spread, using ticker-specific
    annualized drift μ in place of the risk-neutral rate r.

    Standard Black-Scholes d2 with real-world drift:
        d2(K) = [ln(S/K) + (μ - 0.5σ²)T] / (σ√T)
        P(S_T > K) = N(d2(K))

    Bull-put spread:
        - short put at K_short, long put at K_long < K_short
        - WIN  if S_T >= K_short    (full credit kept)
        - LOSS if S_T <= K_long     (max loss)
        - PART otherwise

    Bear-call spread:
        - short call at K_short, long call at K_long > K_short
        - WIN  if S_T <= K_short
        - LOSS if S_T >= K_long
        - PART otherwise

    Parameters
    ----------
    short_strike : strike of the short leg
    long_strike  : strike of the long leg
    spot         : current underlying price
    iv           : implied volatility (annualized, e.g. 0.25 for 25%)
    dte_days     : days to expiry
    drift        : annualized real-world expected return μ
    spread_type  : 'bull_put' or 'bear_call'

    Returns
    -------
    (p, q, ro, None) — same signature as empirical_probs_from_deltas.
    """
    if spot <= 0 or iv <= 0 or dte_days <= 0:
        return None, None, None, None

    T  = dte_days / 365.0
    sT = iv * sqrt(T)

    def prob_above(K):
        """P(S_T > K) under real-world drift."""
        d2 = (log(spot / K) + (drift - 0.5 * iv * iv) * T) / sT
        return _norm_cdf(d2)

    p_above_short = prob_above(short_strike)
    p_above_long  = prob_above(long_strike)

    if spread_type == "bull_put":
        # WIN if S_T > K_short, LOSS if S_T < K_long, PART in between
        p  = p_above_short
        q  = 1.0 - p_above_long
        ro = max(0.0, p_above_long - p_above_short)
    elif spread_type == "bear_call":
        # WIN if S_T < K_short, LOSS if S_T > K_long, PART in between
        p  = 1.0 - p_above_short
        q  = p_above_long
        ro = max(0.0, p_above_short - p_above_long)
    else:
        return None, None, None, None

    # Renormalize for any floating-point drift
    s = p + q + ro
    if s > 0:
        p, q, ro = p / s, q / s, ro / s

    return round(p, 4), round(q, 4), round(ro, 4), None


def skew_adjusted_probs(short_delta: float,
                        long_delta:  float,
                        iv_short:    float,
                        iv_long:     float,
                        alpha:       float = 0.5) -> tuple:
    """
    Skew-adjusted (p, q, ro) for a credit spread.

    The Greek-only estimator uses p = 1 - delta_short. But delta is
    computed from the strike-specific IV, which is inflated by demand
    for OTM put protection (vol skew). When skew is steep, the market
    is overpaying for downside fear — so real p_win for premium sellers
    exceeds the delta-implied p_win.

    skew_pct = (IV_long - IV_short) / IV_short
        > 0  : far-OTM IV exceeds near-strike IV (typical put skew)
        ~ 0  : flat surface
        < 0  : inverted (rare, often before catalysts)

    Adjustment:
        p_adj = (1 - delta_short) + alpha * skew_pct

    For both bull_put and bear_call, the "long leg" is the more-OTM
    strike, so positive skew means the market is most fearful at the
    spread's outer edge. Same adjustment direction works for both.

    q and ro use the unadjusted long_delta (treating the loss boundary
    as a delta-implied probability, since the loss event is dominated by
    underlying movement, not skew).

    Returns (p, q, ro, None) — same signature as empirical_probs_from_deltas.
    """
    sd = float(short_delta)
    ld = float(long_delta)
    if ld > sd:
        ld = sd

    if iv_short is None or iv_short <= 0:
        skew_pct = 0.0
    else:
        skew_pct = (float(iv_long) - float(iv_short)) / float(iv_short)
    # Bound the adjustment so a wild skew (e.g., earnings) doesn't blow up p
    skew_pct = max(-0.5, min(0.5, skew_pct))

    p  = (1.0 - sd) + alpha * skew_pct
    p  = max(0.01, min(0.99, p))
    q  = ld
    ro = max(0.0, sd - ld)

    s = p + q + ro
    if s > 0:
        p, q, ro = p / s, q / s, ro / s

    return round(p, 4), round(q, 4), round(ro, 4), None


def vol_blended_probs(short_strike: float,
                      long_strike:  float,
                      spot:         float,
                      iv:           float,
                      rv:           float,
                      dte_days:     int,
                      drift:        float,
                      spread_type:  str,
                      iv_weight:    float = 0.5) -> tuple:
    """
    Real-world (p, q, ro) using a vol estimate that blends IV and RV.

    The motivation: IV systematically exceeds realized vol (the volatility
    risk premium). Using IV alone in d2 is conservative for premium sellers
    — it assumes a wider distribution than markets actually deliver. A
    blended σ tightens the distribution toward what historically happens.

        σ_eff = iv_weight * IV + (1 - iv_weight) * RV
        d2(K) = [ln(S/K) + (μ - 0.5σ_eff²)T] / (σ_eff √T)

    Parameters mirror drift_adjusted_probs plus:
        rv         : annualized realized vol estimate
        iv_weight  : weight on IV in the blend (default 0.5 = equal mix)

    Falls back to IV-only if rv is None/NaN/<=0.

    Returns (p, q, ro, None).
    """
    if spot <= 0 or iv <= 0 or dte_days <= 0:
        return None, None, None, None

    if rv is None or pd.isna(rv) or rv <= 0:
        sigma_eff = iv
    else:
        sigma_eff = iv_weight * iv + (1.0 - iv_weight) * rv

    T  = dte_days / 365.0
    sT = sigma_eff * sqrt(T)

    def prob_above(K):
        d2 = (log(spot / K) + (drift - 0.5 * sigma_eff * sigma_eff) * T) / sT
        return _norm_cdf(d2)

    p_above_short = prob_above(short_strike)
    p_above_long  = prob_above(long_strike)

    if spread_type == "bull_put":
        p  = p_above_short
        q  = 1.0 - p_above_long
        ro = max(0.0, p_above_long - p_above_short)
    elif spread_type == "bear_call":
        p  = 1.0 - p_above_short
        q  = p_above_long
        ro = max(0.0, p_above_short - p_above_long)
    else:
        return None, None, None, None

    s = p + q + ro
    if s > 0:
        p, q, ro = p / s, q / s, ro / s

    return round(p, 4), round(q, 4), round(ro, 4), None
