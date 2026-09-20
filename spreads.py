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


def _parity_bull_raw(chain: pd.DataFrame) -> float:
    """Median matched-strike call IV minus put IV in the 35-65 delta band."""
    if chain.empty:
        return float("nan")
    d = chain.copy()
    d["_pc"] = d["PutCall"].astype(str).str.lower().str.strip()
    d["_iv"] = pd.to_numeric(d["ImpliedVolatility"], errors="coerce")
    delta_col = "AbsDelta" if "AbsDelta" in d else "Delta"
    d["_ad"] = pd.to_numeric(d[delta_col], errors="coerce").abs()
    d = d[
        d["_pc"].isin(["put", "call"])
        & d["_ad"].between(0.35, 0.65)
        & d["_iv"].between(0.03, 3.0, inclusive="neither")
        & (pd.to_numeric(d["AskPrice"], errors="coerce")
           > pd.to_numeric(d["BidPrice"], errors="coerce"))
    ]
    if d.empty:
        return float("nan")
    p = d.pivot_table(index="StrikePrice", columns="_pc", values="_iv", aggfunc="median")
    if "call" not in p or "put" not in p:
        return float("nan")
    gap = (p["call"] - p["put"]).dropna()
    return float(gap.median()) if not gap.empty else float("nan")


# Module-level regime filter state, set by run.py before backtest.
# REGIME_LOOKUP is a pd.Series indexed by date (sorted ascending),
# values are 'bull' or 'bear'. Canonical lagged lookup uses the latest SPY
# trading day strictly before entry_date.
# When REGIME_FILTER is True, the legacy mode switches sides with the regime.
# The 2026-09-16 canon sets REGIME_BULL_ONLY: bull regime → bull puts only;
# bear or unknown regime → cash.  REGIME_LAG_SESSIONS=1 makes the as-of
# lookup strictly earlier than entry_date, avoiding the 15:00 look-ahead.
# Slippage cost in dollars per leg, applied as a post-hoc P&L haircut
# in backtest.py — does NOT affect trade selection or filters. Selection
# always uses mid-mid pricing so different slippage levels produce
# identical trade lists with different realized P&L.
SLIPPAGE_CENTS = 0.0

REGIME_FILTER     = False
REGIME_LOOKUP     = None   # pd.Series (global) OR dict[ticker -> pd.Series] (per-ticker)
REGIME_PER_TICKER = False  # if True, REGIME_LOOKUP is dict keyed by ticker
REGIME_BULL_ONLY  = False
REGIME_LAG_SESSIONS = 0
REGIME_FAIL_CLOSED = False
REGIME_MAX_STALE_CALENDAR_DAYS = None

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

        parity = _parity_bull_raw(grp)
        if bp is not None:
            bp["parity_bull_raw"] = parity
        if bc is not None:
            bc["parity_bull_raw"] = parity

        if bp is not None:
            candidates.append(bp)
        if bc is not None:
            candidates.append(bc)

    if not candidates:
        return pd.DataFrame()

    return pd.DataFrame(candidates)


def is_liquid_row(row: pd.Series) -> bool:
    """Return whether one option leg passes the active liquidity policy.

    Historical inputs use the OI floor.  Live IBKR snapshots may report zero
    OI intraday, so live mode can explicitly opt into a conservative fallback
    based on current-day volume and displayed BBO size.  Missing fields never
    pass the fallback.
    """
    # LIVE turns this off (2026-09-20, user: "i dont want >100 OI for live
    # production, thats only for backtest and oot"). IBKR reports OpenInterest
    # as 0 on 100% of live rows -- measured on Friday 09-18 market-hours
    # snapshots as well as frozen ones -- so an OI floor can only ever reject.
    # NB: lowering MIN_OPEN_INTEREST to 0 instead would be wrong: `oi >= 0` is
    # true for every row, which returns True here and bypasses the volume/BBO
    # test entirely, disabling the whole gate.
    if getattr(config, "USE_OPEN_INTEREST_LIQUIDITY", True):
        min_oi = int(getattr(config, "MIN_OPEN_INTEREST", 0))
        try:
            oi = float(row.get("OpenInterest", float("nan")))
        except (TypeError, ValueError):
            oi = float("nan")
        if np.isfinite(oi) and oi >= min_oi:
            return True

    if not getattr(config, "ALLOW_VOLUME_LIQUIDITY_FALLBACK", False):
        return False
    try:
        volume = float(row.get("Volume", float("nan")))
        bid_size = float(row.get("BidSize", float("nan")))
        ask_size = float(row.get("AskSize", float("nan")))
        return (
            np.isfinite(volume)
            and np.isfinite(bid_size)
            and np.isfinite(ask_size)
            and volume >= float(getattr(config, "MIN_LIQUIDITY_VOLUME", 0))
            and min(bid_size, ask_size) >= float(getattr(config, "MIN_LIQUIDITY_BBO_SIZE", 1))
        )
    except (TypeError, ValueError):
        return False


