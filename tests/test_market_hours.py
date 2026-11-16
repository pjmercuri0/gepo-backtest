"""Early-close days keep every job's distance from the bell (user 2026-11-16)."""
from datetime import date, datetime

from live import market_hours as mh
from live import trading_calendar as tc


def test_normal_day_is_unchanged():
    d = "2026-11-20"
    assert mh.close_hhmm(d) == "16:00"
    assert [mh.shift(t, d) for t in ("15:15", "15:30", "15:45", "1500")] == ["15:15", "15:30", "15:45", "1500"]
    assert mh.in_session("2026-11-20T15:59:59") and not mh.in_session("2026-11-20T16:00:00")


def test_early_close_moves_three_hours():
    for d in ("2026-11-27", "2026-12-24", "2027-11-26"):
        assert mh.close_hhmm(d) == "13:00"
        assert [mh.shift(t, d) for t in ("15:15", "15:30", "15:45", "1500", "16:05")] == \
            ["12:15", "12:30", "12:45", "1200", "13:05"]
    assert mh.in_session("2026-11-27T12:59:59") and not mh.in_session("2026-11-27T13:00:00")
    assert mh.shift("15:15", date(2026, 11, 27)) == mh.shift("15:15", datetime(2026, 11, 27, 9, 0)) == "12:15"


def test_expiry_notice_flags_holiday_weeks():
    def one(day):
        return tc.expiry_notice([], date.fromisoformat(day))["expiries"][0]
    assert one("2026-11-16")["date"] == "2026-11-20" and one("2026-11-16")["is_friday"]
    e = one("2026-11-23"); assert e["date"] == "2026-11-27" and e["is_friday"] and e["early_close"]
    e = one("2026-12-21"); assert e["date"] == "2026-12-24" and not e["is_friday"] and e["early_close"]
    e = one("2026-12-28"); assert e["date"] == "2026-12-31" and e["friday_holiday"]
    # the rows' own expiry wins while it is still to come
    assert tc.expiry_notice(["2026-11-20T00:00:00"], date(2026, 11, 16))["source"] == "rows"
