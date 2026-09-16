"""The NSE holiday calendar behind is_trading_day (proxy/scheduler.py).

Written after 2026-09-14 (Ganesh Chaturthi): the worker opened a full session on
a market holiday, polled Dhan all morning and only at 15:15 reported
"no bars received - session skipped".  A holiday must read as CLOSED.
"""
import os
import sys
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy.scheduler import (  # noqa: E402
    HOLIDAY_CALENDAR_YEARS, NSE_HOLIDAYS, has_holiday_calendar, holiday_name,
    is_holiday, is_trading_day, next_market_open,
)

IST = ZoneInfo("Asia/Kolkata")


def ist(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


class TestHolidayCalendar(unittest.TestCase):
    def test_the_2026_circular_has_fifteen_weekday_closures(self):
        self.assertEqual(len(NSE_HOLIDAYS), 15)
        self.assertEqual(HOLIDAY_CALENDAR_YEARS, (2026,))
        for d in NSE_HOLIDAYS:
            self.assertLess(d.weekday(), 5, "%s is a weekend" % d)

    def test_every_announced_holiday_is_closed_and_named(self):
        for d, name in NSE_HOLIDAYS.items():
            self.assertFalse(is_trading_day(d), name)
            self.assertTrue(is_holiday(d))
            self.assertEqual(holiday_name(d), name)

    def test_ganesh_chaturthi_2026_09_14_reads_as_a_holiday(self):
        self.assertEqual(holiday_name(ist(2026, 9, 14, 10, 0)), "Ganesh Chaturthi")
        self.assertFalse(is_trading_day(ist(2026, 9, 14, 10, 0)))

    def test_a_normal_weekday_is_still_a_trading_day(self):
        self.assertTrue(is_trading_day(ist(2026, 9, 15)))
        self.assertIsNone(holiday_name(ist(2026, 9, 15)))
        self.assertFalse(is_holiday(ist(2026, 9, 15)))

    def test_weekends_stay_closed(self):
        self.assertFalse(is_trading_day(date(2026, 9, 12)))   # Saturday
        self.assertFalse(is_trading_day(date(2026, 9, 13)))   # Sunday

    def test_next_market_open_skips_a_holiday(self):
        self.assertEqual(next_market_open(ist(2026, 9, 14, 10, 0)),
                         ist(2026, 9, 15, 9, 15))

    def test_next_market_open_clears_a_holiday_weekend(self):
        # Friday 2026-10-02 (Gandhi Jayanti) + the weekend -> Monday 10-05
        self.assertEqual(next_market_open(ist(2026, 10, 2, 10, 0)),
                         ist(2026, 10, 5, 9, 15))

    def test_muhurat_sunday_stays_closed(self):
        # Diwali Laxmi Pujan: a Sunday, Muhurat session only - not our session
        self.assertEqual(holiday_name(date(2026, 11, 8)), None)
        self.assertFalse(is_trading_day(date(2026, 11, 8)))

    def test_an_uncovered_year_is_declared_not_guessed(self):
        self.assertTrue(has_holiday_calendar(2026))
        self.assertFalse(has_holiday_calendar(2027))
        # 2027-01-26 will be Republic Day, but with no table we only know the
        # weekday rule - the helper exists so callers can tell the difference.
        self.assertTrue(is_trading_day(ist(2027, 1, 26)))


if __name__ == "__main__":
    unittest.main()
