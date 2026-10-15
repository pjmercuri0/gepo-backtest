"""Debit-spread execution (user 2026-10-15): trade the parity twin of each canon credit spread.

A bull put credit spread (SELL K_hi put, BUY K_lo put) has the same payoff as the bull CALL debit
spread on the same strikes (BUY K_lo call, SELL K_hi call); a bear call credit spread's twin is the
bear PUT debit spread. Leg ACTIONS are unchanged -- SELL short_strike, BUY long_strike -- only the
right flips.

Everything inside the system stays in CREDIT terms. A debit quote D enters as its credit
equivalent width - D, so selection, the quote gate, marks, P&L, settlement and every book are
untouched. A debit appears only where he reads a price or types a fill.

  bag prices are NET DEBITS (combo_quotes.py). The debit bag's (bid, ask, last) maps onto the
  credit bag's scale by subtracting the width:  credit_bag = debit_bag - width.

Rows carry structure="debit" when they were quoted this way, so rows created before the switch
keep showing credit. Off unless live_config.DEBIT_MODE is True.
"""
from __future__ import annotations

from live import live_config


def enabled() -> bool:
    return bool(getattr(live_config, "DEBIT_MODE", False))


def is_debit(row) -> bool:
    try:
        return (row or {}).get("structure") == "debit"
    except AttributeError:
        return False


def credit_right(spread_type: str) -> str:
    return "P" if spread_type == "bull_put" else "C"


def parity_right(spread_type: str) -> str:
    return "C" if spread_type == "bull_put" else "P"


def right_for(row) -> str:
    """The right this row is actually traded in."""
    st = (row or {}).get("spread_type")
    return parity_right(st) if is_debit(row) else credit_right(st)


def to_credit_bag(bid, ask, last, width):
    """Debit-bag (bid, ask, last) -> the credit-bag scale. None/NaN pass through."""
    f = lambda v: None if v is None or v != v else float(v) - float(width)
    return f(bid), f(ask), f(last)


def debit(credit, width):
    """Credit-equivalent -> the debit he pays. None stays None."""
    if credit is None or width is None:
        return None
    try:
        return round(float(width) - float(credit), 4)
    except (TypeError, ValueError):
        return None


def attach_parity_legs(candidates, snap):
    """Scan side: swap each candidate's conIds for its parity twin's and price the leg-mid
    fallback off the twin's legs (credit equivalent). Delta, IV and the model are untouched --
    they describe the position, which is the same.

    Adds par_{short,long}_{bid,ask} for the leg-book check and structure="debit". A row whose
    twin is missing from the snapshot is dropped: it could not be traded as a debit."""
    import pandas as pd
    if candidates.empty:
        return candidates
    s = snap.copy()
    s["exp_"] = pd.to_datetime(s["ExpirationDate"]).dt.strftime("%Y-%m-%d")
    s["pc_"] = s["PutCall"].astype(str).str.lower().str[0]
    idx = {(r.Symbol, r.exp_, float(r.StrikePrice), r.pc_): r for r in s.itertuples()}
    keep = []
    df = candidates.copy()
    for col in ("par_short_bid", "par_short_ask", "par_long_bid", "par_long_ask"):
        df[col] = float("nan")
    df["structure"] = "debit"
    for i, r in df.iterrows():
        pc = parity_right(r["spread_type"]).lower()
        exp = pd.Timestamp(r["expiry_date"]).strftime("%Y-%m-%d")
        so = idx.get((r["ticker"], exp, float(r["short_strike"]), pc))
        lo = idx.get((r["ticker"], exp, float(r["long_strike"]), pc))
        if so is None or lo is None or not getattr(so, "conId", None) or not getattr(lo, "conId", None):
            continue
        sb, sa, lb, la = (float(so.BidPrice), float(so.AskPrice), float(lo.BidPrice), float(lo.AskPrice))
        w = float(r["spread_width"])
        df.at[i, "short_conid"], df.at[i, "long_conid"] = int(so.conId), int(lo.conId)
        df.at[i, "par_short_bid"], df.at[i, "par_short_ask"] = sb, sa
        df.at[i, "par_long_bid"], df.at[i, "par_long_ask"] = lb, la
        # debit = long_mid - short_mid; credit equivalent = width - debit
        df.at[i, "net_credit"] = round(w - ((lb + la) / 2.0 - (sb + sa) / 2.0), 4)
        df.at[i, "max_loss"] = round(w - df.at[i, "net_credit"], 4)
        keep.append(i)
    dropped = len(df) - len(keep)
    if dropped:
        print(f"  [debit] {dropped} candidate(s) have no parity-twin legs in the snapshot -- dropped", flush=True)
    return df.loc[keep].copy()
