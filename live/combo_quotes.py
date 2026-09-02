"""Quote candidate spreads directly off IBKR's complex-order book.

The ranker's credit has always been synthetic: mid(short leg) − mid(long leg),
each mid computed from that leg's own BBO. That silently breaks whenever one
leg is quoted badly. Observed 2026-09-01 12:31 on MO Sep04 70/69:

    short 70P   bid 0.41 (994 up)   ask 1.68 (50 up)   last 0.50
    long  69P   bid 0.23            ask 0.62           last 0.28

    leg-mid credit                    0.620   <- what the ranker scored
    leg-implied combo market   -1.45 / +0.21  (width 1.66)
    IBKR combo book            -0.91 / -0.06  (width 0.85)

The 1.68 offer was a 50-lot placeholder against a 994-lot bid, so the short
leg's mid sat at roughly 2x fair value and all of the error landed in the
credit. Ranked #1 that scan on a credit ratio of 1.63; the actually-openable
credit was 0.06.

A BAG contract asks IBKR for the two legs as one package. That quote comes
from exchange complex-order books, where participants quote the spread
directly, so it is materially tighter than anything derivable from the leg
BBOs — and it is the number on the order ticket.

Sign convention (matches the TWS ticket): a BAG price is a NET DEBIT, so a
credit is negative. Opening a credit spread means BUYING the bag, which pays
the ask. Hence:

    credit_mid   = -(bid + ask) / 2      the package's midpoint
    credit_touch = -ask                  what you get hitting the offer now
    credit_last  = -last                 the combo's last trade -- this is the
                                         big number on the TWS order ticket
                                         (MO 70/69P showed -0.30 there and
                                         t.last returns -0.30)
"""
from __future__ import annotations

import time

import pandas as pd
from ib_insync import IB, Bag, ComboLeg

from live import live_config
from live.fetcher import _connect_with_retry


def _bag_for(row) -> Bag | None:
    """BAG contract for one candidate. None if either conId is missing.

    A credit spread is opened by selling the short leg and buying the long
    leg, so those are the leg actions regardless of bull_put vs bear_call —
    the direction is already baked into which strikes got paired.
    """
    short_conid = row.get("short_conid")
    long_conid = row.get("long_conid")
    if not short_conid or not long_conid:
        return None
    if pd.isna(short_conid) or pd.isna(long_conid):
        return None
    # NOT "SMART". SMART returns nan on every combo field; a named exchange
    # serves the quote (2026-09-01, AAPL 325/322.5P: SMART nan/nan, CBOE
    # -1.10/-0.95, ISE identical to CBOE). Verified against the Gateway.
    exch = getattr(live_config, "LIVE_COMBO_EXCHANGE", "CBOE")
    return Bag(
        symbol=str(row["ticker"]),
        exchange=exch,
        currency="USD",
        comboLegs=[
            ComboLeg(conId=int(short_conid), ratio=1, action="SELL", exchange=exch),
            ComboLeg(conId=int(long_conid), ratio=1, action="BUY", exchange=exch),
        ],
    )


def _has_quote(t) -> bool:
    return (t.bid is not None and pd.notna(t.bid)
            and t.ask is not None and pd.notna(t.ask))


def _quote_batch(ib: IB, bags: list, wait_s: float) -> list:
    """Stream-quote one batch of bags; return [(bid, ask, last), ...].

    reqTickers() does NOT work on a BAG — IB has no snapshot market data for
    combos and every field comes back NaN (verified 2026-09-01 against the
    Gateway). Streaming reqMktData does work, but the book arrives a beat after
    subscribing, so poll until every bag has a two-sided quote or the wait
    expires. Combos with an empty complex-order book never fill and simply
    burn the full wait, which is why this is batched.
    """
    tickers = [ib.reqMktData(b, "", False, False) for b in bags]
    t0 = time.monotonic()
    deadline = t0 + wait_s
    min_wait = float(getattr(live_config, "LIVE_COMBO_MIN_WAIT", 3.0))
    stall_s = float(getattr(live_config, "LIVE_COMBO_STALL", 1.5))
    best = -1
    last_change = t0
    while time.monotonic() < deadline:
        ib.sleep(0.25)
        n = sum(1 for t in tickers if _has_quote(t))
        if n > best:
            best, last_change = n, time.monotonic()
            if n == len(tickers):
                break
        elif (time.monotonic() - t0 >= min_wait
              and time.monotonic() - last_change >= stall_s):
            # Arrivals have stopped. Waiting out the full budget only idles:
            # measured 2026-09-02, a 50-bag batch went 0 -> 22 quotes inside
            # 2.0s and then sat flat at 22 for the remaining 10s, because the
            # rest simply have no complex-order book.
            break
    out = [(t.bid, t.ask, t.last) for t in tickers]
    for b in bags:
        try:
            ib.cancelMktData(b)
        except Exception:
            pass
    return out


def attach_combo_quotes(candidates: pd.DataFrame) -> pd.DataFrame:
    """Add combo_bid / combo_ask / combo_credit_mid / combo_credit_touch.

    Rows without a conId pair, and rows IBKR returns no market for, come back
    with NaN in those columns — the caller decides whether to fall back to the
    leg-mid credit or drop them. Never raises: a Gateway problem here must not
    take down a scan that has already paid for its option data.
    """
    if candidates.empty:
        return candidates

    df = candidates.copy()
    for col in ("combo_bid", "combo_ask", "combo_last", "combo_credit_mid",
                "combo_credit_touch", "combo_credit_last"):
        df[col] = float("nan")

    pairs = [(idx, _bag_for(row)) for idx, row in df.iterrows()]
    live_pairs = [(idx, bag) for idx, bag in pairs if bag is not None]
    if not live_pairs:
        print("  [combo] no candidate carries a conId pair; skipping", flush=True)
        return df

    ib = IB()
    t0 = time.monotonic()
    try:
        _connect_with_retry(ib, int(live_config.LIVE_COMBO_CLIENT_ID))
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
        bags = [bag for _, bag in live_pairs]
        batch = int(live_config.LIVE_COMBO_BATCH)
        wait_s = float(live_config.LIVE_COMBO_WAIT)
        deadline = time.monotonic() + float(live_config.LIVE_COMBO_TIMEOUT)
        quotes: list = []
        for i in range(0, len(bags), batch):
            chunk = bags[i:i + batch]
            if time.monotonic() >= deadline:
                quotes.extend([(None, None, None)] * len(chunk))
                continue
            quotes.extend(_quote_batch(ib, chunk, wait_s))
    except Exception as e:
        print(f"  [combo] quoting failed ({e}); falling back to leg mids", flush=True)
        return df
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    filled = 0
    for (idx, _), (bid, ask, last) in zip(live_pairs, quotes):
        if bid is None or ask is None or pd.isna(bid) or pd.isna(ask):
            continue
        df.at[idx, "combo_bid"] = float(bid)
        df.at[idx, "combo_ask"] = float(ask)
        if last is not None and pd.notna(last):
            df.at[idx, "combo_last"] = float(last)
            df.at[idx, "combo_credit_last"] = round(-float(last), 4)
        df.at[idx, "combo_credit_mid"] = round(-(float(bid) + float(ask)) / 2.0, 4)
        df.at[idx, "combo_credit_touch"] = round(-float(ask), 4)
        filled += 1

    print(f"  [combo] quoted {filled}/{len(df)} candidates in "
          f"{time.monotonic() - t0:.1f}s", flush=True)
    return df
