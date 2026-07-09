"""Shared NYSE weekly-expiry calendar helpers.

The canonical weekly option expires Friday. When that Friday is an NYSE
full-close holiday (e.g. Juneteenth, Good Friday), the contract instead
expires the prior trading day (Thursday). The fetcher already rolls picks
back this way; the expiry-side crons (close_alert, track_expiring, expire)
need the same awareness so they fire on the real settlement day instead of
a holiday Friday when the market is closed and positions have already expired.

CLI guard for bash crons:
    python3 -m live.trading_calendar --is-settlement-day
exits 0 if today is the week's settlement day, 1 otherwise.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import spreads

_NYSE_HOLIDAY_SET = set(spreads.NYSE_HOLIDAYS)


def roll_back_to_trading_day(d: datetime.date) -> datetime.date:
    """Roll a date back to the prior open trading day if it lands on a
    weekend or an NYSE full-close holiday."""
    while d.weekday() >= 5 or pd.Timestamp(d) in _NYSE_HOLIDAY_SET:
        d -= datetime.timedelta(days=1)
    return d


def weekly_settlement_day(today: datetime.date | None = None) -> datetime.date:
    """Return the settlement/expiry day for the Mon–Fri week containing
    ``today`` — this week's Friday rolled back over holidays/weekends."""
    if today is None:
        today = datetime.date.today()
    friday = today + datetime.timedelta(days=(4 - today.weekday()))
    return roll_back_to_trading_day(friday)


def is_settlement_day(today: datetime.date | None = None) -> bool:
    if today is None:
        today = datetime.date.today()
    return today == weekly_settlement_day(today)


def week_notice(today: datetime.date | None = None) -> dict:
    """Describe the current Mon–Fri week's expiry. ``shifted`` is True when this
    week's nominal Friday is an NYSE holiday so the weekly settles earlier
    (e.g. Juneteenth → Thursday close). The webapp uses this to warn that the
    closing day is not the usual Friday."""
    if today is None:
        today = datetime.date.today()
    friday = today + datetime.timedelta(days=(4 - today.weekday()))
    settle = roll_back_to_trading_day(friday)
    return {
        "shifted": settle != friday,
        "nominal_friday": friday.isoformat(),
        "settlement_date": settle.isoformat(),
        "settlement_weekday": settle.strftime("%A"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--is-settlement-day", action="store_true",
                        help="exit 0 if today is the week's settlement day, else 1")
    args = parser.parse_args()
    if args.is_settlement_day:
        sys.exit(0 if is_settlement_day() else 1)
    print(weekly_settlement_day().isoformat())
