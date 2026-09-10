"""Dashboard crypto tab: renders against fixture state without crashing.

Streamlit is not a test dependency here, so a recording stub is installed into
sys.modules. The tab must never raise, must show the action view, and the kill
switch must reflect the HALT sentinel.
"""
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRYPTO_ROOT = Path(__file__).resolve().parents[1]


class FakeColumn:
    """Recording column that also supports the 'with col:' form."""

    def __init__(self, stub):
        self._stub = stub

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return lambda *a, **k: self._stub._rec(name, *a, **k)


class FakeStub:
    """Minimal recording stand-in for streamlit."""

    def __init__(self):
        self.calls = []

    # -- helpers
    def _rec(self, name, *a, **k):
        self.calls.append((name, a, k))

    def find(self, name):
        return [c for c in self.calls if c[0] == name]

    # -- layout
    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [FakeColumn(self) for _ in range(n)]

    def expander(self, *a, **k):
        self._rec("expander", *a, **k)
        outer = self

        class _Ctx:
            def __enter__(self):
                class _Inner:
                    def __getattr__(self, name):
                        return lambda *aa, **kk: outer._rec(name, *aa, **kk)
                return _Inner()

            def __exit__(self, *exc):
                return False
        return _Ctx()

    def button(self, *a, **k):
        self._rec("button", *a, **k)
        return False

    def checkbox(self, *a, **k):
        self._rec("checkbox", *a, **k)
        return False

    def rerun(self, *a, **k):
        self._rec("rerun", *a, **k)

    def fragment(self, *a, **k):
        def deco(fn):
            return fn
        if a and callable(a[0]):
            return a[0]
        return deco

    def __getattr__(self, name):
        def fn(*a, **k):
            self._rec(name, *a, **k)
            return None
        return fn


STREAMLIT_API = (
    "subheader", "header", "title", "caption", "markdown", "divider", "write", "text",
    "metric", "columns", "expander", "container", "dataframe", "table", "line_chart",
    "plotly_chart", "json", "success", "error", "warning", "info", "button", "checkbox",
    "toggle", "selectbox", "radio", "number_input", "slider", "rerun", "fragment",
    "spinner", "tabs", "form", "form_submit_button",
)


@pytest.fixture
def fake_st(monkeypatch):
    stub = FakeStub()
    module = types.ModuleType("streamlit")
    for attr in STREAMLIT_API:
        bound = getattr(stub, attr, None)
        if bound is None:
            bound = (lambda name: (lambda *a, **k: stub._rec(name, *a, **k)))(attr)
        setattr(module, attr, bound)
    module.session_state = {}
    module.cache_data = lambda *a, **k: (lambda fn: fn)
    monkeypatch.setitem(sys.modules, "streamlit", module)
    return stub


@pytest.fixture
def crypto_mod(fake_st, monkeypatch, tmp_path):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "crypto_data_under_test", REPO_ROOT / "proxy" / "crypto_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # redirect every path at fixture data
    mod.CRYPTO = tmp_path
    mod.STATE_FILE = tmp_path / "data" / "state" / "portfolio.json"
    mod.JOURNAL_FILE = tmp_path / "data" / "journal" / "athena.jsonl"
    mod.LIVE_DIR = tmp_path / "data" / "live"
    mod.HALT_FILE = mod.LIVE_DIR / "HALT"
    mod.COUNTER_FILE = mod.LIVE_DIR / "trade_counter.json"
    mod.AUDIT_FILE = mod.LIVE_DIR / "audit.jsonl"
    mod.CONFIG_FILE = tmp_path / "config" / "config.toml"
    mod.fetch_marks = lambda symbols, timeout=3.0: {}
    mod._stub = fake_st
    return mod


