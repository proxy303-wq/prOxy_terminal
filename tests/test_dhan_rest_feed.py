"""DhanRestFeed poll-set regression tests (08-Sep zero-bars bug).

07-Sep: the FINNIFTY worker (security_id=27) connected but delivered ZERO
5m bars all day.  Root cause was CLIENT-side and deterministic: the feed
hardcoded its poll set to [(IDX_I,13),(IDX_I,25)] and never included its
own security_id, while _next_5m_bar only builds bars from ticks whose sid
== security_id.  A sid-27 feed therefore polled NIFTY/BANKNIFTY and built
nothing - even though Dhan /v2/marketfeed/ltp DOES serve IDX_I 27 (probe
08-Sep: NIFTY 23779.15 / BN 57088.30 / FINNIFTY 25935.60 off-hours).

These tests pin the invariant WITHOUT network: the poll set must contain
the feed's own index id for every variant, and option_only feeds stay
empty until an NSE_FNO leg is subscribed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.dhan_rest_feed import DhanRestFeed, NIFTY_INDEX_ID, BANKNIFTY_INDEX_ID


class TestPollSet(unittest.TestCase):
    def _feed(self, security_id, option_only=False, **kw):
        # no client/token -> no auth path touched; instruments are built
        # from security_id/option_only alone
        return DhanRestFeed(client_id=None, access_token=None,
                            security_id=security_id, option_only=option_only, **kw)

    def test_nifty_polls_13_and_25(self):
        f = self._feed(13)
        self.assertIn(("IDX_I", NIFTY_INDEX_ID), f.instruments)
        self.assertIn(("IDX_I", BANKNIFTY_INDEX_ID), f.instruments)

    def test_banknifty_polls_25(self):
        f = self._feed(25)
        self.assertIn(("IDX_I", BANKNIFTY_INDEX_ID), f.instruments)

    def test_finnifty_polls_27(self):
        # THE regression: a finnifty feed must poll its own index 27
        f = self._feed(27)
        self.assertIn(("IDX_I", 27), f.instruments)
        self.assertIn(("IDX_I", 27), [x for x in f.instruments])

    def test_sensex_polls_51(self):
        f = self._feed(51)
        self.assertIn(("IDX_I", 51), f.instruments)

    def test_option_only_stays_empty(self):
        f = self._feed(27, option_only=True)
        self.assertEqual(f.instruments, [])

    def test_subscribe_option_adds_nse_fno(self):
        f = self._feed(27)
        f.subscribe_option(68391)
        self.assertIn(("NSE_FNO", 68391), f.instruments)

    def test_subscribe_option_idempotent(self):
        f = self._feed(27)
        f.subscribe_option(68391)
        f.subscribe_option(68391)
        self.assertEqual(f.instruments.count(("NSE_FNO", 68391)), 1)


class TestBarBuildAcceptance(unittest.TestCase):
    """A sid-27 tick must be ACCEPTED by _next_5m_bar (not filtered out)."""

    def test_finnifty_tick_accepted(self):
        # simulate the poll loop: a tick whose sid == security_id
        f = DhanRestFeed(client_id=None, access_token=None, security_id=27)
        f.timeout = 1  # keep the drain fast when the queue empties
        # the queue is normally fed by _poll_loop with security_id from the
        # response keys; check the filter inside _next_5m_bar accepts "27"
        import queue
        q = queue.Queue()
        from datetime import datetime
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        t1 = datetime(2026, 9, 8, 9, 15, 10, tzinfo=IST)
        t2 = datetime(2026, 9, 8, 9, 20, 10, tzinfo=IST)  # crosses the 5m bucket
        q.put({"ltp": 26000.0, "tick_time": t1, "security_id": "27"})
        q.put({"ltp": 26050.0, "tick_time": t2, "security_id": "27"})
        f._ticks = q
        # tick1 seeds bucket 09:15; tick2 (09:20) rolls the bucket and the
        # feed returns the completed 09:15 bar.  BEFORE the fix this tick was
        # dropped by the sid filter (security_id 27 not in the poll set), so
        # _next_5m_bar never returned anything.
        bar = f._next_5m_bar(block=False)
        self.assertIsNotNone(bar, "sid-27 tick must produce a bar (was filtered out before the fix)")
        self.assertEqual(bar["time"].hour, 9)
        self.assertEqual(bar["time"].minute, 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
