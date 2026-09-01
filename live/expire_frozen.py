"""Settle frozen daily snapshots whose expiry has passed.

For each frozen file in live/frozen/*.json:
  - If outcome already set: skip
  - If expiry_date is today or earlier: settle

  For each pick in top_picks:
    - Fetch the UNDERLYING ticker's most recent close price via ib_insync
    - Use spreads.calc_outcome / calc_pnl (same logic as the backtest) to
      determine WIN/PARTIAL/LOSS and realized P&L per contract
    - Write payload["outcome"] with the settled results + total P&L

Should be run after market close on the expiry date (16:30 ET or later)
so the close price is final. Idempotent — re-running on an already-settled
file is a no-op.

Usage:
  python3 -m live.expire_frozen                  # use default client ID 201
  python3 -m live.expire_frozen --client-id 251  # override
  python3 -m live.expire_frozen --force          # settle even if expiry is in the future
                                                 # (for testing only — uses current price)

Cron suggestion (Friday after close, weekdays in case of non-Friday expiries):
  30 16 * * 1-5 cd /path/to/repo && python3 -m live.expire_frozen >> live/logs/expire.log 2>&1
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config
import spreads

try:
    from ib_insync import IB, Stock
except ImportError as e:
    raise SystemExit("ib_insync not installed. `pip install ib_insync`") from e


def _connect_ib_with_retries(client_id: int) -> IB:
    last_error = None
    for attempt in range(1, live_config.IB_CONNECT_ATTEMPTS + 1):
        ib = IB()
        try:
            ib.connect(
                live_config.IB_HOST,
                live_config.IB_PORT,
                clientId=client_id,
                timeout=live_config.IB_CONNECT_TIMEOUT,
                readonly=True,
            )
            return ib
        except Exception as error:
            last_error = error
            ib.disconnect()
            if attempt == live_config.IB_CONNECT_ATTEMPTS:
                break
            delay = live_config.IB_CONNECT_RETRY_DELAY * attempt
            print(
                f"  IB connection attempt {attempt}/{live_config.IB_CONNECT_ATTEMPTS} "
                f"failed ({error.__class__.__name__}); retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"IB Gateway connection failed after {live_config.IB_CONNECT_ATTEMPTS} "
        f"attempts (clientId={client_id})"
    ) from last_error


def _is_settable(payload: dict, force: bool = False) -> bool:
    picks = payload.get("top_picks") or []
    if not picks:
        return False
    exp_str = picks[0].get("expiry_date", "")
    try:
        exp_date = datetime.fromisoformat(exp_str).date()
    except (ValueError, TypeError):
        exp_date = None
    if force:
        # Force re-settles ONLY files expiring TODAY. _underlying_price
        # fetches TODAY's close, so re-settling a past expiry would mark
        # it against the wrong session (this corrupted 9 historical files
        # on 2026-06-12 before being caught; restored from Mya).
        return exp_date == date.today()
    if payload.get("outcome"):
        return False
    exp_str = picks[0].get("expiry_date", "")
    try:
        exp_date = datetime.fromisoformat(exp_str).date()
    except (ValueError, TypeError):
        return False
    return exp_date <= date.today()


def _underlying_price(ib: IB, ticker: str) -> float | None:
    """Today's official 4pm RTH close for the underlying.

    Sourced from reqHistoricalData with useRTH=True (the last daily bar's
    close field). Previously this read the snapshot's `close` attribute,
    but in IB's API that field returns the PREVIOUS day's close until the
    next session — which silently caused settlement to use yesterday's
    closes. The historical RTH bar matches what Apple Stocks and other
    consumer apps display as the official today close.

    Falls back to live `last` if historical fetch fails.
    """
    try:
        stock = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(stock)
    except Exception as e:
        print(f"    [{ticker}] qualify failed: {e}", flush=True)
        return None

    try:
        bars = ib.reqHistoricalData(
            stock, endDateTime="", durationStr="2 D",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1,
        )
        if bars:
            return float(bars[-1].close)
    except Exception as e:
        print(f"    [{ticker}] RTH bar fetch failed: {e}", flush=True)

    # Fallback: live last (may include after-hours drift)
    try:
        [t] = ib.reqTickers(stock)
        if t.last and not pd.isna(t.last):
            return float(t.last)
        if t.marketPrice() and not pd.isna(t.marketPrice()):
            return float(t.marketPrice())
    except Exception as e:
        print(f"    [{ticker}] live tick fallback failed: {e}", flush=True)
    return None


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", type=int, default=201,
                        help="IB Gateway clientId (default 201)")
    parser.add_argument("--force", action="store_true",
                        help="Settle even if expiry is in the future "
                             "(uses current/last price; for testing)")
    args = parser.parse_args()

    frozen_dir = Path(live_config.FROZEN_DIR)
    settable: list[tuple[Path, dict]] = []
    for fp in sorted(frozen_dir.glob("*.json")):
        try:
            with open(fp) as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ✗ skip {fp.name}: {e}", flush=True)
            continue
        if _is_settable(payload, force=args.force):
            settable.append((fp, payload))

    if not settable:
        print("No settable frozen files", flush=True)
        return 0

    print(f"Settling {len(settable)} frozen file(s)...", flush=True)

    try:
        ib = _connect_ib_with_retries(args.client_id)
    except Exception as e:
        print(f"  ✗ IB connect failed: {e}", flush=True)
        return 1
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)

    try:
        for fp, payload in settable:
            picks = payload.get("top_picks") or []
            print(f"  {fp.name}: {len(picks)} picks", flush=True)

            results: dict[str, dict] = {}
            total_pnl = 0.0
            total_pnl_actual = 0.0
            any_actual = False
            wins = losses = partials = 0

            # Entry-credit basis (canonical 2026-06-12, matches the history
            # page's fill view in webapp._enrich_pick): actual_credit when
            # the user recorded a real fill, else 0.80 x the freeze-time
            # combo MID recomputed from the leg quotes (also repairs OLD
            # frozen files whose stored net_credit is the phantom
            # LAST-clamped credit), else 0.80 x stored net_credit. The old
            # 0.85 x LAST basis here was the last survivor of the LAST
            # canon and made settled P&L disagree with the displayed
            # max-loss column (error #78).
            FILL_FRAC = 0.80
            for pick in picks:
                tk         = pick["ticker"]
                spread_typ = pick["spread_type"]
                short_k    = float(pick["short_strike"])
                long_k     = float(pick["long_strike"])
                mid_credit = float(pick["net_credit"])
                mid_ml     = float(pick["max_loss"])
                spread_w   = mid_credit + mid_ml

                actual_c0 = pick.get("actual_credit")
                sb = pick.get("short_bid"); sa = pick.get("short_ask")
                lb = pick.get("long_bid");  la = pick.get("long_ask")
                if actual_c0 is not None:
                    credit = round(float(actual_c0), 4)
                elif None not in (sb, sa, lb, la) and float(sa) > 0 and float(la) > 0:
                    qmid = (float(sb) + float(sa)) / 2.0 - (float(lb) + float(la)) / 2.0
                    credit = round(max(qmid, 0.0) * FILL_FRAC, 4)
                else:
                    credit = round(mid_credit * FILL_FRAC, 4)
                credit = min(credit, round(spread_w, 4))
                max_loss = round(spread_w - credit, 4)

                close = _underlying_price(ib, tk)
                if close is None:
                    print(f"    [{tk}] no close price — skipping", flush=True)
                    results[tk] = {"error": "no close price"}
                    continue

                outcome_code  = spreads.calc_outcome(close, short_k, long_k, spread_typ)
                pnl_per_share = spreads.calc_pnl(close, short_k, long_k,
                                                  credit, max_loss, spread_typ)
                pnl_per_ctr   = round(pnl_per_share * 100, 2)

                if outcome_code == 1.0:
                    label = "WIN"
                    wins += 1
                elif outcome_code == -1.0:
                    label = "LOSS"
                    losses += 1
                else:
                    label = "PARTIAL"
                    partials += 1

                pick_res = {
                    "underlying_price":   round(close, 4),
                    "outcome_code":       float(outcome_code),
                    "result":             label,
                    "pnl_per_share":      round(float(pnl_per_share), 4),
                    "pnl_per_contract":   pnl_per_ctr,
                }
                # Actual P&L if real fill credit was recorded on the pick
                # (pick["actual_credit"] is what the broker actually filled at).
                actual_c = pick.get("actual_credit")
                if actual_c is not None:
                    actual_c = float(actual_c)
                    actual_ml = round(spread_w - actual_c, 4)
                    actual_pps = spreads.calc_pnl(close, short_k, long_k,
                                                   actual_c, actual_ml, spread_typ)
                    actual_pnl_ctr = round(float(actual_pps) * 100, 2)
                    pick_res["actual_pnl_per_share"]   = round(float(actual_pps), 4)
                    pick_res["actual_pnl_per_contract"] = actual_pnl_ctr

                results[tk] = pick_res
                total_pnl += pnl_per_ctr
                if actual_c is not None:
                    total_pnl_actual += actual_pnl_ctr
                    any_actual = True
                else:
                    total_pnl_actual += pnl_per_ctr  # fall back if some picks missing
                print(f"    [{tk}] close=${close:.2f}  {label}  P&L=${pnl_per_ctr:+.2f}"
                      + (f"  actual=${actual_pnl_ctr:+.2f}" if actual_c is not None else ""),
                      flush=True)

            outcome = {
                "settled_at":             datetime.now().isoformat(timespec="seconds"),
                "results":                results,
                "wins":                   wins,
                "losses":                 losses,
                "partials":               partials,
                "total_pnl_per_contract": round(total_pnl, 2),
            }
            if any_actual:
                outcome["total_pnl_per_contract_actual"] = round(total_pnl_actual, 2)
            payload["outcome"] = outcome

            _atomic_write(fp, payload)
            print(f"  ✓ settled {fp.name}: "
                  f"{wins}W/{partials}P/{losses}L  total P&L=${total_pnl:+.2f}/ctr",
                  flush=True)
    finally:
        ib.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
