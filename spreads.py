"""
spreads.py
For each (ticker, entry_date), build two credit spread candidates:
  - bull_put:  sell ~50-delta put,  buy one strike below
  - bear_call: sell ~50-delta call, buy one strike above

PATCH (2026-05-06): exposes long_delta on each candidate so the
Greek-based (p, q, ro) estimator can read it directly.
"""

import os

import numpy as np
import pandas as pd
import config


# Module-level regime filter state, set by run.py before backtest.
# REGIME_LOOKUP is a pd.Series indexed by date (sorted ascending),
# values are 'bull' or 'bear'. Lookup uses as-of matching (latest
# SPY trading day ≤ entry_date) so option entries on market holidays
# still get classified correctly using the prior trading day.
# When REGIME_FILTER is True:
#   bull regime → only bull_put allowed (bear_call rejected)
#   bear regime → only bear_call allowed (bull_put rejected)
# Dates before any SPY data → fail open (allow both).
# Slippage cost in dollars per leg, applied as a post-hoc P&L haircut
# in backtest.py — does NOT affect trade selection or filters. Selection
# always uses mid-mid pricing so different slippage levels produce
# identical trade lists with different realized P&L.
SLIPPAGE_CENTS = 0.0

REGIME_FILTER     = False
REGIME_LOOKUP     = None   # pd.Series (global) OR dict[ticker -> pd.Series] (per-ticker)
REGIME_PER_TICKER = False  # if True, REGIME_LOOKUP is dict keyed by ticker

# Earnings filter: reject any spread whose holding window (entry_date, expiry_date]
# contains an earnings announcement for the ticker.
EARNINGS_FILTER = False
EARNINGS_LOOKUP = {}       # dict[ticker -> sorted list of pd.Timestamp]

# SPY Friday→Monday gap filter: reject any candidate whose entry_date has
# a SPY overnight gap_pct < threshold (default -1%). Big down-gaps were a
# clear cliff in the bucket analysis (130 trades at avg -$6.61).
GAP_FILTER     = False
GAP_LOOKUP     = {}      # dict[pd.Timestamp -> gap_pct]
GAP_THRESHOLD  = -0.01   # default cliff threshold

# Low-VIX bull_put filter: skip bull_puts when VIX < threshold on entry_date.
# Bucket analysis showed bull_puts in VIX<15 produce ~$3 avg (n=295) — slow bleed.
LOW_VIX_BULLPUT_FILTER = False
VIX_LOOKUP             = {}     # dict[pd.Timestamp -> vix_close]
LOW_VIX_THRESHOLD      = 15.0

# Holiday filter: reject any spread whose holding window (entry_date, expiry_date]
# contains an NYSE market holiday. Short trading weeks have less theta capture
# and bigger gap risk going into the long weekend.
HOLIDAY_FILTER = False
# NYSE full-close holidays 2020–2026 (excludes early-close half-days like
# day-after-Thanksgiving and Christmas Eve, which still have full data).
NYSE_HOLIDAYS = pd.DatetimeIndex(sorted([
    # 2020
    "2020-01-01","2020-01-20","2020-02-17","2020-04-10","2020-05-25",
    "2020-07-03","2020-09-07","2020-11-26","2020-12-25",
    # 2021
    "2021-01-01","2021-01-18","2021-02-15","2021-04-02","2021-05-31",
    "2021-07-05","2021-09-06","2021-11-25","2021-12-24",
    # 2022
    "2022-01-17","2022-02-21","2022-04-15","2022-05-30","2022-06-20",
    "2022-07-04","2022-09-05","2022-11-24","2022-12-26",
    # 2023
    "2023-01-02","2023-01-16","2023-02-20","2023-04-07","2023-05-29",
    "2023-06-19","2023-07-04","2023-09-04","2023-11-23","2023-12-25",
    # 2024
    "2024-01-01","2024-01-15","2024-02-19","2024-03-29","2024-05-27",
    "2024-06-19","2024-07-04","2024-09-02","2024-11-28","2024-12-25",
    # 2025
    "2025-01-01","2025-01-09","2025-01-20","2025-02-17","2025-04-18",
    "2025-05-26","2025-06-19","2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    # 2026
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25",
    "2026-06-19","2026-07-03","2026-09-07","2026-11-26","2026-12-25",
]))


