"""
spreads.py
For each (ticker, entry_date), build two credit spread candidates:
  - bull_put:  sell ~50-delta put,  buy one strike below
  - bear_call: sell ~50-delta call, buy one strike above

Returns a DataFrame of candidates ready for GROUND scoring.
"""

import numpy as np
import pandas as pd
import config


def build_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build bull put and bear call spread candidates for all
    (ticker, entry_date) combinations in df.

    Parameters
    ----------
    df : filtered options DataFrame from data_loader

    Returns
    -------
    DataFrame with one row per spread candidate
    """
    candidates = []

    groups = df.groupby(["Symbol", "DataDate", "ExpirationDate"])

    for (ticker, entry_date, expiry_date), grp in groups:
        puts  = grp[grp["PutCall"] == "put"].copy()
        calls = grp[grp["PutCall"] == "call"].copy()

        bp = _build_spread(puts,  ticker, entry_date, expiry_date, "bull_put")
        bc = _build_spread(calls, ticker, entry_date, expiry_date, "bear_call")

        if bp is not None:
            candidates.append(bp)
        if bc is not None:
            candidates.append(bc)

    if not candidates:
        return pd.DataFrame()

    return pd.DataFrame(candidates)


def _build_spread(opts: pd.DataFrame, ticker: str, entry_date,
                  expiry_date, spread_type: str) -> dict | None:
    """
    Select short strike (~50 delta) and adjacent long strike.
    Returns a dict with all spread details, or None if not buildable.
    """
    if opts.empty:
        return None

    opts = opts.sort_values("StrikePrice").reset_index(drop=True)
    strikes = opts["StrikePrice"].values

    # Find short strike: closest AbsDelta to 0.50, within eligible range
    eligible = opts[
        opts["AbsDelta"].between(config.DELTA_MIN, config.DELTA_MAX)
    ].copy()

    if eligible.empty:
        return None

    eligible["dist"] = (eligible["AbsDelta"] - config.DELTA_TARGET).abs()
    short_row = eligible.loc[eligible["dist"].idxmin()]
    short_strike = short_row["StrikePrice"]

    # Find index of short strike in the sorted strikes array
    short_idx_arr = np.where(strikes == short_strike)[0]
    if len(short_idx_arr) == 0:
        return None
    short_idx = short_idx_arr[0]

    # Long strike: one step below for puts, one step above for calls
    if spread_type == "bull_put":
        if short_idx == 0:
            return None
        long_strike = strikes[short_idx - 1]
    else:  # bear_call
        if short_idx >= len(strikes) - 1:
            return None
        long_strike = strikes[short_idx + 1]

    long_rows = opts[opts["StrikePrice"] == long_strike]
    if long_rows.empty:
        return None
    long_row = long_rows.iloc[0]

    # Premium calculations
    short_mid = short_row["MidPrice"]
    long_mid  = long_row["MidPrice"]

    # Net credit = premium collected (should be positive for credit spreads)
    net_credit   = round(short_mid - long_mid, 4)
    spread_width = round(abs(short_strike - long_strike), 4)
    max_loss     = round(spread_width - net_credit, 4)

    # Skip if net credit is zero or negative (would be a debit spread)
    if net_credit <= 0:
        return None

    # Skip if max loss is negative (data anomaly)
    if max_loss <= 0:
        return None

    return {
        "ticker":          ticker,
        "entry_date":      entry_date,
        "expiry_date":     expiry_date,
        "spread_type":     spread_type,
        "entry_price":     short_row["UnderlyingPrice"],
        "short_strike":    short_strike,
        "long_strike":     long_strike,
        "short_delta":     round(short_row["AbsDelta"], 4),
        "short_mid":       round(short_mid, 4),
        "long_mid":        round(long_mid, 4),
        "net_credit":      net_credit,
        "spread_width":    spread_width,
        "max_loss":        max_loss,
        "IV":              round(short_row["ImpliedVolatility"], 4),
        "DTE":             int(short_row["DTE"]),
    }


def calc_outcome(ep: float, sp: float, bp: float,
                 spread_type: str) -> float:
    """
    Compute trade outcome using equation 26 from Mercurio et al. (2020).

    Returns a value in [-1, +1]:
      +1.0 = full win  (collected full premium)
      -1.0 = full loss (lost max loss)
       0.x = partial   (price expired in the spread zone)

    Parameters
    ----------
    ep : expiration price of underlying
    sp : short strike (sold option)
    bp : long strike  (bought option)
    spread_type : 'bull_put' or 'bear_call'
    """
    mp = (sp + bp) / 2.0   # midpoint of spread

    if spread_type == "bull_put":
        # Win if price stays ABOVE short put strike
        if ep > sp:
            return 1.0
        elif ep <= bp:
            return -1.0
        else:
            return (ep - mp) / (sp - mp)

    else:  # bear_call
        # Win if price stays BELOW short call strike
        if ep < sp:
            return 1.0
        elif ep >= bp:
            return -1.0
        else:
            return (ep - mp) / (bp - mp)


def calc_pnl(outcome: float, net_credit: float, max_loss: float) -> float:
    """
    Dollar P&L per contract (per $1 of spread width).

    Full win:  collect net_credit
    Full loss: lose max_loss
    Partial:   proportional between the two
    """
    if outcome == 1.0:
        return net_credit
    elif outcome == -1.0:
        return -max_loss
    elif outcome > 0:
        return round(net_credit * outcome, 4)
    else:
        return round(max_loss * outcome, 4)