def write_fixtures(mod):
    (mod.STATE_FILE.parent).mkdir(parents=True, exist_ok=True)
    mod.STATE_FILE.write_text(json.dumps({
        "last_equity": 1043.20, "start_equity": 1000.0, "realized_pnl": 61.4,
        "unrealized_pnl": -18.2, "day_pnl": -18.2, "closed_count": 1,
        "open_positions": [{
            "symbol": "BTCUSD", "product_id": 27, "direction": "short", "size": -7.0,
            "entry_price": 78000.0, "mark_price": 78260.0, "contract_value": 0.001,
            "notional": 546.0, "stop_price": 79560.0, "target_price": 74100.0,
            "unrealized_pnl": -1.82, "risk_usd": 10.92, "r_multiple": -0.17,
            "setup_type": "trend_pullback", "opened_at": time.time(),
        }],
        "mode": "paper", "timeframe": "4h", "symbols": ["BTCUSD", "ETHUSD"],
        "saved_at": time.time(),
    }), encoding="utf-8")
    mod.JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    recs = [
        {"kind": "cycle", "symbol": "BTCUSD", "bar_time": 1788989400,
         "signals": ["pullback in LH/LL downtrend"], "fills": [{"filled": True}],
         "state": {"symbol": "BTCUSD", "price": 78000.0, "regime": "trend_down",
                   "tradeable": True}},
        {"kind": "agent_decision", "symbol": "BTCUSD",
         "proposal": {"direction": "short", "conviction": 0.62, "blockers": []},
         "committee": {"recommendation": "approve"}},
        {"kind": "result", "trade": {
            "symbol": "ETHUSD", "direction": "long", "setup_type": "breakout_retest",
            "entry_price": 2400.0, "exit_price": 2450.0, "exit_reason": "target",
            "net_pnl": 61.4, "fee": 0.7, "size": 30.0, "stop_price": 2350.0,
            "closed_at": time.time(), "meta": {"contract_value": 0.01}}},
    ]
    mod.JOURNAL_FILE.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    mod.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    mod.COUNTER_FILE.write_text(json.dumps({"date": time.strftime("%Y-%m-%d", time.gmtime()),
                                            "count": 2}), encoding="utf-8")
    mod.AUDIT_FILE.write_text(json.dumps({"ts": time.time(), "event": "order_allowed",
                                          "symbol": "BTCUSD",
                                          "decision": {"code": "allowed"}}) + "\n",
                              encoding="utf-8")
    mod.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    mod.CONFIG_FILE.write_text(
        '[markets]\ntimeframe = "4h"\nsymbols = ["BTCUSD", "ETHUSD"]\n'
        '[risk]\nmax_orders_per_day = 12\ndaily_loss_limit_frac = 0.03\n'
        'drawdown_limit_frac = 0.06\n[account]\npaper_equity = 1000.0\n', encoding="utf-8")


def test_renders_action_view(crypto_mod):
    st = crypto_mod._stub
    write_fixtures(crypto_mod)
    crypto_mod.render_crypto_page()

    labels = [c[1][0] for c in st.find("metric") if c[1]]
    for expected in ("Equity", "Open P&L", "Realized P&L", "Day P&L", "Total return",
                     "Orders today"):
        assert expected in labels, expected
    # open positions + decisions + trades tables rendered
    dfs = st.find("dataframe")
    assert len(dfs) >= 3
    assert any("KILL SWITCH" in str(c[1][0]) for c in st.find("success"))
    assert not st.find("error")


def test_shows_tripped_kill_switch(crypto_mod, tmp_path):
    st = crypto_mod._stub
    write_fixtures(crypto_mod)
    crypto_mod.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    crypto_mod.HALT_FILE.write_text(json.dumps({"by": "test", "reason": "unit"}), encoding="utf-8")
    crypto_mod.render_crypto_page()
    assert any("TRIPPED" in str(c[1][0]) for c in st.find("error"))


def test_missing_files_still_render(crypto_mod):
    st = crypto_mod._stub
    crypto_mod.render_crypto_page()   # nothing exists yet
    assert not st.find("error")
    assert any("No bot state yet" in str(c[1][0]) for c in st.find("info"))


def test_halt_and_resume_roundtrip(crypto_mod):
    write_fixtures(crypto_mod)
    crypto_mod.trip_halt(reason="unit test")
    assert crypto_mod.HALT_FILE.exists()
    assert crypto_mod.guard_state()["halted"] is True
    assert crypto_mod.clear_halt() is True
    assert not crypto_mod.HALT_FILE.exists()
    assert crypto_mod.guard_state()["halted"] is False


def test_trade_stats_from_results(crypto_mod):
    write_fixtures(crypto_mod)
    results = crypto_mod.journal(kinds=["result"])
    stats = crypto_mod.trade_stats(results)
    assert stats["trades"] == 1
    assert round(stats["net"], 2) == 61.4
    assert stats["win_rate"] == 100.0
    # R must be derived from the trade payload, not read from a missing field
    # fixture: 30 contracts * 0.01 cv * |2400-2350| = 15.0 risk -> 61.4 / 15 = 4.09R
    assert round(stats["expectancy_r"], 2) == 4.09


def test_trade_r_helper(crypto_mod):
    t = {"entry_price": 100.0, "stop_price": 99.0, "size": 10.0, "net_pnl": 5.0,
         "meta": {"contract_value": 1.0}}
    assert round(crypto_mod.trade_r(t), 2) == 0.5
    # incomplete record -> None (rendered as '-'), never a fake 0.0
    assert crypto_mod.trade_r({"net_pnl": 5.0}) is None
    # implausible R (contract value inconsistent with price scale) -> None
    bad = {"entry_price": 98.5, "stop_price": 99.4, "size": 9.0, "net_pnl": 113.4,
           "meta": {"contract_value": 0.001}}
    assert crypto_mod.trade_r(bad) is None
    # stats ignore implausible records instead of averaging them
    stats = crypto_mod.trade_stats([])
    assert stats["trades"] == 0