def build_candidates(df: pd.DataFrame) -> pd.DataFrame:
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
                  expiry_date, spread_type: str) -> dict:
    if opts.empty:
        return None

    # Regime filter: in bull regime (price > SMA), only allow bull_put.
    # In bear regime (price < SMA), only allow bear_call.
    # Global mode reads SPY/OEF; per-ticker mode reads each ticker's own
    # Monday-sampled price vs. its own rolling SMA.
    if REGIME_FILTER and REGIME_LOOKUP is not None:
        if REGIME_PER_TICKER:
            regime_series = REGIME_LOOKUP.get(ticker)
        else:
            regime_series = REGIME_LOOKUP
        if regime_series is not None and len(regime_series) > 0:
            ed  = pd.Timestamp(entry_date)
            idx = regime_series.index.searchsorted(ed, side="right") - 1
            if idx >= 0:
                regime = regime_series.iloc[idx]
                if regime == "bull" and spread_type == "bear_call":
                    return None
                if regime == "bear" and spread_type == "bull_put":
                    return None
            # idx < 0 → entry_date predates regime data → fail open
        # ticker missing from per-ticker dict → fail open

    # SPY gap filter: reject any candidate when SPY's overnight gap_pct
    # on entry_date is below threshold (e.g., -1% = big down-gap).
    if GAP_FILTER and GAP_LOOKUP:
        gap = GAP_LOOKUP.get(pd.Timestamp(entry_date))
        if gap is not None and gap < GAP_THRESHOLD:
            return None

    # Low-VIX bull_put filter: bull_puts in low-vol regimes are weak per
    # the bucket analysis. Reject bull_put when VIX < threshold on entry.
    if LOW_VIX_BULLPUT_FILTER and spread_type == "bull_put" and VIX_LOOKUP:
        vix = VIX_LOOKUP.get(pd.Timestamp(entry_date))
        if vix is not None and vix < LOW_VIX_THRESHOLD:
            return None

    # Holiday filter: reject if a NYSE full-close holiday falls within the
    # holding window. Short trading weeks have less theta capture and the
    # market can gap on either side of the long weekend.
    if HOLIDAY_FILTER:
        entry_ts  = pd.Timestamp(entry_date)
        expiry_ts = pd.Timestamp(expiry_date)
        lo = NYSE_HOLIDAYS.searchsorted(entry_ts,  side="right")
        hi = NYSE_HOLIDAYS.searchsorted(expiry_ts, side="right")
        if hi > lo:
            return None

    # Earnings filter: reject if an earnings date falls within the holding
    # window (entry_date, expiry_date]. Earnings cause overnight gaps that
    # can wreck close-to-the-money spreads.
    if EARNINGS_FILTER and EARNINGS_LOOKUP:
        ed_list = EARNINGS_LOOKUP.get(ticker)
        if ed_list is not None and len(ed_list) > 0:
            entry_ts  = pd.Timestamp(entry_date)
            expiry_ts = pd.Timestamp(expiry_date)
            # Binary search for any earnings date in (entry, expiry]
            lo = ed_list.searchsorted(entry_ts,  side="right")
            hi = ed_list.searchsorted(expiry_ts, side="right")
            if hi > lo:
                return None

    opts = opts.sort_values("StrikePrice").reset_index(drop=True)
    all_strikes = opts["StrikePrice"].values

    eligible = opts[
        opts["AbsDelta"].between(config.DELTA_MIN, config.DELTA_MAX)
    ].copy()

    if eligible.empty:
        return None

    eligible["dist"] = (eligible["AbsDelta"] - config.DELTA_TARGET).abs()
    short_row = eligible.loc[eligible["dist"].idxmin()]
    short_strike = short_row["StrikePrice"]

    short_idx_arr = np.where(all_strikes == short_strike)[0]
    if len(short_idx_arr) == 0:
        return None
    short_idx = short_idx_arr[0]

    if spread_type == "bull_put":
        if short_idx == 0:
            return None
        long_strike = all_strikes[short_idx - 1]
    else:  # bear_call
        if short_idx >= len(all_strikes) - 1:
            return None
        long_strike = all_strikes[short_idx + 1]

    long_rows = opts[opts["StrikePrice"] == long_strike]
    if long_rows.empty:
        return None
    long_row = long_rows.iloc[0]

    # Liquidity gate at candidate-construction time, applied to both legs.
    # Preprocess already filtered short-leg OI but kept neighboring strikes
    # regardless; this re-checks both legs against the current config so
    # bumping MIN_OPEN_INTEREST doesn't require a parquet rebuild.
    min_oi = getattr(config, "MIN_OPEN_INTEREST", 0)
    if short_row["OpenInterest"] < min_oi or long_row["OpenInterest"] < min_oi:
        return None

    # Selection always uses mid-mid pricing. Slippage is applied as a
    # post-hoc P&L haircut in backtest.py, so it does not contaminate
    # filters (credit_ratio, theta_credit_ratio, etc.) or trade selection.
    short_mid  = (short_row["BidPrice"] + short_row["AskPrice"]) / 2.0
    long_mid   = (long_row["BidPrice"]  + long_row["AskPrice"])  / 2.0
    net_credit = round(short_mid - long_mid, 4)
    spread_width = round(abs(short_strike - long_strike), 4)
    max_loss     = round(spread_width - net_credit, 4)

    if net_credit <= 0 or max_loss <= 0:
        return None

    # Filter: reject spreads with poor credit-to-risk ratio (too little
    # premium for the risk taken — typically wide-strike-interval underlyings)
    # OR with absurdly high credit ratio (deep-ITM short legs, stale quotes,
    # tiny spread widths where max_loss → 0 makes the math explode).
    credit_ratio = net_credit / max_loss
    if credit_ratio < config.MIN_CREDIT_RATIO:
        return None
    if credit_ratio >= getattr(config, "MAX_CREDIT_RATIO", float("inf")):
        return None

    # Filter: reject spreads where max_loss exceeds cap (large dollar risk
    # per contract, typically wide-strike spreads on expensive underlyings).
    if max_loss > getattr(config, "MAX_MAX_LOSS", float("inf")):
        return None

    # Theta-to-credit ratio: how much of the credit is earned via expected
    # theta decay vs. dependent on IV crush / favorable underlying moves.
    # Theta in the data is signed (negative for both long-call and long-put
    # positions); for a credit spread the seller's per-day theta gain is
    # |theta_short| - |theta_long| = theta_long - theta_short (both negative).
    short_theta = float(short_row["Theta"])
    long_theta  = float(long_row["Theta"])
    net_theta_per_day = long_theta - short_theta   # positive = good for seller
    dte = int(short_row["DTE"])
    if net_credit > 0:
        theta_credit_ratio = (net_theta_per_day * dte) / net_credit
    else:
        theta_credit_ratio = 0.0

    min_tcr = getattr(config, "MIN_THETA_CREDIT_RATIO", 0.0)
    if theta_credit_ratio < min_tcr:
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
        "long_delta":      round(long_row["AbsDelta"], 4),
        "short_mid":       round(short_mid, 4),
        "long_mid":        round(long_mid, 4),
        "net_credit":      net_credit,
        "spread_width":    spread_width,
        "max_loss":        max_loss,
        "IV":                  round(short_row["ImpliedVolatility"], 4),
        "long_IV":             round(long_row["ImpliedVolatility"], 4),
        "short_theta":         round(short_theta, 4),
        "long_theta":          round(long_theta, 4),
        "theta_credit_ratio":  round(theta_credit_ratio, 4),
        "DTE":                 dte,
    }


