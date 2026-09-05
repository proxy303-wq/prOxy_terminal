"""Hermetic checks: WS non-blocking drain + REST option-only feed."""
import sys, os, queue, threading, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.dhan_auth as _auth
_auth.resolve_token_safe = lambda cid, notify=None: ("x", "fake")  # no network

from proxy.dhan_live import DhanLiveFeed
from proxy.dhan_rest_feed import DhanRestFeed

# 1) WS _next_5m_bar(block=False) must return immediately on an empty queue
f = object.__new__(DhanLiveFeed)
f._ticks = queue.Queue()
f._closed = False
f.max_idle_seconds = 300
f.timeout = 30
f._bar_lock = threading.Lock()
f._bar_accum = None
f._bar_start = None
t0 = time.time()
assert f._next_5m_bar(block=False) is None
dt = time.time() - t0
assert dt < 0.2, f"block=False blocked {dt:.2f}s!"
print(f"1. WS non-blocking drain returns in {dt*1000:.0f}ms OK")

# 2) REST option-only feed: no default indexes, skips when nothing subscribed
r = DhanRestFeed(client_id="c", access_token="t", option_only=True)
assert r.option_only and r.instruments == [], f"instruments={r.instruments}"
r.subscribe_option(42650)
assert ("NSE_FNO", 42650) in r.instruments, r.instruments
r.close()
print("2. REST option-only feed: no defaults + subscribe works OK")

# 3) default feed still polls both indexes (regression guard)
r2 = DhanRestFeed(client_id="c", access_token="t")
segs = {s for s, _ in r2.instruments}
assert segs == {"IDX_I"} and len(r2.instruments) == 2, r2.instruments
r2.close()
print("3. default REST feed still has both indexes OK")
print("\nALL PASS")
