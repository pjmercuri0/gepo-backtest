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


def _bag_for(row, exch: str | None = None, twin: bool = False) -> Bag | None:
    """BAG contract for one candidate. None if either conId is missing.

    A credit spread is opened by selling the short leg and buying the long
    leg, so those are the leg actions regardless of bull_put vs bear_call —
    the direction is already baked into which strikes got paired.
    """
    short_conid = row.get("twin_short_conid" if twin else "short_conid")
    long_conid = row.get("twin_long_conid" if twin else "long_conid")
    if not short_conid or not long_conid:
        return None
    if pd.isna(short_conid) or pd.isna(long_conid):
        return None
    # NOT "SMART". SMART returns nan on every combo field; a named exchange
    # serves the quote (2026-09-01, AAPL 325/322.5P: SMART nan/nan, CBOE
    # -1.10/-0.95, ISE identical to CBOE). Verified against the Gateway.
    # 2026-10-27 (user: "it must be picking the best one"): a SMART bag whose LEGS are tagged
    # SMART is what returns nan. Tag the legs with a named exchange and the SMART bag quotes the
    # best bid and best ask across every options exchange -- EOG 162.5/160P read -1.29/-1.15
    # (BATS bid, ISE/BOX/MIAX ask) while CBOE alone said -1.50/-0.45 and SMART/SMART said nan.
    # Any named exchange works (CBOE, ISE, AMEX gave identical SMART quotes).
    exch = exch or getattr(live_config, "LIVE_COMBO_EXCHANGE", "SMART")
    return Bag(
        symbol=str(row["ticker"]),
        exchange=exch,
        currency="USD",
        comboLegs=[
            ComboLeg(conId=int(short_conid), ratio=1, action="SELL", exchange=LEG_EXCHANGE),
            ComboLeg(conId=int(long_conid), ratio=1, action="BUY", exchange=LEG_EXCHANGE),
        ],
    )


# Book order (user 2026-10-27): SMART first -- the best bid/ask across every exchange's combo
# book (see _bag_for) -- CBOE's own book for whatever SMART left empty, then the caller's leg
# mids. Each pass re-quotes only the rows still missing, inside the same LIVE_COMBO_TIMEOUT
# budget, so the scan cannot run longer than before.
COMBO_EXCHANGES = ("SMART", "CBOE")
LEG_EXCHANGE = "CBOE"
# A combo book is usable only when BOTH sides sit inside [0, width] AND the bid-ask gap is at
# most MAX_GAP_FRAC of the width. UPS 105/104 on 2026-10-28 15:32: SMART bid -0.96 / ask -0.06 on
# a 1.00-wide spread -- each side inside the width, mid 0.51, page showed $0.53 while the app's
# ticket read -0.47/-0.09 (0.28). A 0.90 market on a 1.00 width is a stub, not a quote.
MAX_GAP_FRAC = float(getattr(live_config, "LIVE_COMBO_MAX_GAP_FRAC", 0.5))


def usable_combo(bid, ask, width) -> bool:
    """True if a credit-bag bid/ask (both negative) is a real two-sided market for `width`."""
    try:
        cb, ca = -float(bid), -float(ask)
    except (TypeError, ValueError):
        return False
    if cb != cb or ca != ca:
        return False
    if width is None or width != width or width <= 0:
        return cb >= 0 and ca >= 0
    w = float(width)
    if not (0.0 <= cb <= w + 1e-9 and 0.0 <= ca <= w + 1e-9):
        return False
    return abs(cb - ca) <= MAX_GAP_FRAC * w + 1e-9            # leg tag only; see _bag_for


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
    print(f"  [combo]     batch {'+'.join(sorted({b.exchange for b in bags}))} n={len(tickers)} quoted={max(best, 0)} "
          f"exit={time.monotonic() - t0:.1f}s last_arrival={last_change - t0:.1f}s", flush=True)
    for b in bags:
        try:
            ib.cancelMktData(b)
        except Exception:
            pass
    # The gateway releases cancelled lines asynchronously. Measured 2026-11-09 10:40: three
    # 90-bag rounds back to back quoted 85 / 0 / 0 with no Error 101 (the starved rounds just
    # never got a book); with a 1s pause after the cancels they quoted 85 / 79 / 83. The old
    # 50-bag rounds never showed it because two overlapping rounds still fit in the budget.
    ib.sleep(float(getattr(live_config, "LIVE_COMBO_SETTLE", 1.0)))
    return out