def calc_outcome(ep: float, sp: float, bp: float,
                 spread_type: str) -> float:
    mp = (sp + bp) / 2.0

    if spread_type == "bull_put":
        if ep > sp:
            return 1.0
        elif ep <= bp:
            return -1.0
        else:
            return (ep - mp) / (sp - mp)
    else:  # bear_call
        if ep < sp:
            return 1.0
        elif ep >= bp:
            return -1.0
        else:
            return (ep - mp) / (bp - mp)


def calc_pnl(outcome: float, net_credit: float, max_loss: float) -> float:
    if outcome == 1.0:
        return net_credit
    elif outcome == -1.0:
        return -max_loss
    elif outcome > 0:
        return round(net_credit * outcome, 4)
    else:
        return round(max_loss * outcome, 4)


def build_regime_lookup(csv_path: str, sma_window: int = 50):
    """
    Build a sorted pd.Series of regime classifications indexed by date,
    derived from daily benchmark close prices vs trailing SMA.

    Parameters
    ----------
    csv_path   : path to a Stooq/Yahoo-format CSV with Date,Close columns
    sma_window : trailing SMA window in trading days, default 50

    Returns
    -------
    pd.Series indexed by ascending date, values 'bull' or 'bear'.
    Empty Series if the file is missing or unreadable.

    The Series index is sorted, supporting as-of (searchsorted) lookups
    so option entries on market holidays match the prior trading day.
    """
    import os
    if not os.path.exists(csv_path):
        print(f"[regime] benchmark file not found: {csv_path}")
        return pd.Series([], dtype=object)

    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"[regime] failed to read {csv_path}: {e}")
        return pd.Series([], dtype=object)

    df.columns = [c.strip() for c in df.columns]
    if "Date" not in df.columns or "Close" not in df.columns:
        print(f"[regime] {csv_path} missing Date/Close columns")
        return pd.Series([], dtype=object)

    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["SMA"]  = df["Close"].rolling(window=sma_window, min_periods=sma_window).mean()

    valid = df.dropna(subset=["SMA"])
    series = pd.Series(
        np.where(valid["Close"] > valid["SMA"], "bull", "bear"),
        index=pd.DatetimeIndex(valid["Date"]),
        dtype=object,
    ).sort_index()

    n_bull = int((series == "bull").sum())
    n_bear = int((series == "bear").sum())
    print(f"[regime] built {len(series):,} day classifications "
          f"({n_bull:,} bull, {n_bear:,} bear) from {csv_path}")
    return series


