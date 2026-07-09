"""Black-Scholes theoretical option pricing for MTM tracking.

Used when bid/ask/last are unreliable for illiquid OTM weeklies. BS price
is computed from underlying spot + leg's snapshot IV + days to expiry,
which gives a deterministic, model-consistent number — not a guess about
whether a wide bid-ask quote is real.

Convention: r = 0, q = 0 (short-dated equity options, dividends ignored).
"""
from __future__ import annotations
from math import erf, log, sqrt


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def bs_price(spot: float, strike: float, iv: float, dte_days: float,
             pc: str) -> float:
    """Return BS theoretical price for a put or call. r=0, q=0.

    For OTM short-dated options where bid/ask are wide-and-fake, this is
    a more reliable mark than market quotes. Always non-negative; always
    ≥ intrinsic (BS guarantees this for r=0).
    """
    if dte_days <= 0:
        # At/past expiry: only intrinsic remains.
        if pc == 'put':
            return max(0.0, strike - spot)
        return max(0.0, spot - strike)

    if iv is None or iv <= 0 or spot <= 0 or strike <= 0:
        # No vol → no time value; collapse to intrinsic. Better than NaN.
        if pc == 'put':
            return max(0.0, strike - spot)
        return max(0.0, spot - strike)

    T = dte_days / 365.0
    sT = iv * sqrt(T)
    d1 = (log(spot / strike) + 0.5 * iv * iv * T) / sT
    d2 = d1 - sT
    if pc == 'put':
        return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)


def bs_spread_debit(spot: float, short_strike: float, long_strike: float,
                    short_iv: float, long_iv: float, dte_days: float,
                    spread_type: str) -> float:
    """Theoretical close debit for a credit spread (cost to buy back).

    bull_put: long short_put + short long_put → debit = short_put − long_put
    bear_call: long short_call + short long_call → debit = short_call − long_call
    """
    pc = 'put' if spread_type == 'bull_put' else 'call'
    short_px = bs_price(spot, short_strike, short_iv, dte_days, pc)
    long_px  = bs_price(spot, long_strike,  long_iv,  dte_days, pc)
    return max(0.0, short_px - long_px)