def _build_spread(opts: pd.DataFrame, ticker: str, entry_date,
                  expiry_date, spread_type: str) -> dict:
    if opts.empty:
        return None

    # Regime filter.  Canonical bull-only mode takes bull puts in a bull regime
    # and cash otherwise.  Legacy mode switches between puts and calls.
    # Global mode reads SPY/OEF; per-ticker mode reads each ticker's own
    # Monday-sampled price vs. its own rolling SMA.
    if REGIME_FILTER:
        if REGIME_PER_TICKER:
            regime_series = REGIME_LOOKUP.get(ticker) if REGIME_LOOKUP is not None else None
        else:
            regime_series = REGIME_LOOKUP
        regime = None
        if regime_series is not None and len(regime_series) > 0:
            ed  = pd.Timestamp(entry_date)
            side = "left" if REGIME_LAG_SESSIONS else "right"
            idx = regime_series.index.searchsorted(ed, side=side) - 1
            if idx >= 0:
                regime_date = pd.Timestamp(regime_series.index[idx]).normalize()
                stale_days = (ed.normalize() - regime_date).days
                if (REGIME_MAX_STALE_CALENDAR_DAYS is None
                        or stale_days <= REGIME_MAX_STALE_CALENDAR_DAYS):
                    regime = regime_series.iloc[idx]
        if REGIME_BULL_ONLY:
            if regime != "bull" or spread_type != "bull_put":
                return None
        elif regime == "bull" and spread_type == "bear_call":
            return None
        elif regime == "bear" and spread_type == "bull_put":
            return None
        elif regime is None and REGIME_FAIL_CLOSED:
            return None

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

    # TRUE moneyness cap (2026-09-12). Delta cannot police this: vendor IV is
    # inflated far OTM, delta is derived from it, so a strike 8%+ OTM still reads
    # ~0.19 delta and the 0.20 selector picks it. Those contracts have no bid 31%
    # of the time, and the backtest then books ask/2 as premium on something that
    # was never fillable. Measured zero-bid rate by true distance OTM:
    #   <1% 1.1% | 1-2% 0.8% | 2-3% 0.7% | 3-5% 1.8% | 5-8% 7.1% | >8% 31.2%
    _max_otm = getattr(config, "MAX_SHORT_OTM_PCT", None)
    if _max_otm is not None:
        spot = float(opts["UnderlyingPrice"].iloc[0])
        if spot > 0:
            if spread_type == "bull_put":
                otm_pct = 100.0 * (spot - short_strike) / spot
            else:
                otm_pct = 100.0 * (short_strike - spot) / spot
            if otm_pct > _max_otm:
                return None

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
    if not is_liquid_row(short_row) or not is_liquid_row(long_row):
        return None

    # Selection uses LAST-based pricing (canonical 2026-05-30, clamped 2026-05-30).
    # LAST is the price of the most recent trade, but vendor EOD snapshots often
    # have LAST OUTSIDE the current BBO (LAST trade happened earlier when the
    # market was at a different level). Audit found 48.6% of picks had short
    # LAST > short ASK, inflating credit by +$0.23/share average. Clamping LAST
    # to [BID, ASK] caps the credit at the natural ceiling (short_ask − long_bid)
    # — the best realistic fill at 15:01 — and floors it at the natural debit.
    short_last_raw = float(short_row.get("LastPrice", 0) or 0)
    long_last_raw  = float(long_row.get("LastPrice", 0) or 0)
    short_bid_raw  = float(short_row["BidPrice"])
    short_ask_raw  = float(short_row["AskPrice"])
    long_bid_raw   = float(long_row["BidPrice"])
    long_ask_raw   = float(long_row["AskPrice"])
    short_mid_raw  = (short_bid_raw + short_ask_raw) / 2.0
    long_mid_raw   = (long_bid_raw  + long_ask_raw)  / 2.0
    credit_basis = getattr(config, "CREDIT_BASIS", "last_clamped")
    if credit_basis != "mid" and (short_last_raw <= 0 or long_last_raw <= 0):
        # No LAST on a leg. Under the vendor EOD basis that meant no real market,
        # so the pair was skipped. On a live basis it just means the contract has
        # not traded yet: at 09:30 74% of weeklies have no print (vs 19% at 15:45),
        # which cut the 2026-09-16 open from 94 viable pairs to 18. CREDIT_BASIS
        # "mid" prices off the two-sided quote and never consults LAST, so the gate
        # does not apply there. Backtest behaviour (last_clamped) is unchanged.
        return None
    if credit_basis == "mid":
        short_credit_basis = short_mid_raw
        long_credit_basis  = long_mid_raw
    else:
        # Clamp LAST to current BBO on each leg
        short_credit_basis = max(short_bid_raw, min(short_last_raw, short_ask_raw))
        long_credit_basis  = max(long_bid_raw,  min(long_last_raw,  long_ask_raw))
    # Expected-fill scaling (calibrated 2026-06-10 on real May-28 fills:
    # fill ≈ 0.82×mid flat across tight and wide books; width-penalty model
    # rejected — widest books filled closest to mid). 1.0 = raw basis.
    credit_scale = getattr(config, "CREDIT_SCALE", 1.0)
    net_credit = round(credit_scale * (short_credit_basis - long_credit_basis), 4)
    # Mid still computed for display fields (short_mid / long_mid) and for
    # back-compat with diagnostics that read the *_mid columns.
    short_mid = short_mid_raw
    long_mid  = long_mid_raw
    spread_width = round(abs(short_strike - long_strike), 4)
    _maxw = getattr(config, "MAX_SPREAD_WIDTH", None)
    if _maxw is not None and spread_width > _maxw + 1e-9:
        return None

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
    if credit_ratio > getattr(config, "MAX_CREDIT_RATIO", float("inf")):
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
        "short_bid":       round(float(short_row["BidPrice"]), 4),
        "short_ask":       round(float(short_row["AskPrice"]), 4),
        "long_bid":        round(float(long_row["BidPrice"]),  4),
        "long_ask":        round(float(long_row["AskPrice"]),  4),
        "short_last":      round(float(short_row.get("LastPrice", 0.0) or 0.0), 4),
        "long_last":       round(float(long_row.get("LastPrice", 0.0) or 0.0), 4),
        "short_oi":        (None if pd.isna(short_row.get("OpenInterest")) else int(short_row["OpenInterest"])),
        "long_oi":         (None if pd.isna(long_row.get("OpenInterest"))  else int(long_row["OpenInterest"])),
        "short_volume":    (None if "Volume" not in short_row or pd.isna(short_row.get("Volume")) else int(short_row["Volume"])),
        "long_volume":     (None if "Volume" not in long_row  or pd.isna(long_row.get("Volume"))  else int(long_row["Volume"])),
        # Top-of-book sizes (live snapshots only; vendor EOD data lacks them).
        "short_bid_size":  (None if "BidSize" not in short_row or pd.isna(short_row.get("BidSize")) else int(short_row["BidSize"])),
        "short_ask_size":  (None if "AskSize" not in short_row or pd.isna(short_row.get("AskSize")) else int(short_row["AskSize"])),
        "long_bid_size":   (None if "BidSize" not in long_row  or pd.isna(long_row.get("BidSize"))  else int(long_row["BidSize"])),
        "long_ask_size":   (None if "AskSize" not in long_row  or pd.isna(long_row.get("AskSize"))  else int(long_row["AskSize"])),
        "net_credit":      net_credit,
        "spread_width":    spread_width,
        "max_loss":        max_loss,
        # IB contract ids (live snapshots only; vendor EOD data lacks them).
        # Let the ranker build a BAG and quote the spread off the complex-order
        # book rather than differencing two leg mids.
        "short_conid":     (None if "conId" not in short_row or pd.isna(short_row.get("conId")) else int(short_row["conId"])),
        "long_conid":      (None if "conId" not in long_row  or pd.isna(long_row.get("conId"))  else int(long_row["conId"])),
        "IV":                  round(short_row["ImpliedVolatility"], 4),
        "long_IV":             round(long_row["ImpliedVolatility"], 4),
        "short_theta":         round(short_theta, 4),
        "long_theta":          round(long_theta, 4),
        "theta_credit_ratio":  round(theta_credit_ratio, 4),
        "DTE":                 dte,
        # Pre-merged on the input df by score_year; None if not present.
        "iv_rank_bucket":      (None if 'iv_rank_bucket' not in short_row
                                or pd.isna(short_row.get('iv_rank_bucket'))
                                else int(short_row['iv_rank_bucket'])),
        "rv_30d":              (None if 'rv_30d' not in short_row
                                or pd.isna(short_row.get('rv_30d'))
                                else float(short_row['rv_30d'])),
    }


