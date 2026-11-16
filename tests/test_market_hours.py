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


def test_backup_rerun_only_for_an_empty_in_session_scan(tmp_path):
    import json
    from live import scan_backup as sb
    p = tmp_path / "latest.json"

    def write(ts, n):
        p.write_text(json.dumps({"snapshot_ts": ts, "ticker": [{}] * n}))

    start = datetime(2026, 11, 16, 9, 30, 1)
    write("2026-11-16T09:32:35", 0);  assert sb.needs_rerun(start, p)[0]          # ranked nothing
    write("2026-11-16T09:32:35", 82); assert not sb.needs_rerun(start, p)[0]
    write("2026-11-13T15:46:41", 30); assert sb.needs_rerun(start, p)[0]          # scan wrote no ranking
    write("2026-11-16T09:16:00", 0)
    assert not sb.needs_rerun(datetime(2026, 11, 16, 9, 15, 0), p)[0]             # pre-open
    assert not sb.needs_rerun(datetime(2026, 11, 16, 16, 0, 0), p)[0]             # after the bell
    assert not sb.needs_rerun(datetime(2026, 11, 27, 13, 0, 0), p)[0]             # early-close bell
    assert not sb.needs_rerun(datetime(2026, 11, 14, 10, 0, 0), p)[0]             # Saturday
