"""Debug case 3: LTP 100.5 should hold - what fires?"""
import sys, os, types, tempfile, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from zoneinfo import ZoneInfo
import proxy.config as cfg
from proxy.engine import PaperEngine
from proxy.tracker import Tracker
from proxy.notifier import Notifier

lc = types.SimpleNamespace(**vars(cfg))
lc.NO_STOP_LOSS = False
lc.MAX_UNARMED_BARS = 4

class B:
    live = True
    def place_order(self, side, instrument, quantity, **kw):
        return {"orderStatus": "TRADED", "orderId": "X1"}

tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); tmp.close()
e = PaperEngine(lc, broker=B(), tracker=Tracker(lc, db_path=tmp.name),
                notifier=Notifier(quiet=True), trade_date=datetime(2026, 9, 3).date())
e.entry_ltp_fn = lambda sid: e._ltp

trade = {"instrument": "NIFTY 08SEP 24050 PE", "direction": "LONG", "option_type": "PE",
         "strike": 24050.0, "security_id": 42650, "lots": 4, "quantity": 260,
         "entry_premium": 100.0, "stop_premium": 95.0, "target_premium": 106.5,
         "entry_spot": 24000.0, "entry_time": "2026-09-03T09:20:00+05:30",
         "bars_held": 1, "lock_enabled": True, "pnl_peak": 100.0,
         "lock_armed": False, "premium_source": "real_option_bar"}
e.active_trade = dict(trade)
e._ltp = 100.5
try:
    rec = e.check_live_ltp_exit()
    print("check_live_ltp_exit ->", rec)
except Exception:
    traceback.print_exc()
