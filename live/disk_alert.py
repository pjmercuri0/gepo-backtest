#!/usr/bin/env python3
"""Disk alert (user 2026-11-11: "make sure mya alerts me when we get over like 75%").

Writes ONE notification file per day into live/notifications/ while the disk holding --path is at
--pct or more; Mya's notify_watcher.sh forwards any file with a "message" to Telegram. Runs on both
machines: Mya calls it from notify_watcher.sh (her own disk), the Mac mini from pull_now_parallel.sh
(its disk; the file rides the next upload). Stdlib only (Mya is python3.10, no venv).

  python3 live/disk_alert.py --host "Mac mini" [--path /] [--pct 75] [--notif-dir DIR]
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="name in the alert, e.g. 'Mya' or 'Mac mini'")
    ap.add_argument("--path", default="/", help="a path on the disk to measure")
    ap.add_argument("--pct", type=float, default=float(os.environ.get("GEPO_DISK_ALERT_PCT", 75)))
    ap.add_argument("--notif-dir", default=os.environ.get("GEPO_NOTIF_DIR", os.path.join(HERE, "notifications")))
    a = ap.parse_args()

    u = shutil.disk_usage(a.path)
    pct = u.used / u.total * 100 if u.total else 0.0
    free_gb = u.free / 1e9
    slug = re.sub(r"[^a-z0-9]+", "_", a.host.lower()).strip("_")
    today = dt.date.today().isoformat()
    os.makedirs(a.notif_dir, exist_ok=True)
    ledger = os.path.join(a.notif_dir, f".disk_alerted_{slug}")
    print(f"[disk_alert] {a.host}: {pct:.1f}% used, {free_gb:.1f} GB free (alert at {a.pct:.0f}%)")
    if pct < a.pct:
        return 0
    try:
        if open(ledger).read().strip() == today:          # one alert per day while above the line
            return 0
    except OSError:
        pass
    msg = (f"⚠️ {a.host} disk at {pct:.0f}% ({free_gb:.1f} GB free), alert line {a.pct:.0f}%.\n"
           f"Prune logs or data before it fills.")
    fn = os.path.join(a.notif_dir, f"disk_alert_{slug}_{today}.json")
    with open(fn, "w") as f:
        json.dump({"type": "disk-alert", "host": a.host, "ts": dt.datetime.now().isoformat(timespec="seconds"),
                   "pct": round(pct, 1), "free_gb": round(free_gb, 1), "message": msg}, f, indent=2)
    with open(ledger, "w") as f:
        f.write(today)
    print(f"[disk_alert] wrote {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
