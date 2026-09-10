"""Trade journal - the metacognition/memory layer.

Every decision is journaled before acting (intent records) and every closed
trade is journaled with its outcome (result records). Hypothesis registry and
calibration buckets live here too, enabling the attribution loop described in
the masterplan (prediction vs realisation, drift, counterfactuals).
"""
import json
import os
import time
from typing import Optional


class TradeJournal:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def _append(self, rec: dict):
        rec["_ts"] = time.time()
        rec["_ts_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    # ------------------------------------------------------------------ intents
    def log_intent(self, plan, regime, mstate_snapshot, decision: str, note: str = ""):
        self._append({
            "kind": "intent",
            "decision": decision,            # APPROVE | MODIFY | REJECT | HOLD
            "plan": plan.to_jsonable() if plan else None,
            "regime": regime,
            "state": self._mini(mstate_snapshot),
            "note": note,
        })

    def log_decision_cycle(self, symbol, bar_time, mstate_summary, signals, fills, errors):
        self._append({
            "kind": "cycle",
            "symbol": symbol,
            "bar_time": bar_time,
            "signals": [s.reason for s in signals],
            "fills": fills,
            "errors": [str(e) for e in errors],
            "state": mstate_summary,
        })

    def log_trade_result(self, trade: dict):
        self._append({"kind": "result", "trade": trade})

    def log_exit_check(self, symbol, candle_time, outcome: str):
        self._append({"kind": "exit", "symbol": symbol, "candle_time": candle_time, "outcome": outcome})

    # ------------------------------------------------------------------ research
    def log_hypothesis(self, hypothesis: dict):
        self._append({"kind": "hypothesis", **hypothesis})

    def _mini(self, mstate):
        if mstate is None:
            return {}
        return {
            "symbol": mstate.get("symbol"),
            "time": mstate.get("time"),
            "price": mstate.get("price"),
            "regime": None,  # regime attached by controller
            "atr": mstate.get("atr"),
            "ema_fan": (mstate.get("price_action") or {}).get("ema_fan", {}).get("fan"),
            "structure": (mstate.get("structure") or {}).get("hh_ll"),
            "vol_pct": (mstate.get("volatility") or {}).get("rv_percentile"),
        }

    # ------------------------------------------------------------------ reads
    def read(self, kind: Optional[str] = None, limit: int = 500):
        out = []
        if not os.path.isfile(self.path):
            return out
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if kind and rec.get("kind") != kind:
                    continue
                out.append(rec)
                if len(out) >= limit:
                    break
        return out

    def results(self):
        return self.read(kind="result")

    def summary(self):
        results = self.read(kind="result")
        n = len(results)
        if n == 0:
            return {"closed_trades": 0}
        wins = [r for r in results if r.get("trade", {}).get("net_pnl", 0) > 0]
        gross = sum(r.get("trade", {}).get("net_pnl", 0) for r in results)
        return {
            "closed_trades": n,
            "wins": len(wins),
            "win_rate": len(wins) / n,
            "net_pnl": gross,
            "by_setup": _by_key(results, lambda r: r.get("trade", {}).get("setup_type", "?")),
            "by_exit": _by_key(results, lambda r: r.get("trade", {}).get("exit_reason", "?")),
        }


def _by_key(records, keyfn):
    out = {}
    for r in records:
        k = keyfn(r)
        out.setdefault(k, {"n": 0, "net_pnl": 0.0})
        out[k]["n"] += 1
        out[k]["net_pnl"] += r.get("trade", {}).get("net_pnl", 0.0)
    return out
