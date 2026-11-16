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


from live.market_hours import EARLY_CLOSES   # 1:00 pm ET closes (options 1:15 pm)


def upcoming_settlement_day(today: datetime.date | None = None) -> datetime.date:
    """The next weekly settlement day on or after ``today`` (this week's, or
    next week's once this week's has passed, e.g. on a weekend)."""
    if today is None:
        today = datetime.date.today()
    settle = weekly_settlement_day(today)
    if settle < today:
        monday = today + datetime.timedelta(days=7 - today.weekday())
        settle = weekly_settlement_day(monday)
    return settle


def expiry_notice(expiry_dates, today: datetime.date | None = None) -> dict:
    """Expiry day(s) of the live list, for the live tab's header (user
    2026-11-16: write the expiry day, highlight it when it is not a Friday).

    ``expiry_dates`` are the rows' own expiry dates (ISO strings, from the
    option chain). Dates still to come are reported as-is, ``source`` "rows".
    When the list is empty or already expired (frozen board before the next
    scan), the upcoming weekly settlement day stands in, ``source`` "calendar".
    """
    if today is None:
        today = datetime.date.today()
    live = set()
    for e in expiry_dates or []:
        try:
            d = datetime.date.fromisoformat(str(e)[:10])
        except ValueError:
            continue
        if d >= today:
            live.add(d)
    source = "rows" if live else "calendar"
    if not live:
        live = {upcoming_settlement_day(today)}
    out = []
    for d in sorted(live):
        friday = d + datetime.timedelta(days=(4 - d.weekday()))
        out.append({
            "date": d.isoformat(),
            "weekday": d.strftime("%A"),
            "is_friday": d.weekday() == 4,
            "friday_holiday": d.weekday() != 4 and pd.Timestamp(friday) in _NYSE_HOLIDAY_SET,
            "nominal_friday": friday.isoformat(),
            "early_close": d.isoformat() in EARLY_CLOSES,
            "days_away": (d - today).days,
        })
    return {"source": source, "expiries": out}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--is-settlement-day", action="store_true",
                        help="exit 0 if today is the week's settlement day, else 1")
    args = parser.parse_args()
    if args.is_settlement_day:
        sys.exit(0 if is_settlement_day() else 1)
    print(weekly_settlement_day().isoformat())
