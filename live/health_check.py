"""Silent-fail health check for the SPY intraday cron.

Cron-driven, runs every 5 min during market hours. Logic:

  Let `age` = current time minus mtime of live/ranked/spy_intraday.json.

  If age > STALE_THRESHOLD_MIN and we're inside US market hours on a weekday:
    → emit an alert JSON to live/notifications/health-<timestamp>.json
    → emit a one-line summary to live/logs/health.log
    → exit 0 (no shell error — we've handled the failure)

  Otherwise: silent no-op.

Mya's server watches live/notifications/ and ships the alert via her usual
push/email channel. The alert is intentionally throttled: we only emit one
new file per cron firing where the condition is true; Mya can de-dup
on the server side if needed.

Run:
  python3 -m live.health_check                 # one check, exit
  python3 -m live.health_check --force         # emit an alert even if fresh
                                               # (useful for testing the pipeline)
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import sys
import tempfile
from datetime import datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config


SPY_PATH         = Path(live_config.RANKED_DIR) / "spy_intraday.json"
NOTIFICATIONS    = Path(live_config.NOTIFICATIONS_DIR)
LOG_PATH         = Path(live_config.LOGS_DIR) / "health.log"
STALE_THRESHOLD_MIN = 60


# Market hours (US Eastern). Keep this tight so Mya does not receive stale
# alerts after the tradeable session; normal expiry/settlement jobs handle
# post-close state separately.
MARKET_OPEN  = dtime(hour=9,  minute=30)
MARKET_CLOSE = dtime(hour=16, minute=5)


def _in_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:           # 5=Sat, 6=Sun
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _spy_mtime() -> datetime | None:
    if not SPY_PATH.exists():
        return None
    return datetime.fromtimestamp(SPY_PATH.stat().st_mtime)


def _spy_has_quote() -> bool:
    try:
        with open(SPY_PATH) as f:
            payload = json.load(f)
        return payload.get("mark") is not None
    except (OSError, json.JSONDecodeError):
        return False


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _append_log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def emit_alert(reason: str, age_min: float | None, last_tick_ts: str | None) -> Path:
    now = datetime.now()
    payload = {
        "type":          "health-alert",
        "ts":            now.isoformat(timespec="seconds"),
        "host":          socket.gethostname(),
        "reason":        reason,
        "stale_min":     round(age_min, 1) if age_min is not None else None,
        "last_tick_ts":  last_tick_ts,
        "threshold_min": STALE_THRESHOLD_MIN,
        "spy_path":      str(SPY_PATH.relative_to(ROOT)),
        # Mya-facing summary string (sent verbatim to push/email)
        "message": (
            f"⚠️ gepo SPY fetcher stale: {round(age_min,1) if age_min else '∞'} min "
            f"since last successful tick ({last_tick_ts or 'never'}). "
            f"Check IB Gateway connection on Dr. Peter's Mac."
        ),
    }
    out = NOTIFICATIONS / f"health-{now.strftime('%Y%m%d-%H%M%S')}.json"
    _atomic_write_json(out, payload)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Emit an alert regardless of staleness (for testing)")
    args = parser.parse_args()

    now = datetime.now()
    mtime = _spy_mtime()

    if args.force:
        age = (now - mtime).total_seconds() / 60 if mtime else None
        out = emit_alert("forced (test)", age, mtime.isoformat() if mtime else None)
        _append_log(f"{now.isoformat(timespec='seconds')}  FORCED  → {out.name}")
        print(f"  ✓ wrote {out}")
        return 0

    if not _in_market_hours(now):
        # silent no-op outside hours
        return 0

    if mtime is None:
        out = emit_alert("no SPY snapshot file on disk", None, None)
        _append_log(f"{now.isoformat(timespec='seconds')}  ALERT (no file)  → {out.name}")
        print(f"  ✗ no SPY snapshot file; wrote alert {out}")
        return 0

    if not _spy_has_quote():
        out = emit_alert("SPY snapshot has no quote", None, mtime.isoformat())
        _append_log(f"{now.isoformat(timespec='seconds')}  ALERT (empty quote)  → {out.name}")
        print(f"  ✗ SPY snapshot has no quote; wrote alert {out}")
        return 0

    age_min = (now - mtime).total_seconds() / 60
    if age_min > STALE_THRESHOLD_MIN:
        out = emit_alert(f"stale by {round(age_min,1)} min", age_min, mtime.isoformat())
        _append_log(f"{now.isoformat(timespec='seconds')}  ALERT (stale {age_min:.1f}m)  → {out.name}")
        print(f"  ✗ SPY snapshot stale by {age_min:.1f} min; wrote alert {out}")
        return 0

    # Fresh — silent no-op
    return 0


if __name__ == "__main__":
    sys.exit(main())