def settle_pnl(spot: float, sp: float, bp: float,
               net_credit: float, max_loss: float,
               spread_type: str) -> float:
    """Canon SETTLEMENT payoff per share = calc_pnl + the partial-WIN haircut.

    The canon books a partial win at HALF its theoretical intrinsic. That lives
    in research/bear_regime_sweep.py (which writes both published payloads), in
    ~10 backtest/sweep scripts, and in live/snapshot_picks.py -- but History,
    Actuals and the 15:45 freeze were paying partial wins IN FULL, so the same
    trade read richer on those surfaces than in the books it is measured
    against. One helper so the two cannot drift apart again.

    The haircut is ONE-SIDED in canon: partial losses are untouched.

    Zone predicate is canon's, not calc_outcome's -- calc_outcome returns a
    FRACTION in the partial zone, never 0, so it cannot be tested for equality.

    Use this only at expiry. An open position's mark-to-market is credit minus
    the cost to close and must not be haircut.
    """
    pnl = calc_pnl(spot, sp, bp, net_credit, max_loss, spread_type)
    if spread_type == "bull_put":
        partial = (spot <= sp) and (spot > bp)
    else:
        partial = (spot >= sp) and (spot < bp)
    if partial and pnl > 0:
        pnl *= 0.5
    return pnl


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


