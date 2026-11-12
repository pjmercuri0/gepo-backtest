"""06:05 ET Telegram status: the open Actuals rows with their cushion to the short strike, exactly the
text Mya forwards (Vito job daily-605am-gepo-status on Mya, user 2026-11-12: "The STALE alert seems
broken still referring to credit"). Until today that job read the CREDIT book (live/actuals.json) and
let the model format the rows; it now runs this script and relays stdout verbatim.

Reads the site the way the /actuals page does (webapp._actuals_view, debit site unless GEPO_SITE says
otherwise) so the spot is the streamed spot the page shows. One row per open trade:

  TICKER BC long/short sp X.XX +/-cushion     debit bull call (parity twin of the bull put)
  TICKER BP long/short sp X.XX +/-cushion     debit bear put  (parity twin of the bear call)

cushion = spot - short strike for a bull spread, short strike - spot for a bear spread: positive when
the spot is past the short strike (the spread at full value), negative when it is short of it. Rows
sort by cushion, best first. If the last scan is older than the previous trading day the first line is
"STALE data_date/snapshot_ts"; the previous session's 16:00 scan is current at 06:05.

  GEPO_SITE=debit python3 live/morning_status.py
"""
from __future__ import annotations
import json, os, sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("GEPO_SITE", "debit")      # Mya's jobs point at the debit site (user 2026-11-10)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live import live_config, webapp                    # noqa: E402
from live.trading_calendar import roll_back_to_trading_day  # noqa: E402


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _spot(r: dict):
    for src in (r.get("last_track") or {}, r.get("pick") or {}):
        s = _f(src.get("underlying_price"))
        if s is not None and s > 0:
            return s
    return None


def main() -> int:
    today = date.today()
    rows, _risk, stream = webapp._actuals_view()
    out = []
    for r in rows:
        if r.get("closed") or (r.get("outcome_row") or {}).get("result"):
            continue
        p = r.get("pick") or {}
        exp = str(p.get("expiry_date") or "")[:10]
        if not exp or exp < today.isoformat():
            continue
        st = p.get("spread_type") or ""
        debit = p.get("structure") == "debit"
        ks, kl = _f(p.get("short_strike")), _f(p.get("long_strike"))
        spot = _spot(r)
        if ks is None or kl is None or spot is None:
            continue
        bull = st == "bull_put"
        cushion = (spot - ks) if bull else (ks - spot)
        if debit:
            tag = "BC" if bull else "BP"                 # bull call / bear put
            strikes = f"{kl:.2f}/{ks:.2f}"               # long/short, as the Actuals row prints them
        else:
            tag = "BP" if bull else "BC"                 # credit book rows: bull put / bear call
            strikes = f"{ks:.2f}/{kl:.2f}"
        out.append((cushion, f"{p.get('ticker')} {tag} {strikes} sp {spot:.2f} {cushion:+.2f}"))
    out.sort(key=lambda t: -t[0])

    lines = []
    meta = {}
    try:
        meta = json.loads((Path(live_config.RANKED_DIR) / "latest.json").read_text())
    except (OSError, json.JSONDecodeError):
        pass
    data_date = str(meta.get("data_date") or "")[:10]
    prev_session = roll_back_to_trading_day(today - timedelta(days=1)).isoformat()
    if not data_date or data_date < prev_session:
        lines.append(f"STALE {data_date or '?'}/{meta.get('snapshot_ts') or '?'}")
    lines += [s for _, s in out] or ["no open actuals"]
    ts = str(stream.get("ts") or "")
    if ts:
        try:
            lines.append(f"sp {datetime.fromisoformat(ts):%H:%M} ET")
        except ValueError:
            pass
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