def _quote_window(ib: IB, keyed: list, lines: int, hold_s: float, deadline: float,
                  settle_s: float, idle_s: float = 0.25) -> dict:
    """Stream-quote every bag in `keyed` ([(key, bag), ...]) through a sliding window of at
    most `lines` concurrent subscriptions. A bag leaves the window as soon as it has a
    two-sided quote or has held its line for `hold_s`; its line counts as busy for a further
    `settle_s` after the cancel (the gateway frees lines asynchronously -- see _quote_batch).
    Returns {key: (bid, ask, last)} for every bag that was subscribed.

    Why not rounds: in a round every line waits for the round's slowest bag plus the stall
    window, and 70% of bags never quote at all (46/146 on 2026-11-09 10:30), so most lines
    sit idle on dead books while the next bags queue. Here a dead book costs exactly hold_s
    of one line and a live one leaves the moment it quotes.
    """
    pending = list(keyed)          # consumed from the front
    active: dict = {}              # key -> (bag, ticker, t_subscribed)
    releasing: list = []           # cancel timestamps still inside settle_s
    out: dict = {}
    while pending or active:
        now = time.monotonic()
        releasing = [t for t in releasing if now - t < settle_s]
        while pending and len(active) + len(releasing) < lines and now < deadline:
            key, bag = pending.pop(0)
            active[key] = (bag, ib.reqMktData(bag, "", False, False), now)
        if not active:
            if now >= deadline:
                break
            ib.sleep(idle_s)
            continue
        ib.sleep(idle_s)
        now = time.monotonic()
        for key, (bag, t, t0) in list(active.items()):
            if _has_quote(t) or now - t0 >= hold_s or now >= deadline:
                out[key] = (t.bid, t.ask, t.last)
                try:
                    ib.cancelMktData(bag)
                except Exception:
                    pass
                releasing.append(now)
                del active[key]
        if now >= deadline and not active:
            break
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

    rows = {idx: row for idx, row in df.iterrows() if _bag_for(row) is not None}
    if not rows:
        print("  [combo] no candidate carries a conId pair; skipping", flush=True)
        return df

    ib = IB()
    t0 = time.monotonic()
    twin_rows, got_twin = [], {}
    try:
        _connect_with_retry(ib, int(live_config.LIVE_COMBO_CLIENT_ID))
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
        batch = int(live_config.LIVE_COMBO_BATCH)
        wait_s = float(live_config.LIVE_COMBO_WAIT)
        deadline = time.monotonic() + float(live_config.LIVE_COMBO_TIMEOUT)
        # Parity twins (live/stores.py, gepo-debit-ticker): the same spread as a debit bag, SELL the
        # short strike / BUY the long strike in the other right. Quoted BEFORE the credit pass (user
        # 2026-11-02: 'debit priority then credit') inside the same budget; stored on the credit scale
        # (debit - width) like every debit row.
        twin_rows = [idx for idx in rows if _bag_for(rows[idx], "SMART", twin=True) is not None]
        got_twin: dict = {}
        got: dict = {}                    # idx -> (bid, ask, last)

        # 2026-11-09: one batch carries EVERY exchange's bag for a row (SMART and CBOE side by
        # side) instead of a full SMART pass followed by a CBOE pass over the leftovers. The
        # exchange preference is unchanged -- for each row the first exchange in COMBO_EXCHANGES
        # with a usable book wins, exactly what the sequential passes produced -- but the rows
        # are covered in half the rounds. Measured 10:30 that day: 12 rounds x 3.5-5.5s = 55s of
        # the scan's 124s; arrivals stop 2-4s after subscribing, so round count is the whole cost.
        # Lines: a probe at 10:35 that day hit Error 101 at the 100th streaming subscription
        # (account-wide, with combo_stream holding its 98), so a round uses LIVE_COMBO_LINES.
        lines = int(getattr(live_config, "LIVE_COMBO_LINES", 90))
        rows_per_round = max(1, lines // len(COMBO_EXCHANGES))

        def _round(idx_list, twin: bool, accept):
            """Quote idx_list in rounds of rows_per_round rows x all exchanges; accept(idx, ex, bid, ask, last)
            returns the stored tuple or None. Returns {idx: (ex, tuple)}."""
            out = {}
            for i in range(0, len(idx_list), rows_per_round):
                if time.monotonic() >= deadline:
                    break
                chunk = idx_list[i:i + rows_per_round]
                keyed = []
                for idx in chunk:
                    for ex in COMBO_EXCHANGES:
                        b = _bag_for(rows[idx], ex, twin=twin)
                        if b is not None:
                            keyed.append((idx, ex, b))
                if not keyed:
                    continue
                res = _quote_batch(ib, [b for _, _, b in keyed], wait_s)
                per_idx: dict = {}
                for (idx, ex, _), (bid, ask, last) in zip(keyed, res):
                    val = accept(idx, ex, bid, ask, last)
                    if val is not None:
                        per_idx.setdefault(idx, {})[ex] = val
                for idx, by_ex in per_idx.items():
                    for ex in COMBO_EXCHANGES:          # preference order, as before
                        if ex in by_ex:
                            out[idx] = (ex, by_ex[ex])
                            break
            return out

        def _accept_twin(idx, ex, bid, ask, last):
            if bid is None or ask is None or pd.isna(bid) or pd.isna(ask):
                return None
            # A debit bag is a positive price. 0/0 is an empty book, and subtracting the
            # width turned it into a credit equal to the width -- RTX 200/202.5 showed a
            # $0.00 debit with a check mark on 2026-11-02 (user: 'these ones are lighting up').
            if float(bid) <= 0 or float(ask) <= 0:
                return None
            _r = rows[idx]
            _w = _r.get("spread_width")
            if _w is None or pd.isna(_w):
                _w = abs(float(_r["short_strike"]) - float(_r["long_strike"]))
            from live import debit_mode
            cb, ca, cl = debit_mode.to_credit_bag(bid, ask, last, float(_w))
            return (cb, ca, cl) if usable_combo(cb, ca, _w) else None

        def _accept_main(idx, ex, bid, ask, last):
            if bid is None or ask is None or pd.isna(bid) or pd.isna(ask):
                return None
            _r = rows[idx]
            _w = _r.get("spread_width")
            if _w is None or pd.isna(_w):
                _w = abs(float(_r["short_strike"]) - float(_r["long_strike"]))
            if _r.get("structure") == "debit":
                return (bid, ask, last)      # debit scale; validated after conversion below
            return (bid, ask, last) if usable_combo(bid, ask, _w) else None

        mode = str(getattr(live_config, "LIVE_COMBO_MODE", "window"))
        if mode == "window":
            # One sliding window over every bag: twin bags queued first (user 2026-11-02:
            # 'debit priority then credit'), then the credit bags; both exchanges per row.
            keyed = []
            for idx in twin_rows:
                for ex in COMBO_EXCHANGES:
                    b = _bag_for(rows[idx], ex, twin=True)
                    if b is not None:
                        keyed.append((("twin", idx, ex), b))
            for idx in rows:
                for ex in COMBO_EXCHANGES:
                    b = _bag_for(rows[idx], ex)
                    if b is not None:
                        keyed.append((("main", idx, ex), b))
            res = _quote_window(ib, keyed, lines,
                                float(getattr(live_config, "LIVE_COMBO_HOLD", 5.0)), deadline,
                                float(getattr(live_config, "LIVE_COMBO_SETTLE", 1.0)))
            by: dict = {"twin": {}, "main": {}}
            for (side, idx, ex), (bid, ask, last) in res.items():
                val = (_accept_twin if side == "twin" else _accept_main)(idx, ex, bid, ask, last)
                if val is not None:
                    by[side].setdefault(idx, {})[ex] = val
            won = {"twin": {}, "main": {}}
            for side in ("twin", "main"):
                for idx, by_ex in by[side].items():
                    for ex in COMBO_EXCHANGES:          # preference order, as before
                        if ex in by_ex:
                            won[side][idx] = (ex, by_ex[ex])
                            break
            got_twin = {idx: v for idx, (ex, v) in won["twin"].items()}
            got = {idx: v for idx, (ex, v) in won["main"].items()}
            for side, label, n_rows in (("twin", "twin: ", len(twin_rows)), ("main", "", len(rows))):
                if n_rows:
                    print(f"  [combo] {label}" + ", ".join(
                        f"{ex} +{sum(1 for e, _ in won[side].values() if e == ex)}" for ex in COMBO_EXCHANGES)
                        + f" quoted (window of {lines} lines, {len(keyed)} bags, "
                        f"{len(res)} subscribed, {time.monotonic() - t0:.1f}s)", flush=True)
        else:
            # Parity twins first (user 2026-11-02: 'debit priority then credit'), same budget.
            if twin_rows:
                r = _round(twin_rows, True, _accept_twin)
                got_twin = {idx: v for idx, (ex, v) in r.items()}
                print("  [combo] twin: " + ", ".join(
                    f"{ex} +{sum(1 for e, _ in r.values() if e == ex)}" for ex in COMBO_EXCHANGES)
                    + f" quoted ({(len(twin_rows) + rows_per_round - 1) // rows_per_round} rounds)", flush=True)
            r = _round(list(rows), False, _accept_main)
            got = {idx: v for idx, (ex, v) in r.items()}
            print("  [combo] " + ", ".join(
                f"{ex} +{sum(1 for e, _ in r.values() if e == ex)}" for ex in COMBO_EXCHANGES)
                + f" quoted ({(len(rows) + rows_per_round - 1) // rows_per_round} rounds)", flush=True)
    except Exception as e:
        print(f"  [combo] quoting failed ({e}); falling back to leg mids", flush=True)
        return df
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    for idx, (bid, ask, last) in got_twin.items():
        df.at[idx, "twin_combo_bid"] = float(bid)
        df.at[idx, "twin_combo_ask"] = float(ask)
        df.at[idx, "twin_combo_credit_mid"] = round(-(float(bid) + float(ask)) / 2.0, 4)
    if twin_rows:
        print(f"  [combo] twin quoted {len(got_twin)}/{len(twin_rows)}", flush=True)

    filled = 0
    for idx, (bid, ask, last) in got.items():
        if "structure" in df.columns and df.at[idx, "structure"] == "debit":
            # Debit bag -> credit-bag scale (live/debit_mode.py): subtract the width.
            from live import debit_mode
            bid, ask, last = debit_mode.to_credit_bag(bid, ask, last, float(df.at[idx, "spread_width"]))
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