def calc_pnl(spot: float, sp: float, bp: float,
             net_credit: float, max_loss: float,
             spread_type: str) -> float:
    """True piecewise-linear bull-put / bear-call spread payoff at expiry.

    Replaces the old outcome-scaled formula `pnl = max_loss * outcome` /
    `pnl = credit * outcome`, which assumed breakeven sits at the midpoint
    between strikes. That's only correct for symmetric fills (credit =
    width/2). With actual fills at 75%+ of mid, credit > width/2 and the
    real breakeven is at `sp - credit` (bull-put) or `sp + credit`
    (bear-call) — the model was misreporting partial-zone P&L.

    True payoff:
      bull-put:  pnl(spot) = clamp(credit - max(sp - spot, 0), -max_loss, credit)
      bear-call: pnl(spot) = clamp(credit - max(spot - sp, 0), -max_loss, credit)
    """
    if spread_type == "bull_put":
        if spot >= sp:
            return float(net_credit)
        if spot <= bp:
            return -float(max_loss)
        return round(float(net_credit) - (float(sp) - float(spot)), 4)
    else:  # bear_call
        if spot <= sp:
            return float(net_credit)
        if spot >= bp:
            return -float(max_loss)
        return round(float(net_credit) - (float(spot) - float(sp)), 4)


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