def load_spy_gap_lookup(csv_path: str) -> dict:
    """
    Build {pd.Timestamp -> gap_pct} from a daily SPY CSV (Date,Open,Close,...).
    gap_pct = (Open - prev_Close) / prev_Close.
    """
    if not os.path.exists(csv_path):
        print(f"[gap] SPY CSV not found: {csv_path}")
        return {}
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    if "Open" not in df.columns or "Close" not in df.columns or "Date" not in df.columns:
        print(f"[gap] {csv_path} missing Date/Open/Close columns")
        return {}
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["prev_close"] = df["Close"].shift(1)
    df["gap_pct"]    = (df["Open"] - df["prev_close"]) / df["prev_close"]
    valid = df.dropna(subset=["gap_pct"])
    lookup = {pd.Timestamp(r["Date"]): float(r["gap_pct"])
              for _, r in valid.iterrows()}
    print(f"[gap] loaded {len(lookup):,} SPY gap_pct values from {csv_path}")
    return lookup


def load_vix_lookup(parquet_or_csv_path: str) -> dict:
    """
    Build {pd.Timestamp -> vix_close} from a VIX history file (parquet or CSV).
    """
    if not os.path.exists(parquet_or_csv_path):
        print(f"[vix] file not found: {parquet_or_csv_path}")
        return {}
    if parquet_or_csv_path.endswith(".parquet"):
        df = pd.read_parquet(parquet_or_csv_path)
    else:
        df = pd.read_csv(parquet_or_csv_path)
    df.columns = [c.strip().capitalize() if c.lower() != "date" else "Date"
                  for c in df.columns]
    if "Close" not in df.columns or "Date" not in df.columns:
        print(f"[vix] {parquet_or_csv_path} missing Date/Close columns")
        return {}
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    lookup = {pd.Timestamp(r["Date"]): float(r["Close"])
              for _, r in df.iterrows()}
    print(f"[vix] loaded {len(lookup):,} VIX close values from "
          f"{parquet_or_csv_path}")
    return lookup


def load_earnings_lookup(csv_path: str) -> dict:
    """
    Load earnings-date CSV (cols: Symbol, EarningsDate) into a dict
    keyed by ticker, with values as sorted pd.DatetimeIndex for fast
    binary-search lookup.
    """
    if not os.path.exists(csv_path):
        print(f"[earnings] file not found: {csv_path}")
        return {}
    df = pd.read_csv(csv_path)
    if "Symbol" not in df.columns or "EarningsDate" not in df.columns:
        print(f"[earnings] {csv_path} missing required columns")
        return {}
    df["EarningsDate"] = pd.to_datetime(df["EarningsDate"])
    lookup = {}
    for sym, grp in df.groupby("Symbol"):
        lookup[sym] = pd.DatetimeIndex(sorted(grp["EarningsDate"].unique()))
    print(f"[earnings] loaded {len(df):,} announcements for "
          f"{len(lookup):,} tickers from {csv_path}")
    return lookup


def build_per_ticker_regime_lookup(df_options: pd.DataFrame,
                                   sma_window_days: int = 100) -> dict:
    """
    Build per-ticker regime lookup from Monday-sampled UnderlyingPrice in
    the options parquet. SMA window is given in trading days for symmetry
    with the global mode; converted to weekly samples (≈ days / 5).

    Returns dict[ticker -> pd.Series], each Series indexed by DataDate
    (sorted), values 'bull' or 'bear'. Missing tickers fail open.
    """
    sma_weeks = max(1, round(sma_window_days / 5))
    uniq = (df_options[["Symbol", "DataDate", "UnderlyingPrice"]]
            .dropna()
            .drop_duplicates(["Symbol", "DataDate"]))

    lookup, total_b, total_r = {}, 0, 0
    for sym, grp in uniq.groupby("Symbol"):
        grp = grp.sort_values("DataDate").set_index("DataDate")
        grp["SMA"] = grp["UnderlyingPrice"].rolling(
            window=sma_weeks, min_periods=sma_weeks
        ).mean()
        valid = grp.dropna(subset=["SMA"])
        if valid.empty:
            continue
        s = pd.Series(
            np.where(valid["UnderlyingPrice"] > valid["SMA"], "bull", "bear"),
            index=pd.DatetimeIndex(valid.index),
            dtype=object,
        ).sort_index()
        lookup[sym] = s
        total_b += int((s == "bull").sum())
        total_r += int((s == "bear").sum())

    print(f"[regime] built per-ticker classifications for {len(lookup):,} "
          f"tickers ({total_b:,} bull, {total_r:,} bear weeks; "
          f"SMA={sma_weeks}-week ≈ {sma_window_days}-day)")
    return lookup
