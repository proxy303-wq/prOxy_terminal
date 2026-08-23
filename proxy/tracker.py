"""
PrOxy Trading Terminal - Performance Tracker
============================================

Real-time P&L, win rate, daily/monthly progress and persistence.

Storage: SQLite (stdlib sqlite3) in reports/proxy_state.sqlite plus a
human-readable trades.csv export.  Everything a dashboard needs is
exposed through to_snapshot().
"""

import csv
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import CAPITAL, DB_PATH, REPORT_DIR

IST = ZoneInfo("Asia/Kolkata")


class Tracker:
    def __init__(self, cfg, db_path=None):
        self.cfg = cfg
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    # ----------------------------------------------------------
    # sqlite plumbing
    # ----------------------------------------------------------

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, instrument TEXT, direction TEXT, option_type TEXT,
                strike REAL, lots INTEGER, quantity INTEGER,
                entry_premium REAL, exit_premium REAL, stop_premium REAL,
                target_premium REAL, entry_spot REAL, entry_time TEXT,
                exit_time TEXT, exit_reason TEXT, setup_type TEXT,
                confidence REAL, trend TEXT, reason TEXT, pnl REAL, pnl_pct REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY, value TEXT
            )
        """)
        conn.commit()
        conn.close()

    def add_trade(self, record, state, cfg):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO trades (ts, instrument, direction, option_type, strike,
               lots, quantity, entry_premium, exit_premium, stop_premium,
               target_premium, entry_spot, entry_time, exit_time, exit_reason,
               setup_type, confidence, trend, reason, pnl, pnl_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(IST).isoformat(),
                record.get("instrument"), record.get("direction"),
                record.get("option_type"), record.get("strike"),
                record.get("lots"), record.get("quantity"),
                record.get("entry_premium"), record.get("exit_premium"),
                record.get("stop_premium"), record.get("target_premium"),
                record.get("entry_spot"), record.get("entry_time"),
                record.get("exit_time"), record.get("exit_reason"),
                record.get("setup_type"), record.get("confidence"),
                record.get("trend"), record.get("reason"),
                record.get("pnl"), record.get("pnl_pct"),
            ),
        )
        conn.commit()
        conn.close()
        self.save_state(state)
        self.export_csv()

    def save_state(self, state):
        conn = sqlite3.connect(self.db_path)
        for key, value in state.items():
            conn.execute(
                "INSERT OR REPLACE INTO state (key, value) VALUES (?,?)",
                (key, json.dumps(value)),
            )
        conn.commit()
        conn.close()

    def load_state(self):
        default = {
            "date": "", "trades_today": 0, "realized_pnl_today": 0.0,
            "realized_pnl_month": 0.0, "realized_pnl_total": 0.0,
            "wins": 0, "losses": 0, "trading_halted_day": False,
            "trading_halted_month": False, "equity_curve": [],
        }
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT key, value FROM state").fetchall()
        conn.close()
        for key, value in rows:
            try:
                default[key] = json.loads(value)
            except Exception:
                default[key] = value
        return default

    def get_trades(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM trades LIMIT 1").description]
        conn.close()
        return [dict(zip(cols, row)) for row in rows]

    def clear_trades(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM trades")
        conn.commit()
        conn.close()

    # ----------------------------------------------------------
    # analysis helpers
    # ----------------------------------------------------------

    def stats(self, trades=None):
        trades = trades if trades is not None else self.get_trades()
        wins = [t for t in trades if (t.get("pnl") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        return {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
            "net_pnl": round(sum(t.get("pnl", 0.0) for t in trades), 2),
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "avg_win": (gross_win / len(wins)) if wins else 0.0,
            "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        }

    def monthly_breakdown(self, trades=None):
        trades = trades if trades is not None else self.get_trades()
        by_month = {}
        for t in trades:
            month = (t.get("ts") or "")[:7]
            if month:
                by_month.setdefault(month, 0.0)
                by_month[month] += t.get("pnl", 0.0)
        return dict(sorted(by_month.items()))

    def export_csv(self, path=None):
        path = path or os.path.join(REPORT_DIR, "trades.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        trades = self.get_trades()
        if not trades:
            return path
        keys = [k for k in trades[0].keys()]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=keys)
            writer.writeheader()
            writer.writerows(trades)
        return path

    # ----------------------------------------------------------
    # dashboard snapshot
    # ----------------------------------------------------------

    def to_snapshot(self, state=None, live=None):
        state = state if state is not None else self.load_state()
        trades = self.get_trades()
        stats = self.stats(trades)
        equity = state.get("equity_curve", [])
        return {
            "capital": CAPITAL,
            "state": state,
            "stats": stats,
            "equity_curve": equity,
            "trades": trades[-40:][::-1],          # latest first
            "monthly_breakdown": self.monthly_breakdown(trades),
            "live": live or {},
            "generated_at": datetime.now(IST).isoformat(),
        }
