"""Same-timestamp combo-book comparison: canon credit spread vs its parity-equivalent debit spread.

WHY (user 2026-10-14): a credit spread and a debit spread on the SAME two strikes and the
SAME expiry are the same position (put-call parity). The canon trade is entered as a credit
spread, which carries an early-assignment tail on its ITM short put; the parity-equivalent
debit spread's short leg is a call, and a no-dividend American call is never rationally
exercised early, so that tail goes to roughly zero. Giving up the credit costs CARRY_RATE on
the capital, measured at $145 across the 3,045-trade canon book against $51,304 of P&L (0.28%).

The ONE thing theory cannot answer is whether the opposite-right legs quote wide enough to eat
that 0.28%. This measures it off the exchange complex-order book, which is the number on the
order ticket -- not leg BBOs, which are exactly what combo_quotes.py exists to avoid.

PARITY MAPPING. The canon pairs (short_strike, long_strike) with the short leg nearer spot:
  bull_put : short K_hi put,  long K_lo put   ->  debit = long K_lo CALL, short K_hi CALL
  bear_call: short K_lo call, long K_hi call  ->  debit = long K_hi PUT,  short K_lo PUT
In BOTH cases the leg ACTIONS are unchanged (SELL short_strike, BUY long_strike) and only the
right flips. So the debit bag is the credit bag with the opposite right, nothing else.

SIGN. A BAG price is a NET DEBIT (combo_quotes.py). So the credit bag quotes negative and
  credit_mid = -(bid + ask) / 2     (positive = credit received)
  debit_mid  =  (bid + ask) / 2     (positive = debit paid)
and parity says credit_mid + debit_mid == width, up to carry and any early-exercise premium.
The residual is reported per row: a large one means one of the two books is stale or one-sided,
NOT that parity is broken.

BOTH BAGS ARE QUOTED IN THE SAME BATCH, interleaved. Quoting them in two passes lets the
underlying move between them and manufactures a width difference that is not there.

Read-only. Writes one timestamped CSV under research/, touches no live data file.

Usage (on the mini, Gateway up, during market hours):
    python3 -m research.debit_vs_credit_combo
    python3 -m research.debit_vs_credit_combo --max 20 --qualified-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from ib_insync import IB, Bag, ComboLeg, Option

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config                      # noqa: E402
from live.combo_quotes import _has_quote          # noqa: E402
from live.fetcher import _connect_with_retry      # noqa: E402

# 110 = the scan's combo pass, 111 = combo_stream (live), 193 = assignment risk; 113 is free.
CLIENT_ID = 113


def _board(qualified_only: bool, limit: int) -> list[dict]:
    """Rows off the latest ranked payload. No conIds yet -- both rights get resolved below."""
    fp = Path(live_config.RANKED_DIR) / "latest.json"
    payload = json.loads(fp.read_text())
    rows = payload.get("ticker") or []
    if qualified_only:
        rows = [r for r in rows if r.get("qualified")]
    else:
        rows = [r for r in rows if r.get("qualified")] + [r for r in rows if not r.get("qualified")]
    out = []
    for r in rows:
        try:
            exp = str(r["expiry_date"])[:10]
            ks, kl = float(r["short_strike"]), float(r["long_strike"])
        except (KeyError, TypeError, ValueError):
            continue
        if not exp or r.get("spread_type") not in ("bull_put", "bear_call"):
            continue
        out.append({"ticker": str(r["ticker"]), "expiry": exp, "k_s": ks, "k_l": kl,
                    "spread_type": r["spread_type"], "qualified": bool(r.get("qualified")),
                    "ground": r.get("GROUND"), "scan_credit": r.get("net_credit")})
        if len(out) >= limit:
            break
    return out


def _conid(ib: IB, ticker: str, exp: str, strike: float, right: str) -> int | None:
    o = Option(ticker, exp.replace("-", ""), strike, right, "SMART", currency="USD")
    try:
        if not ib.qualifyContracts(o) or not o.conId:
            return None
    except Exception:
        return None
    return int(o.conId)


def _bag(ticker: str, short_conid: int, long_conid: int, exch: str) -> Bag:
    """SELL the short strike, BUY the long strike. Identical for the credit and debit bag --
    see the PARITY MAPPING note; only the conIds' right differs."""
    return Bag(symbol=ticker, exchange=exch, currency="USD",
               comboLegs=[ComboLeg(conId=short_conid, ratio=1, action="SELL", exchange=exch),
                          ComboLeg(conId=long_conid, ratio=1, action="BUY", exchange=exch)])


