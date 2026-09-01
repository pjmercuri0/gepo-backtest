"""Archive every scan's qualified picks + settle expired ones.

Runs after the ranker on each :01/:31 firing (pull_now_parallel.sh).
1. Appends the current ranking's qualified top-5 to
   live/intraday_picks/YYYY-MM-DD.json (idempotent per HHMM).
2. Settles any unsettled picks (any day file) whose expiry has passed,
   using data/daily_bars_yahoo closes. P&L model matches the backtest:
   entry = 0.80 x mid credit, partial-WIN at 50% intrinsic.

Purpose: test whether the GROUND threshold selects good picks at ANY time
of day, or only at the 15:01 freeze that mirrors the backtest's EOD basis.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from live import live_config
import spreads

FILL_FRAC = 0.80
PICKS_DIR = Path(live_config.ROOT_DIR) / "intraday_picks"
BARS_DIR = Path(live_config.ROOT_DIR).parent / "data" / "daily_bars_yahoo"
PICK_FIELDS = [
    "ticker", "spread_type", "short_strike", "long_strike",
    "net_credit", "max_loss", "spread_width", "credit_ratio",
    "GROUND", "G", "DKL", "entry_price", "expiry_date", "DTE",
]


def capture(latest_path: Path) -> None:
    try:
        d = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[snapshot_picks] cannot read latest.json: {e}")
        return
    ts = d.get("snapshot_ts")
    if not ts or "T" not in ts:
        return
    day = ts[:10]
    hhmm = ts[11:16].replace(":", "")
    rows = [r for r in d.get("ticker") or [] if r.get("qualified")]
    rows = sorted(rows, key=lambda r: r.get("GROUND") or 0, reverse=True)[:5]

    PICKS_DIR.mkdir(exist_ok=True)
    day_path = PICKS_DIR / f"{day}.json"
    payload = {"date": day, "scans": []}
    if day_path.exists():
        try:
            payload = json.loads(day_path.read_text())
        except json.JSONDecodeError:
            pass
    if any(s.get("hhmm") == hhmm for s in payload["scans"]):
        print(f"[snapshot_picks] scan {hhmm} already captured; skip")
        return
    picks = []
    for r in rows:
        p = {k: r.get(k) for k in PICK_FIELDS}
        p["entry_credit"] = round(float(r["net_credit"]) * FILL_FRAC, 4)
        p["outcome"] = None
        p["pnl"] = None
        p["expiry_close"] = None
        picks.append(p)
    payload["scans"].append({"hhmm": hhmm, "snapshot_ts": ts, "picks": picks})
    payload["scans"].sort(key=lambda s: s["hhmm"])
    day_path.write_text(json.dumps(payload, indent=1))
    print(f"[snapshot_picks] captured {len(picks)} qualified pick(s) at {hhmm}")


def _expiry_close(ticker: str, expiry: str):
    fp = BARS_DIR / f"{ticker}.csv"
    if not fp.exists():
        return None
    try:
        bars = pd.read_csv(fp, usecols=["date", "close"])
    except Exception:
        return None
    row = bars[bars["date"] == expiry]
    if row.empty:
        return None
    return float(row["close"].iloc[0])


def _connect_ib():
    """Open an IB connection for same-evening close lookups, or None if
    ib_insync/IB Gateway is unavailable. Distinct clientId so it never
    collides with the fetcher (11), daily-bars (13) or expire_frozen (201)."""
    try:
        from ib_insync import IB
    except ImportError:
        print("[snapshot_picks] ib_insync unavailable — cannot fetch live close")
        return None
    ib = IB()
    try:
        ib.connect(live_config.IB_HOST, live_config.IB_PORT,
                   clientId=live_config.IB_CLIENT_ID + 10, readonly=True)
    except Exception as e:
        print(f"[snapshot_picks] IB connect failed: {e}")
        return None
    ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
    return ib


def settle(post_close=False) -> None:
    """Settle expired snapshot picks.

    Default (intraday calls from pull_now_parallel): Yahoo daily-bar close,
    only for picks whose expiry is strictly before today ("settle the morning
    after"), since the same-day Yahoo EOD bar isn't reliably final intraday.

    post_close=True (cron_daily_bars at 17:01, after the official close):
    also settle picks expiring TODAY, sourcing today's RTH close live from IB
    (same source as expire_frozen) so the Snapshots tab settles the same
    evening as the History tab instead of lagging a day. Older expiries still
    use Yahoo — IB's last daily bar is always TODAY and would mis-settle them.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    ib = _connect_ib() if post_close else None
    ib_close_cache = {}
    try:
        _settle_files(today, ib, ib_close_cache)
    finally:
        if ib is not None:
            ib.disconnect()


def _settle_files(today, ib, ib_close_cache) -> None:
    for day_path in sorted(PICKS_DIR.glob("*.json")):
        try:
            payload = json.loads(day_path.read_text())
        except json.JSONDecodeError:
            continue
        changed = False
        for scan in payload.get("scans", []):
            for p in scan.get("picks", []):
                if p.get("outcome") is not None:
                    continue
                expiry = (p.get("expiry_date") or "")[:10]
                if not expiry or expiry > today:
                    continue  # not yet expired
                if expiry == today:
                    # Same-day settle only after the close, via live IB.
                    if ib is None:
                        continue
                    tk = p["ticker"]
                    if tk not in ib_close_cache:
                        from live import expire_frozen
                        ib_close_cache[tk] = expire_frozen._underlying_price(ib, tk)
                    spot = ib_close_cache[tk]
                else:  # expiry < today — morning-after Yahoo close
                    spot = _expiry_close(p["ticker"], expiry)
                if spot is None:
                    continue
                ss, ls = float(p["short_strike"]), float(p["long_strike"])
                width = float(p["net_credit"]) + float(p["max_loss"])
                credit = float(p["entry_credit"])
                ml_adj = width - credit
                pnl = spreads.calc_pnl(spot, ss, ls, credit, ml_adj,
                                       p["spread_type"]) * 100
                if p["spread_type"] == "bull_put":
                    oc = "WIN" if spot > ss else ("LOSS" if spot <= ls else "PARTIAL")
                else:
                    oc = "WIN" if spot < ss else ("LOSS" if spot >= ls else "PARTIAL")
                if oc == "PARTIAL" and pnl > 0:
                    pnl *= 0.5  # partial-WIN haircut, matches backtest canon
                p["outcome"] = oc
                p["pnl"] = round(float(pnl), 2)
                p["expiry_close"] = round(spot, 2)
                changed = True
        if changed:
            day_path.write_text(json.dumps(payload, indent=1))
            print(f"[snapshot_picks] settled picks in {day_path.name}")


if __name__ == "__main__":
    capture(Path(live_config.RANKED_DIR) / "latest.json")
    settle()
