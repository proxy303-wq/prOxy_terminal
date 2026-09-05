"""Verify check_live_ltp_exit: fires on a live LTP crossing the floor."""
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import proxy.config as cfg
from proxy.engine import PaperEngine
from proxy.tracker import Tracker
from proxy.notifier import Notifier


class LiveBrokerStub:
    live = True
    filled = True

    def place_order(self, side, instrument, quantity, **kw):
        self.calls = getattr(self, "calls", [])
        self.calls.append((side, instrument, quantity))
        return {"orderStatus": "TRADED", "orderId": "X1"}


class NotFillingStub(LiveBrokerStub):
    filled = False
    def place_order(self, side, instrument, quantity, **kw):
        return {"orderStatus": "REJECTED"}


def live_cfg():
    c = types.SimpleNamespace(**vars(cfg))
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    return c


def mk_engine(broker):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    tr = Tracker(live_cfg(), db_path=tmp.name)
    e = PaperEngine(live_cfg(), broker=broker, tracker=tr, notifier=Notifier(quiet=True),
                    trade_date=datetime(2026, 9, 3).date())
    e.entry_ltp_fn = lambda sid: e._ltp
    return e, tmp.name


IST = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 9, 3, 10, 30, tzinfo=IST)   # before 15:15 so time-stop doesn't fire
trade = {"instrument": "NIFTY 08SEP 24050 PE", "direction": "LONG", "option_type": "PE",
         "strike": 24050.0, "security_id": 42650, "lots": 4, "quantity": 260,
         "entry_premium": 100.0, "stop_premium": 95.0, "target_premium": 106.5,
         "entry_spot": 24000.0, "entry_time": "2026-09-03T09:20:00+05:30",
         "bars_held": 1, "lock_enabled": True, "pnl_peak": 100.0,
         "lock_armed": False, "premium_source": "real_option_bar"}

# 1) no trade -> None
e, p = mk_engine(LiveBrokerStub())
assert e.check_live_ltp_exit(MORNING) is None, "no-trade must be None"
print("1. no trade -> None  OK")

# 2) paper broker -> None
e2, p2 = mk_engine(LiveBrokerStub())
e2.broker.live = False
e2.active_trade = dict(trade)
assert e2.check_live_ltp_exit(MORNING) is None, "paper must be None"
print("2. paper mode -> None  OK")

# 3) live broker, trade far from levels -> hold
e3, p3 = mk_engine(LiveBrokerStub())
e3.active_trade = dict(trade)
e3._ltp = 100.5   # near entry, not crossing anything
assert e3.check_live_ltp_exit(MORNING) is None, "hold expected"
print("3. LTP 100.5 (no cross) -> hold  OK")

# 4) live broker, LTP crosses the unarmed stop -> STOP exit fires
e4, p4 = mk_engine(LiveBrokerStub())
t4 = dict(trade)
e4.active_trade = t4
e4._ltp = 94.5    # below the 95 stop
rec = e4.check_live_ltp_exit(MORNING)
assert rec is not None and "STOP_LOSS_HIT" in rec["exit_reason"], rec
print("4. LTP 94.5 crosses stop ->", rec["exit_reason"], "OK")

# 5) armed trade, LTP dips to the floor -> LOCK_PROFIT fires immediately
e5, p5 = mk_engine(LiveBrokerStub())
t5 = dict(trade)
t5["pnl_peak"] = 105.0     # rode to +5pt
t5["lock_armed"] = True
e5.active_trade = t5
e5._ltp = 103.5            # floor = max(+1pt, peak-1pt=104) -> 103.5 < 104 crosses
rec5 = e5.check_live_ltp_exit(MORNING)
assert rec5 is not None and "LOCK_PROFIT" in rec5["exit_reason"], rec5
print("5. armed, LTP 103.5 < floor 104 ->", rec5["exit_reason"], "OK")

# 6) rejected exit order -> trade stays open, returns None
e6, p6 = mk_engine(NotFillingStub())
t6 = dict(trade)
e6.active_trade = t6
e6._ltp = 94.5
rec6 = e6.check_live_ltp_exit(MORNING)
assert rec6 is None and e6.active_trade is not None, "rejected must keep trade open"
print("6. rejected order -> trade stays open, returns None  OK")

print("\nALL PASS")
for path in (p, p2, p3, p4, p5, p6):
    try:
        os.remove(path)
    except OSError:
        pass