def _quote_batch(ib: IB, bags: list, wait_s: float) -> list:
    """Stream-quote one batch. Mirrors combo_quotes._quote_batch: reqTickers() returns NaN on
    every BAG field, so subscribe and poll until the book arrives or arrivals stall."""
    tickers = [ib.reqMktData(b, "", False, False) for b in bags]
    t0 = time.monotonic()
    deadline = t0 + wait_s
    min_wait = float(getattr(live_config, "LIVE_COMBO_MIN_WAIT", 3.0))
    stall_s = float(getattr(live_config, "LIVE_COMBO_STALL", 1.5))
    best, last_change = -1, t0
    while time.monotonic() < deadline:
        ib.sleep(0.25)
        n = sum(1 for t in tickers if _has_quote(t))
        if n > best:
            best, last_change = n, time.monotonic()
            if n == len(tickers):
                break
        elif time.monotonic() - t0 >= min_wait and time.monotonic() - last_change >= stall_s:
            break
    out = [(t.bid, t.ask, t.last) for t in tickers]
    for b in bags:
        try:
            ib.cancelMktData(b)
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=40, help="board rows to test (default 40)")
    ap.add_argument("--qualified-only", action="store_true", help="only this scan's picks")
    ap.add_argument("--out", default=None, help="CSV path (default research/debit_vs_credit_<ts>.csv)")
    args = ap.parse_args()

    rows = _board(args.qualified_only, args.max)
    if not rows:
        print("no usable rows on the ranked board", flush=True)
        return 1
    print(f"board rows: {len(rows)}", flush=True)

    exch = getattr(live_config, "LIVE_COMBO_EXCHANGE", "SMART")
    ib = IB()
    try:
        _connect_with_retry(ib, CLIENT_ID)
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

        # Resolve BOTH rights at BOTH strikes before any quoting, so the two bags of a pair
        # always enter the same batch together.
        pairs = []
        for r in rows:
            credit_right = "P" if r["spread_type"] == "bull_put" else "C"
            debit_right = "C" if credit_right == "P" else "P"
            legs = {}
            for tag, right in (("credit", credit_right), ("debit", debit_right)):
                s = _conid(ib, r["ticker"], r["expiry"], r["k_s"], right)
                l = _conid(ib, r["ticker"], r["expiry"], r["k_l"], right)
                if s and l:
                    legs[tag] = (s, l)
            if len(legs) == 2:
                pairs.append((r, legs))
            else:
                print(f"  [skip] {r['ticker']} {r['k_s']}/{r['k_l']} {r['expiry']}: "
                      f"missing {'credit' if 'credit' not in legs else 'debit'} leg conIds", flush=True)
        if not pairs:
            print("no row resolved both rights", flush=True)
            return 1
        print(f"resolved both rights: {len(pairs)} rows -> {2 * len(pairs)} bags", flush=True)

        # Interleave credit and debit so a pair never straddles two batches with a price move
        # in between. Batch size is even, so a pair cannot be split.
        flat = []
        for r, legs in pairs:
            flat.append((r, "credit", _bag(r["ticker"], *legs["credit"], exch)))
            flat.append((r, "debit", _bag(r["ticker"], *legs["debit"], exch)))

        batch = int(live_config.LIVE_COMBO_BATCH)
        batch -= batch % 2
        wait_s = float(live_config.LIVE_COMBO_WAIT)
        quotes = []
        for i in range(0, len(flat), batch):
            chunk = flat[i:i + batch]
            quotes.extend(_quote_batch(ib, [b for _, _, b in chunk], wait_s))
    except Exception as e:
        print(f"quoting failed: {e}", flush=True)
        return 1
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    book: dict[int, dict] = {}
    for (r, tag, _), (bid, ask, last) in zip(flat, quotes):
        key = id(r)
        rec = book.setdefault(key, {"row": r})
        if bid is None or ask is None or pd.isna(bid) or pd.isna(ask):
            continue
        rec[tag] = (float(bid), float(ask), None if last is None or pd.isna(last) else float(last))

    recs = []
    for rec in book.values():
        r = rec["row"]
        width = round(abs(r["k_s"] - r["k_l"]), 4)
        out = {"ticker": r["ticker"], "expiry": r["expiry"], "spread_type": r["spread_type"],
               "k_s": r["k_s"], "k_l": r["k_l"], "width": width,
               "qualified": r["qualified"], "ground": r["ground"], "scan_credit": r["scan_credit"]}
        for tag in ("credit", "debit"):
            q = rec.get(tag)
            out[f"{tag}_bid"] = None if q is None else q[0]
            out[f"{tag}_ask"] = None if q is None else q[1]
            out[f"{tag}_book_width"] = None if q is None else round(q[1] - q[0], 4)
        if rec.get("credit"):
            b, a, _ = rec["credit"]
            out["credit_mid"] = round(-(b + a) / 2.0, 4)      # positive = credit received
        if rec.get("debit"):
            b, a, _ = rec["debit"]
            out["debit_mid"] = round((b + a) / 2.0, 4)        # positive = debit paid
        if out.get("credit_mid") is not None and out.get("debit_mid") is not None:
            out["parity_residual"] = round(out["credit_mid"] + out["debit_mid"] - width, 4)
        if out["credit_book_width"] is not None and out["debit_book_width"] is not None:
            d = round(out["debit_book_width"] - out["credit_book_width"], 4)
            out["width_delta"] = d                             # >0 means the debit book is WIDER
            out["width_delta_pct_of_spread"] = round(100.0 * d / width, 2) if width else None
        recs.append(out)

    df = pd.DataFrame(recs)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_fp = Path(args.out) if args.out else ROOT / "research" / f"debit_vs_credit_{ts}.csv"
    df.to_csv(out_fp, index=False)

    both = df.dropna(subset=["width_delta"]) if "width_delta" in df else df.iloc[0:0]
    print(f"\nrows quoted on BOTH books: {len(both)} of {len(df)}", flush=True)
    if len(both):
        cols = ["ticker", "spread_type", "k_s", "k_l", "credit_book_width", "debit_book_width",
                "width_delta", "width_delta_pct_of_spread", "parity_residual"]
        print(both[cols].to_string(index=False), flush=True)
        med = both["width_delta"].median()
        medp = both["width_delta_pct_of_spread"].median()
        print(f"\nmedian width_delta: {med:+.4f}  ({medp:+.2f}% of spread width)", flush=True)
        print(f"debit book tighter or equal on {int((both.width_delta <= 0).sum())}/{len(both)} rows", flush=True)
        print(f"median |parity_residual|: {both.parity_residual.abs().median():.4f}", flush=True)
        print("\nVERDICT BAR: the carry given up by paying a debit is 0.28% of canon book P&L.", flush=True)
        print("A median width_delta at or below 0 says switching is free on execution;", flush=True)
        print("a positive one must be weighed against that 0.28%.", flush=True)
    else:
        print("no row got a two-sided book on BOTH rights -- rerun during RTH with the Gateway up.", flush=True)
    print(f"\nwrote {out_fp}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
