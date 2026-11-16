"""Backup check for an empty scan (user 2026-11-16: "if there are ever 0
candidates then need to do a rerun in 5 minutes").

The 09:30 scan on 2026-11-16 ranked 0 candidates: the fetchers hit their time
cap looking up the new week's contracts and the board stayed empty until the
next quarter hour. cron_parallel.sh asks this after each scan and, on exit 0,
waits five minutes and runs the scan once more.

    python3 -m live.scan_backup --since <epoch the scan started>
exit 0 = rerun needed: the scan started inside the regular session and left no
ranked candidates (latest.json not rewritten by it, or written with 0 rows).
exit 1 = nothing to do.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config, market_hours


def needs_rerun(since: datetime, latest_path: Path | None = None) -> tuple[bool, str]:
    """``since``: naive ET start time of the scan that just finished."""
    if since.weekday() >= 5 or not market_hours.in_session(since.isoformat(timespec="seconds")):
        return False, "scan started outside the session"
    path = latest_path or Path(live_config.RANKED_DIR) / "latest.json"
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return True, f"{path.name} unreadable"
    ts = str(d.get("snapshot_ts") or "")
    if ts[:19] < since.isoformat(timespec="seconds"):
        return True, f"no ranking from this scan (latest is {ts or 'missing'})"
    n = len(d.get("ticker") or [])
    return (n == 0), f"{n} candidates"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, required=True, help="epoch seconds the scan started")
    a = ap.parse_args()
    start = datetime.fromtimestamp(a.since, market_hours.ET).replace(tzinfo=None)
    rerun, why = needs_rerun(start)
    print(f"[backup] {'RERUN' if rerun else 'ok'}: {why}", flush=True)
    sys.exit(0 if rerun else 1)
