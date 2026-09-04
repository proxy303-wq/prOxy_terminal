"""PrOxy - MASTER ACCOUNT RISK GOVERNOR (V4.1 item 8, HANDOVER.md 13.8).

One Dhan account runs TWO engines (NIFTY + BANKNIFTY), each sized on its
own ~50% share and each carrying its OWN 1%-of-basis daily halt.  Two
engine-local -1% days can therefore cost ~1% of the WHOLE account
(2k + 2k on a ~4.1L balance) - exactly what the two engines are
individually allowed, and neither knows the other is bleeding.

The governor adds an ACCOUNT-level layer ABOVE both engines:

  * COMBINED OPEN RISK CAP  - the sum of stop-distance risk (sl_total in
    INR) across all currently open positions must stay <=
    MASTER_OPEN_RISK_PCT of the FULL account balance (0.5-0.75% per the
    user instruction; default 0.75%).
  * COMBINED DAY LOSS FLOOR - today's realised P&L across BOTH engines
    must stay above -MASTER_DAILY_LOSS_PCT of the full account (1%).  A
    shared account-level halt: when the ACCOUNT floor trips, no engine
    opens new positions.
  * COMBINED OPEN RISK is what the cap limits: sl_total = stop distance x
    quantity, the same INR the engine would lose if the position stopped.

State lives in a shared JSON (reports/master_risk.json) that both workers
update under a cross-platform advisory lock (fcntl on POSIX, msvcrt on
Windows), so the two processes see ONE account even though each holds its
own engine state / tracker DB.

Every call is a NO-OP (returns allowed) when cfg.MASTER_GOVERNOR_ENABLED
is falsy or the shared file cannot be read - paper sessions and every
backtest never activate the governor.

Staleness: an engine that dies mid-trade leaks its open-risk reservation.
A later acquire from the SAME engine replaces its own stale entry (the
engine's current position is the only live one), so the leak only blocks
the OTHER engine until the dead engine's worker restarts and re-acquires -
the fail-safe direction.
"""
import json
import os
import time
from datetime import datetime

from .config import REPORT_DIR

GOV_FILE = os.path.join(REPORT_DIR, "master_risk.json")


class _FileLock:
    """Cross-platform advisory file lock."""

    def __init__(self, path):
        self.path = path
        self._fh = None

    def __enter__(self):
        try:
            self._fh = open(self.path, "a+")
            try:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            except ImportError:
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        except Exception:
            pass  # best-effort lock; atomic replace still guards the file
        return self

    def __exit__(self, *a):
        try:
            if self._fh is not None:
                try:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except ImportError:
                    self._fh.seek(0)
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                self._fh.close()
        except Exception:
            pass
        return False


def _empty_state(day):
    return {"day": day, "updated": None, "account_capital": 0.0,
            "open_risk": 0.0, "day_pnl": 0.0, "day_halted": False,
            "entries": {}, "rejects": 0}


def _read(cfg):
    p = getattr(cfg, "MASTER_FILE", GOV_FILE)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        if not isinstance(st, dict):
            return _empty_state("")
    except Exception:
        return _empty_state("")
    return st


def _write(cfg, st):
    p = getattr(cfg, "MASTER_FILE", GOV_FILE)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        st["updated"] = datetime.now().isoformat(timespec="seconds")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def _account_capital(cfg):
    cap = float(getattr(cfg, "MASTER_ACCOUNT_CAPITAL", 0.0) or 0.0)
    if cap > 0:
        return cap
    return float(getattr(cfg, "CAPITAL", 500_000.0))


def _enabled(cfg):
    return bool(getattr(cfg, "MASTER_GOVERNOR_ENABLED", False))


def _engine_name(cfg):
    return str(getattr(cfg, "OPTION_SYMBOL", None) or "NIFTY")


def _today():
    return str(datetime.now().date())


def _roll_day(st, cfg):
    """Reset day counters on a new calendar day (also clears stale entries)."""
    day = _today()
    if st.get("day") != day:
        st = _empty_state(day)
    st["account_capital"] = _account_capital(cfg)
    return st


def snapshot(cfg):
    """Read-only state view for logs / dashboard (never blocks)."""
    if not _enabled(cfg):
        return None
    st = _read(cfg)
    cap = _account_capital(cfg)
    return {"day": st.get("day"), "account_capital": cap,
            "open_risk": round(st.get("open_risk", 0.0), 2),
            "open_risk_pct": round(st.get("open_risk", 0.0) / cap * 100.0, 3) if cap else 0.0,
            "cap": round(cap * float(getattr(cfg, "MASTER_OPEN_RISK_PCT", 0.0075)), 2),
            "day_pnl": round(st.get("day_pnl", 0.0), 2),
            "day_halted": st.get("day_halted", False),
            "entries": st.get("entries", {})}


