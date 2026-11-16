"""Session close for a given day. Standard library only: Mya's bare python runs
win_alert.py and the Mac's cron shell calls the CLI below.

NYSE closes at 16:00 ET, or 13:00 ET on the early-close days listed here
(eligible options 13:15). On those days every job keeps its distance from the
bell (user 2026-11-16: "fix the jobs for early close handling"): the 15:15
freeze and the 15:30 / 15:45 top-ups become 12:15, 12:30 and 12:45, the live
board stops at the 12:45 scan, the close alert fires at 12:01, and "after the
bell" starts at 13:00.

CLI (run from the repo root):
    python3 -m live.market_hours --close          # 16:00 or 13:00
    python3 -m live.market_hours --shift 15:15    # 15:15 or 12:15
    python3 -m live.market_hours --shift-hour 15  # 15 or 12
    python3 -m live.market_hours --after-close    # exit 0 once the bell has rung
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Source: nyse.com/markets/hours-calendars, read 2026-11-16. Add each new year.
EARLY_CLOSES = {"2026-11-27", "2026-12-24", "2027-11-26"}

OPEN_HHMM = "09:30"
NORMAL_CLOSE_HHMM = "16:00"
EARLY_CLOSE_HHMM = "13:00"
_SHIFT_MIN = 180          # NORMAL_CLOSE - EARLY_CLOSE


def _iso(day=None) -> str:
    if day is None:
        return datetime.now(ET).date().isoformat()
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    return str(day)[:10]


def is_early_close(day=None) -> bool:
    return _iso(day) in EARLY_CLOSES


def close_hhmm(day=None) -> str:
    """The closing bell on ``day`` (default today, ET), "HH:MM"."""
    return EARLY_CLOSE_HHMM if is_early_close(day) else NORMAL_CLOSE_HHMM


def shift(hhmm: str, day=None) -> str:
    """A normal-day clock time moved to keep its distance from the bell on
    ``day``: unchanged on a 16:00 day, three hours earlier on an early close.
    Keeps the input's format ("15:15" -> "12:15", "1515" -> "1215")."""
    if not is_early_close(day):
        return hhmm
    s = str(hhmm)
    colon = ":" in s
    digits = s.replace(":", "")
    total = int(digits[:2]) * 60 + int(digits[2:4]) - _SHIFT_MIN
    out = f"{total // 60:02d}{':' if colon else ''}{total % 60:02d}"
    return out + digits[4:] if not colon else out + s[5:]


def in_session(ts) -> bool:
    """True when a naive ET timestamp ("YYYY-MM-DDTHH:MM:SS") is inside that
    day's regular session, 09:30 <= t < the bell."""
    s = str(ts or "")
    t = s[11:19]
    return bool(t) and f"{OPEN_HHMM}:00" <= t < f"{close_hhmm(s[:10])}:00"


def after_close(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    return now.strftime("%H:%M") >= close_hhmm(now)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--close", action="store_true")
    ap.add_argument("--shift")
    ap.add_argument("--shift-hour", type=int)
    ap.add_argument("--after-close", action="store_true")
    a = ap.parse_args()
    if a.after_close:
        sys.exit(0 if after_close() else 1)
    if a.shift:
        print(shift(a.shift))
    elif a.shift_hour is not None:
        print(int(shift(f"{a.shift_hour:02d}:00")[:2]))
    else:
        print(close_hhmm())
