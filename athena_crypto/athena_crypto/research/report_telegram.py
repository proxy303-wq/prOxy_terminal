"""Run a backtest matrix + unit tests and post the results to Telegram.

Usage:
  python -m athena_crypto.research.report_telegram --days 60 [--no-send]
"""
import argparse, copy, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from athena_crypto.config import PROJECT_ROOT, WORKSPACE_ROOT, load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.backtest.engine import Backtester
from athena_crypto.strategies.registry import get_enabled_strategies
from athena_crypto.notify.telegram import TelegramNotifier

FX = 88.0


def telegram_creds():
    """token, chat from env or the usual local env files (never printed)."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    for path in (os.path.join(WORKSPACE_ROOT, ".oracle", "box.env"),
                 os.path.join(PROJECT_ROOT, ".env.demo"),
                 os.path.join(PROJECT_ROOT, ".env"),
                 os.path.join(WORKSPACE_ROOT, ".env")):
        if tok and chat:
            break
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    k, _, v = line.strip().partition("=")
                    if k == "TELEGRAM_BOT_TOKEN" and not tok:
                        tok = v.strip()
                    elif k == "TELEGRAM_CHAT_ID" and not chat:
                        chat = v.strip()
        except OSError:
            continue
    return tok, chat


class Shim:
    def __init__(self, toml):
        self.toml = toml

    @property
    def risk_config(self):
        return self.toml.get("risk", {})

    @property
    def costs_config(self):
        return self.toml.get("costs", {})

    @property
    def account_config(self):
        return self.toml.get("account", {})


def variant(cfg, strategy, session_hour=None, stop_mode="midpoint", max_hold=None, free=False):
    t = copy.deepcopy(cfg.toml)
    for name in t.get("strategies", {}):
        t["strategies"][name]["enabled"] = False
    t["strategies"][strategy]["enabled"] = True
    if strategy == "orb_sniper":
        t["strategies"]["orb_sniper"]["stop_mode"] = stop_mode
        t["strategies"]["orb_sniper"]["target_r"] = 1.5
        t["features"]["session_start_hour"] = session_hour or 0.0
        t["features"]["opening_range_minutes"] = 15
        t["backtest"]["max_hold_bars"] = max_hold or 12
    if free:
        t["costs"]["taker_fee_rate"] = 0.0
        t["costs"]["slippage_bps"] = 0.0
        t["costs"]["funding_rate_8h"] = 0.0
    return t


def run_one(cfg, store, products, sym, tf, strategy, session_hour=None, days=60,
            free=False, max_hold=None, stop_mode="midpoint"):
    candles = store.load(sym, tf)
    cut = int(time.time()) - days * 86400
    candles = [c for c in candles if c.time >= cut]
    if len(candles) < 200:
        return None
    t = variant(cfg, strategy, session_hour, stop_mode=stop_mode, max_hold=max_hold, free=free)
    shim = Shim(t)
    rep = Backtester([sym], get_enabled_strategies(t), products, shim).run_symbol(
        candles, sym, start_equity=200000.0 / FX / 3.0, lookback=300)
    tr = rep["closed_trades"]
    curve = rep["equity_curve"]
    peak = curve[0]
    mdd = 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak if peak else 0)
    rs = []
    for x in tr:
        risk = (abs(x["entry_price"] - x["stop_price"])
                * x["meta"].get("contract_value", 1) * abs(x["size"]))
        if risk:
            rs.append(x["net_pnl"] / risk)
    return {"n": len(tr),
            "net": sum(x["net_pnl"] for x in tr),
            "gross": sum(x["gross_pnl"] for x in tr),
            "costs": sum(x.get("fee", 0) for x in tr),
            "wins": len([x for x in tr if x["net_pnl"] > 0]),
            "exp": (sum(rs) / len(rs)) if rs else 0.0,
            "mdd": mdd * 100,
            "bars": len(candles)}


def safe_print(text):
    """Windows consoles are cp1252 and choke on emoji - print ASCII-safe locally."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def fmt_row(label, r, free=False):
    if r is None:
        return "  %s -- no data" % label
    key = "gross" if free else "net"
    sign = "+" if r[key] > 0 else ""
    return "  %s -- %d trades, %s%.2f USD (%s%.0f INR), win %.0f%%, exp %+.2fR, DD %.1f%%" % (
        label, r["n"], sign, r[key], sign, r[key] * FX,
        (100.0 * r["wins"] / r["n"]) if r["n"] else 0, r["exp"], r["mdd"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    os.chdir(PROJECT_ROOT)
    cfg = load_config()
    client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
    universe = cfg.symbols
    products = {p.symbol: p for p in
                client.get_products(contract_types="perpetual_futures", states="live")
                if p.symbol in universe}
    store = CandleStore(os.path.join("data", "history"))
    days = args.days

    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "--no-header",
                               "-p", "no:cacheprovider"], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, timeout=1800)
        lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        tests_line = lines[-1] if lines else "no output"
    except Exception as exc:
        tests_line = "could not run: %s" % exc

    msgs = []
    msgs.append("\U0001F9EA ATHENA CRYPTO test run\n"
                "universe: %s (perpetuals, no options)\nunit tests: %s"
                % (", ".join(universe), tests_line))

    orb = ["\U0001F4CA ORB SNIPER SCALPER - last %d days (real Delta data)" % days,
           "opening range = first 15 min of session; entry = first close beyond it;",
           "target 1.5R; capital Rs2L/3 per market; costs 0.05% + 2bps + funding."]
    for tf, mh in (("5m", 12), ("15m", 8)):
        for hour, hname in ((0.0, "00:00 UTC"), (13.5, "13:30 UTC")):
            for sym in universe:
                r = run_one(cfg, store, products, sym, tf, "orb_sniper",
                            session_hour=hour, days=days, max_hold=mh)
                orb.append(fmt_row("%s %s %s" % (sym, tf, hname), r))
    msgs.append("\n".join(orb))

    extra = ["\U0001F50D ORB with ZERO costs (is there any gross edge at all?)"]
    for sym in universe:
        r = run_one(cfg, store, products, sym, "15m", "orb_sniper",
                    session_hour=13.5, days=days, max_hold=8, free=True)
        extra.append(fmt_row("%s 15m 13:30 UTC gross" % sym, r, free=True))
    extra.append("")
    extra.append("\U0001F4C8 INCUMBENT trend_pullback 4h - same %d days" % days)
    for sym in universe:
        r = run_one(cfg, store, products, sym, "4h", "trend_pullback", days=days)
        extra.append(fmt_row(sym, r))
    extra.append("")
    extra.append("negative GROSS = no edge to capture; positive gross +")
    extra.append("negative net = costs are the problem. Demo/paper only.")
    msgs.append("\n".join(extra))

    for m in msgs:
        safe_print(m)
        print("-" * 78)

    if args.no_send:
        print("(not sending: --no-send)")
        return
    tok, chat = telegram_creds()
    n = TelegramNotifier(token=tok, chat_id=chat, enabled=True, min_interval=1.5)
    if not n.enabled:
        print("telegram not configured - printed only")
        return
    for m in msgs:
        print("sent:", n.send(m))
    print("telegram errors:", n.errors)


if __name__ == "__main__":
    main()