def acquire(cfg, sl_total_inr, meta=None):
    """Reserve one engine's open-risk (sl_total) for a NEW position.

    Atomic check-and-commit under the file lock: the combined OPEN risk
    must stay <= MASTER_OPEN_RISK_PCT of the account and the combined DAY
    P&L must be above the master floor.  Returns a RiskCheck."""
    from .risk import RiskCheck
    if not _enabled(cfg):
        return RiskCheck(True, "governor disabled")
    try:
        sl = float(sl_total_inr or 0.0)
        sl = sl if sl == sl and sl >= 0 else 0.0
        engine = _engine_name(cfg)
        open_cap_pct = float(getattr(cfg, "MASTER_OPEN_RISK_PCT", 0.0075))
        day_floor_pct = float(getattr(cfg, "MASTER_DAILY_LOSS_PCT", 0.01))
        day_floor = _account_capital(cfg) * day_floor_pct
        open_cap = _account_capital(cfg) * open_cap_pct
        with _FileLock(getattr(cfg, "MASTER_FILE", GOV_FILE) + ".lock"):
            st = _roll_day(_read(cfg), cfg)
            if st.get("day_halted"):
                st["rejects"] = st.get("rejects", 0) + 1
                _write(cfg, st)
                return RiskCheck(False, "MASTER: account day loss floor hit - combined halt")
            # this engine's existing (possibly stale) reservation is replaced
            # by its current position: only one open position per engine
            old = float(st.get("entries", {}).get(engine, {}).get("sl", 0.0) or 0.0)
            new_open = st.get("open_risk", 0.0) - old + sl
            if new_open > open_cap + 1e-9:
                st["rejects"] = st.get("rejects", 0) + 1
                _write(cfg, st)
                return RiskCheck(
                    False,
                    f"MASTER: combined open risk {new_open:,.0f} > {open_cap:,.0f} "
                    f"({open_cap_pct*100:.2f}% of account) - {engine} entry blocked")
            entries = dict(st.get("entries", {}))
            entries[engine] = {"sl": round(sl, 2), "at": time.time(),
                               "meta": (meta or {})}
            st["open_risk"] = round(new_open, 2)
            st["entries"] = entries
            st["account_capital"] = _account_capital(cfg)
            _write(cfg, st)
        return RiskCheck(True, f"MASTER: open risk now {new_open:,.0f} / {open_cap:,.0f}")
    except Exception:
        # fail-open: a governor fault must never freeze a live engine
        return RiskCheck(True, "MASTER: governor unavailable (fail-open)")


def release(cfg, sl_total_inr):
    """Drop this engine's open-risk reservation after its position closes."""
    if not _enabled(cfg):
        return
    try:
        sl = float(sl_total_inr or 0.0)
        engine = _engine_name(cfg)
        with _FileLock(getattr(cfg, "MASTER_FILE", GOV_FILE) + ".lock"):
            st = _roll_day(_read(cfg), cfg)
            old = float(st.get("entries", {}).get(engine, {}).get("sl", 0.0) or 0.0)
            entries = dict(st.get("entries", {}))
            entries.pop(engine, None)
            st["open_risk"] = max(0.0, st.get("open_risk", 0.0) - old)
            st["entries"] = entries
            _write(cfg, st)
    except Exception:
        pass


def record_realized(cfg, pnl_inr, release_sl_inr=None):
    """Accumulate realised P&L into the account day and trip the shared
    halt when the combined floor is breached.  Optionally releases the
    engine's open-risk in the same locked write."""
    if not _enabled(cfg):
        return
    try:
        pnl = float(pnl_inr or 0.0)
        pnl = pnl if pnl == pnl else 0.0
        engine = _engine_name(cfg)
        with _FileLock(getattr(cfg, "MASTER_FILE", GOV_FILE) + ".lock"):
            st = _roll_day(_read(cfg), cfg)
            day_floor = _account_capital(cfg) * float(getattr(cfg, "MASTER_DAILY_LOSS_PCT", 0.01))
            st["day_pnl"] = round(st.get("day_pnl", 0.0) + pnl, 2)
            if st["day_pnl"] <= -day_floor:
                st["day_halted"] = True
            if release_sl_inr is not None:
                old = float(st.get("entries", {}).get(engine, {}).get("sl", 0.0) or 0.0)
                entries = dict(st.get("entries", {}))
                entries.pop(engine, None)
                st["open_risk"] = max(0.0, st.get("open_risk", 0.0) - old)
                st["entries"] = entries
            _write(cfg, st)
    except Exception:
        pass
