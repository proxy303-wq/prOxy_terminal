#!/usr/bin/env python3
"""
PrOxy Trading Terminal - entry point
====================================

    python run_terminal.py                 interactive menu
    python run_terminal.py lots            lot-size answer (NIFTY lot 65)
    python run_terminal.py rules           the plan at a glance
    python run_terminal.py live [--fast]   paper-trade one demo day
    python run_terminal.py backtest [--days N] [--verbose]
    python run_terminal.py dashboard [--serve]   build + optionally serve the HTML dashboard
    python run_terminal.py report          backtest + dashboard in one shot
"""

import argparse
import os
import sys
import webbrowser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Windows consoles default to cp1252; force UTF-8 so box-drawing and
# rupee/arrow characters render correctly
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# allow running from any cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# load Telegram / Dhan credentials from C:\Athena_X\.env
from proxy.athena_env import load_athena_env  # noqa: E402
load_athena_env()

from proxy import config as cfg  # noqa: E402
from proxy.options import recommend_lots, estimate_premium  # noqa: E402
from proxy.risk import projected_year1_equity  # noqa: E402
from proxy.scheduler import phase_schedule, now_ist, is_trading_day, is_market_open  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")

B = "\033[1m"; R = "\033[0m"; CY = "\033[96m"; GR = "\033[92m"; YE = "\033[93m"; MG = "\033[95m"
RED = "\033[91m"


def banner():
    print(f"""{CY}
  {B}========================================================{R}{CY}
  {B}   PrOxy Trading Terminal{R}{CY}
  {B}   NIFTY options  |  lot 65  |  paper mode{R}{CY}
  {B}   5,00,000  ->  12.5%/mo  ->  62,500 INR/month{R}{CY}
  {B}========================================================{R}""")


def cmd_lots(args=None):
    banner()
    calc = recommend_lots(cfg)
    print(f"""
  {B}LOT-SIZE ANSWER - NIFTY lot size {calc['lot_size']}{R}
  -----------------------------------------------------
  Capital           : {calc['capital']:,.0f} INR
  Risk per trade    : {calc['risk_budget']:,.0f} INR  (0.5%)
  ATM premium (est) : {calc['premium']:.0f} INR
  Stop per unit     : {calc['stop_per_unit']:.2f} INR  (0.5% of premium, ~1 point)
  Target per unit   : {calc['target_per_unit']:.2f} INR  (1%)
  Risk per lot      : {calc['risk_per_lot']:.2f} INR  ({calc['lot_size']} x {calc['stop_per_unit']:.2f})
  Cost per lot      : {calc['cost_per_lot']:,.0f} INR  ({calc['lot_size']} x {calc['premium']:.0f})

  Max lots by risk  : {calc['max_lots_by_risk']}   ({calc['risk_budget']:,.0f} / {calc['risk_per_lot']:.2f})
  Max lots by cap   : {calc['max_lots_by_capital']}   ({calc['capital']:,.0f} / {calc['cost_per_lot']:,.0f})

  {B}RECOMMENDATION{R}
    Conservative : {calc['bands']['conservative']['lo']}-{calc['bands']['conservative']['hi']} lots   (risk {calc['bands']['conservative']['lo'] * calc['risk_per_lot']:,.0f}-{calc['bands']['conservative']['hi'] * calc['risk_per_lot']:,.0f} INR)
    {GR}Balanced     : {calc['bands']['balanced']['lo']}-{calc['bands']['balanced']['hi']} lots   (risk {calc['bands']['balanced']['lo'] * calc['risk_per_lot']:,.0f}-{calc['bands']['balanced']['hi'] * calc['risk_per_lot']:,.0f} INR){R}
    Full target  : {calc['bands']['full_target']['lo']} lots      (risk {calc['bands']['full_target']['lo'] * calc['risk_per_lot']:,.0f} INR -> {calc['daily_target_rs']:,.0f} INR/day)

  {B}Terminal default: {calc['selected_lots']} lots{R} (risk {calc['selected_risk']:,.0f} INR,
  cost {calc['selected_cost']:,.0f} INR, daily target {calc['daily_target_rs']:,.0f} INR,
  monthly target {calc['monthly_target_rs']:,.0f} INR)
""")


def cmd_rules(args=None):
    banner()
    proj = projected_year1_equity(cfg)
    print(f"""
  {B}THE PLAN{R}
  -----------------------------------------------------
  Entry   : Trend direction (Bullish -> CE / Bearish -> PE)
            Momentum RSI > 70 or < 30 | near support/resistance
            Signal strength > 70% | 9:15 - 14:45 window
  Exit    : +1% profit target | -0.5% stop-loss | 15:15 time stop
            reverse-signal exit
  Risk    : 0.5% per trade ({cfg.CAPITAL * cfg.RISK_PER_TRADE_PCT:,.0f} INR)
            daily loss stop 1% ({cfg.CAPITAL * cfg.MAX_DAILY_LOSS_PCT:,.0f} INR)
            monthly loss stop 5% ({cfg.CAPITAL * cfg.MAX_MONTHLY_LOSS_PCT:,.0f} INR)
            max {cfg.MAX_POSITIONS} position(s), R:R >= {cfg.MIN_RISK_REWARD}

  {B}SCORING (spec formula){R}
    Score = Trend*0.30 + Momentum*0.25 + S/R*0.25 + Volume*0.20
    BUY > +0.15 | SELL < -0.15 | WAIT otherwise
    + price-action / candlestick confirmation, confidence >= 70%

  {B}MONTHLY MATH{R}
    20 days: 15 wins (+1%) - 5 losses (-0.5%) = +12.5% = {cfg.CAPITAL * 0.125:,.0f} INR

  {B}YEAR-1 COMPOUNDING @ 12.5%/mo{R}""")
    for m in proj[:6]:
        print(f"    Month {m['month']:>2}: {m['equity']:>12,.0f} INR  (+{m['gain']:,.0f})")
    print(f"    ... Year 1 end: {proj[-1]['equity']:,.0f} INR")
    print(f"""
  {B}DAILY CYCLE{R}
    SETUP 8:30  | PRE-MARKET 9:00-9:15 (data->analytics->signal)
    TRADING 9:15-15:15 | POST-MARKET 15:15-15:30 (P&L, tracking, report)""")


def cmd_live(args):
    banner()
    from proxy.data import SyntheticLiveFeed, FastForwardFeed, yfinance_available
    from proxy.engine import PaperEngine
    from proxy.tracker import Tracker
    from proxy.notifier import Notifier
    from proxy.broker import PaperBroker

    trade_date = now_ist().date() if is_trading_day() else None
    if trade_date is None:
        d = now_ist().date()
        while d.weekday() >= 5:
            d = d + timedelta(days=1)
        trade_date = d

    spot = None
    if args.live_feed and yfinance_available():
        from proxy.data import fetch_live_spot
        spot = fetch_live_spot()
        print(f"{GR}Live NIFTY spot: {spot}{R}")

    live_orders = bool(getattr(args, "live", False))
    broker = None
    feed = None

    # ---- LIVE mode: real orders on the Dhan account ----
    if live_orders:
        from proxy.mode import get_mode
        from proxy.dhan_broker import DhanBroker
        if get_mode() != "live":
            print(f"{RED}Not in LIVE mode. Set it first: python run_terminal.py mode live{R}")
            return
        print(f"{RED}{B}LIVE TRADING - real orders will be placed on your Dhan account.{R}")
        confirm = input(f"  Type {B}LIVE{R} to continue: ").strip()
        if confirm.upper() != "LIVE":
            print(f"{YE}Aborted - no orders placed.{R}")
            return
        try:
            broker = DhanBroker()
            balance = broker.get_balance()
            print(f"{GR}Dhan connected. Available balance: {balance['cash']:,.2f} INR{R}")
            print(f"{YE}Live sizing uses the Dhan balance (risk {balance['cash']*cfg.RISK_PER_TRADE_PCT:,.2f} INR/trade, daily loss cap {balance['cash']*cfg.MAX_DAILY_LOSS_PCT:,.2f} INR){R}")
        except Exception as exc:
            print(f"{RED}Dhan live broker error: {exc}{R}")
            return
        from proxy.dhan_live import DhanLiveFeed, DhanUnavailable
        try:
            feed = DhanLiveFeed()
            feed.connect()
            print(f"{GR}Dhan WebSocket connected - streaming live 5m bars{R}")
        except DhanUnavailable as exc:
            print(f"{YE}Dhan feed unavailable ({exc}) - synthetic bars, real orders still live.{R}")
            feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)
        except Exception as exc:
            print(f"{YE}Dhan feed error ({exc}) - synthetic bars, real orders still live.{R}")
            feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)

    # ---- --dhan: paper orders, live Dhan market data ----
    elif getattr(args, "dhan", False):
        broker = PaperBroker(cfg.CAPITAL)
        from proxy.dhan_live import DhanLiveFeed, DhanUnavailable
        try:
            feed = DhanLiveFeed()
            feed.connect()
            print(f"{GR}Dhan WebSocket connected - streaming live 5m bars (paper orders){R}")
        except DhanUnavailable as exc:
            print(f"{RED}Dhan feed unavailable: {exc}{R}")
            print(f"{YE}Falling back to synthetic feed.{R}")
            feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)
        except Exception as exc:
            print(f"{RED}Dhan feed error: {exc}{R}")
            print(f"{YE}Falling back to synthetic feed.{R}")
            feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)

    # ---- default: synthetic paper demo ----
    else:
        broker = PaperBroker(cfg.CAPITAL)
        feed_cls = FastForwardFeed if args.fast else SyntheticLiveFeed
        feed = feed_cls(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED, spot=spot or cfg.SYNTHETIC_SPOT)

    tracker = Tracker(cfg)
    notifier = Notifier(quiet=False)
    live_capital = balance["cash"] if live_orders and balance else None
    engine = PaperEngine(cfg, broker=broker, tracker=tracker, notifier=notifier,
                         trade_date=trade_date, capital=live_capital)
    summary = engine.run_feed(feed, live=False)
    mode_tag = "LIVE (real orders)" if live_orders else "PAPER"
    print(f"""
  {B}DAY SUMMARY - {mode_tag}{R}
    Trades     : {summary.get('trades_today', 0)}
    Day P&L    : {summary.get('day_pnl', 0):+,.2f} INR
    Equity     : {summary.get('equity', 0):,.2f} INR
    Win rate   : {summary.get('win_rate', 0):.1f}%   (target 75%)
    Monthly    : {summary.get('monthly_progress_pct', 0):.1f}% of {cfg.CAPITAL * 0.125:,.0f} INR target
""")
    if live_orders and hasattr(broker, "kill_switch"):
        print(f"  Emergency stop available: broker.kill_switch()")



def cmd_backtest(args):
    banner()
    from proxy.backtest import Backtest
    bt = Backtest(cfg, max_days=args.days, last_days=args.last, verbose=args.verbose,
                  target_date=getattr(args, "date", None))
    label = (f"date {args.date}" if getattr(args, "date", None)
             else (f"last {args.last} days" if args.last else (f"{args.days} days" if args.days else "all days")))
    print(f"{MG}Backtesting on {bt.path} ({label}){R}\n")
    report = bt.run()
    paths = bt.save_report(report)
    print(f"""
  {B}BACKTEST RESULT{R}
    Period      : {report['period']}  ({report['bars']:,} x 5-min bars, exits {report.get('exit_resolution', '5m')})
    Trades      : {report['trades']}   (wins {report['wins']} / losses {report['losses']})
    Win rate    : {report['win_rate']:.1f}%   (target 75%)
    Net P&L     : {report['net_pnl']:+,.2f} INR
    Profit fact : {report['profit_factor'] if report['profit_factor'] is not None else 'inf'}
    Max DD      : {report['max_drawdown_pct']}%
    Avg win     : {report['avg_win']:,.2f}   Avg loss: {report['avg_loss']:,.2f}

  {B}EXIT REASONS{R}  {report['exit_reason_counts']}
  {B}SETUPS{R}         {report['setup_counts']}

  Saved: {paths[0]}
         {paths[1]}
""")


def cmd_dashboard(args):
    banner()
    from proxy.tracker import Tracker
    from proxy.dashboard import build_dashboard
    from proxy.data import load_csv

    tracker = Tracker(cfg)
    snapshot = tracker.to_snapshot()
    df = None
    try:
        df = load_csv(cfg.CSV_PATH)
    except Exception:
        df = None
    report = None
    try:
        import json as _json
        report_path = os.path.join(cfg.REPORT_DIR, "backtest_report.json")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as fh:
                report = _json.load(fh)
    except Exception:
        report = None
    chain = None
    try:
        from proxy.options import (build_option_chain, nifty_expiries, realized_volatility)
        spot = float(df["close"].iloc[-1]) if df is not None else cfg.SYNTHETIC_SPOT
        sigma = realized_volatility(df.set_index("date")["close"], 60) if df is not None else None
        chain = build_option_chain(spot, cfg, sigma=sigma)
        exps = []
        for e in nifty_expiries():
            c = build_option_chain(spot, cfg, sigma=sigma, dte=e["dte"])
            atm = next((r for r in c["rows"] if r["strike"] == c["atm"] and r["option_type"] == "CE"), None)
            exps.append({"bucket": e["bucket"], "date": str(e["date"]), "dte": e["dte"],
                         "atm_premium": atm["premium"] if atm else 0,
                         "atm_theta_pct": abs(atm["theta_pct_day"]) if atm else 0})
        chain["expiries"] = exps
    except Exception:
        chain = None
    sweep = None
    try:
        import json as _json2
        sweep_path = os.path.join(cfg.REPORT_DIR, "stop_loss_sweep.json")
        if os.path.exists(sweep_path):
            with open(sweep_path, "r", encoding="utf-8") as fh:
                sweep = _json2.load(fh)
    except Exception:
        sweep = None
    path = build_dashboard(snapshot, bars=df, backtest_report=report, chain=chain, sweep=sweep)
    print(f"{GR}Dashboard written: {path}{R}")
    board = None
    if getattr(args, "live_board", False) or os.environ.get("LIVE_BOARD") == "1":
        try:
            from proxy.live_board import LiveBoard
            board = LiveBoard(cfg)
        except Exception as exc:
            print(f"{YE}Live board unavailable: {exc}{R}")
    if args.serve:
        _serve(path, board=board)
    elif args.open:
        webbrowser.open("file:///" + path.replace("\\", "/"))


def cmd_report(args):
    cmd_backtest(args)
    cmd_dashboard(args)


def _serve(path, board=None):
    import http.server
    import json as _json
    import threading

    from proxy.tracker import Tracker
    root = os.path.dirname(path)
    os.chdir(root)
    port = int(os.environ.get("PORT", "8090"))   # Railway/Heroku set $PORT
    tracker = Tracker(cfg)

    def api_state():
        try:
            from proxy.portfolio import portfolio_report
            snap = tracker.to_snapshot()
            snap["portfolio"] = portfolio_report(snap)
            return snap
        except Exception:
            return {}

    from proxy.mode import get_mode, set_mode
    _MODE_KEY = os.environ.get("PROXY_MODE_KEY", "")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, payload, code=200):
            body = _json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _mode_allowed(self):
            """Localhost always allowed; remote needs the PROXY_MODE_KEY header."""
            if self.client_address[0] in ("127.0.0.1", "::1"):
                return True
            return bool(_MODE_KEY) and self.headers.get("X-PROXY-KEY") == _MODE_KEY

        def do_GET(self):
            if self.path in ("/api/state", "/api/state/"):
                return self._json(api_state())
            if self.path in ("/api/board", "/api/board/"):
                if board is not None:
                    return self._json(board.snapshot())
                return self._json({"status": "off"})
            if self.path in ("/api/trades", "/api/trades/"):
                return self._json({"trades": tracker.get_trades()[-100:]})
            if self.path in ("/api/mode", "/api/mode/"):
                return self._json({"mode": get_mode(), "key_required": bool(_MODE_KEY)})
            if self.path in ("/", "/index.html"):
                self.path = "/" + os.path.basename(path)
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/api/mode"):
                if not self._mode_allowed():
                    return self._json({"error": "not authorized - set PROXY_MODE_KEY to toggle mode remotely"}, 403)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    payload = _json.loads(self.rfile.read(length) or b"{}")
                    mode = (payload.get("mode") or "").lower()
                    if mode not in ("paper", "live"):
                        return self._json({"error": "mode must be paper|live"}, 400)
                    set_mode(mode)
                    return self._json({"mode": mode})
                except Exception as exc:
                    return self._json({"error": str(exc)}, 400)
            return self._json({"error": "unknown endpoint"}, 404)

    # 0.0.0.0 so Railway's healthcheck + public routing can reach the
    # server from outside the container (127.0.0.1 would refuse them)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    url = f"http://127.0.0.1:{port}/{os.path.basename(path)}"
    print(f"{GR}Serving dashboard at {url}  (Ctrl+C to stop){R}")
    if board is not None:
        print(f"{GR}Live board: {board.status}{R}")
    try:
        webbrowser.open(url)
    except Exception:
        pass   # headless hosts (Railway) have no browser - that is fine
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_sweep(args=None):
    """Run the stop-loss sweep (last N days, default 40)."""
    banner()
    from proxy.backtest import Backtest
    import json as _json
    days = args.last if args is not None and getattr(args, "last", None) else 40
    rows = []
    for stop in (0.005, 0.0075, 0.010, 0.015, 0.020):
        for lock in (True, False):
            cfg.STOP_LOSS_PCT = stop
            cfg.PROFIT_TARGET_PCT = 2.0 * stop
            cfg.LOCK_PROFIT_ENABLED = lock
            bt = Backtest(cfg, last_days=days)
            r = bt.run()
            rows.append({
                "stop_pct": round(stop, 4), "target_pct": round(2 * stop, 4), "lock": lock,
                "trades": r["trades"], "win_rate": r["win_rate"], "net_pnl": r["net_pnl"],
                "pf": r["profit_factor"], "max_dd": r["max_drawdown_pct"],
            })
    # restore defaults
    cfg.STOP_LOSS_PCT = 0.005
    cfg.PROFIT_TARGET_PCT = 0.010
    cfg.LOCK_PROFIT_ENABLED = True
    print(f"\n  {B}STOP-LOSS SWEEP - last {days} trading days{R}")
    print(f"  {'stop':>7} {'target':>7} {'lock':>5} {'trades':>6} {'win%':>6} {'net INR':>11} {'PF':>5} {'maxDD':>6}")
    for row in rows:
        print(f"  {row['stop_pct']*100:6.2f}% {row['target_pct']*100:6.2f}% {str(row['lock']):>5} "
              f"{row['trades']:>6} {row['win_rate']:>6.1f} {row['net_pnl']:>11,.0f} "
              f"{str(row['pf']):>5} {row['max_dd']:>6.2f}")
    path = os.path.join(cfg.REPORT_DIR, "stop_loss_sweep.json")
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(rows, fh, indent=2)
    print(f"\n  Saved: {path}")


def cmd_chain(args=None):
    """Show expiries + ATM/ITM option chain and the lowest-decay strike."""
    banner()
    from proxy.options import (build_option_chain, build_chain_for_expiry,
                               nifty_expiries, select_best_strike, realized_volatility)
    from proxy.data import load_csv

    spot = None
    if args is not None and getattr(args, "spot", None):
        spot = float(args.spot)
    expiry = None
    if args is not None and getattr(args, "expiry", None):
        expiry = args.expiry
    try:
        df = load_csv(cfg.CSV_PATH)
        close = df.set_index("date")["close"]
        sigma = realized_volatility(close, 14)
    except Exception:
        sigma = None
    if spot is None:
        try:
            df = load_csv(cfg.CSV_PATH)
            spot = float(df["close"].iloc[-1])
        except Exception:
            spot = cfg.SYNTHETIC_SPOT

    # ---- expiries table ----
    exps = nifty_expiries()
    print(f"\n  {B}EXPIRIES (NIFTY weekly Thu / monthly last Thu){R}")
    print(f"  {'bucket':<14} {'date':<12} {'DTE':>4} {'ATM prem':>9} {'ATM theta%/d':>12}")
    print("  " + "-" * 56)
    for e in exps:
        c = build_option_chain(spot, cfg, sigma=sigma, dte=e["dte"])
        atm = next(r for r in c["rows"] if r["strike"] == c["atm"] and r["option_type"] == "CE")
        mark = ">" if e["bucket"] == (expiry or cfg.OPTION_EXPIRY_BUCKET) else " "
        print(f"{mark} {e['bucket']:<14} {e['date'].strftime('%Y-%m-%d'):<12} {e['dte']:>4} "
              f"{atm['premium']:>9.2f} {abs(atm['theta_pct_day']):>12.2f}")

    # ---- chain for the selected expiry ----
    bucket = expiry or cfg.OPTION_EXPIRY_BUCKET
    chain = build_chain_for_expiry(spot, cfg, bucket=bucket, sigma=sigma)
    rows = chain["rows"]
    best = chain["best"]
    exp = chain["expiry"]

    print(f"\n  {B}OPTION CHAIN - NIFTY {spot:,.0f} | {exp['bucket']} expiry "
          f"({exp['date'].strftime('%d %b')}, {exp['dte']}d) | IV {chain['sigma']*100:.1f}%{R}")
    print(f"  {'strike':>7} {'type':>3} {'prem':>7} {'delta':>6} {'theta/d':>8} "
          f"{'theta%/d':>8} {'vega':>6} {'intr':>7} {'money':>5}")
    print("  " + "-" * 72)
    for r in sorted(rows, key=lambda x: (x["strike"], x["option_type"])):
        marker = " *" if (r["strike"] == best["strike"] and r["option_type"] == "CE") else "  "
        print(f"{marker}{r['strike']:>6.0f} {r['option_type']:>3} {r['premium']:>7.2f} "
              f"{r['delta']:>6.2f} {r['theta_day']:>8.2f} {r['theta_pct_day']:>8.2f} "
              f"{r['vega']:>6.2f} {r['intrinsic']:>7.2f} {r['moneyness']:>5}")

    print(f"\n  {B}RECOMMENDED LONG STRIKE (lowest time-decay tax){R}")
    print(f"    {best['strike']:.0f} CE | premium {best['premium']:.2f} | delta {best['delta']:.2f} "
          f"| theta {best['theta_pct_day']:.2f}%/day | IV {best['iv']*100:.1f}%")
    print(f"    One day of holding costs ~{abs(best['theta_pct_day']):.2f}% of premium at this strike "
          f"(ATM typically costs more); 1 lot = {best['premium']*cfg.LOT_SIZE:,.0f} INR.")
    print(f"\n  Toggle auto-selection with SELECT_BY_DELTA in proxy/config.py "
          f"(picks delta {cfg.OPTION_DELTA_MIN:.2f}-{cfg.OPTION_DELTA_MAX:.2f}).")


def cmd_mode(args=None):
    """Show or set the trading mode (paper | live)."""
    from proxy.mode import get_mode, set_mode
    current = get_mode()
    if args is not None and getattr(args, "mode", None):
        new_mode = args.mode
        if new_mode == "live":
            print(f"{RED}{B}WARNING: LIVE mode places REAL orders on your Dhan account.{R}")
            confirm = input(f"  Type {B}LIVE{R} to confirm switching to live trading: ").strip()
            if confirm.upper() != "LIVE":
                print(f"{YE}Aborted - still in paper mode.{R}")
                return
        set_mode(new_mode)
        print(f"{GR}Mode set to {new_mode.upper()}.{R}")
    else:
        color = RED if current == "live" else GR
        print(f"\n  Current trading mode: {color}{B}{current.upper()}{R}")
        print(f"  Toggle with: python run_terminal.py mode live | paper")


def cmd_ml_train(args=None):
    """Train the ML prediction model (LSTM per the research paper)."""
    banner()
    model_type = getattr(args, "model", None) or "lstm"
    days = getattr(args, "days", None)
    print(f"{MG}Training {model_type.upper()} on NIFTY 5m data (paper: LSTM is the best model){R}\n")
    from proxy.ml_model import train
    max_bars = days * 75 if days else None
    meta = train(model_type=model_type, max_bars=max_bars)
    print(f"\n  {B}MODEL READY{R} - test metrics: {meta['metrics']} "
          f"(majority baseline {meta['majority_class']}%)")
    print(f"  Advisory layer active; ML_CONFIRM in config.py makes it a gate.")


def cmd_dhan_auth(args=None):
    """Run the Dhan API-key consent flow to refresh the access token."""
    banner()
    from proxy.dhan_auth import (load_api_keypair, load_saved_token, resolve_token,
                                 token_is_expired)
    from proxy.dhan_broker import _load_athena_env
    creds = _load_athena_env()
    client_id = creds["client_id"]
    api_key, api_secret = load_api_keypair()
    saved = load_saved_token()
    env_token = creds["access_token"]
    if not client_id:
        print(f"{RED}DHAN_CLIENT_ID missing (C:\Athena_X\.env).{R}")
        return
    print(f"  client_id ......... {client_id}")
    print(f"  API key present ... {bool(api_key)}  (long-lived ~12-month credentials)")
    print(f"  .env token ........ {'present, valid' if env_token and not token_is_expired(env_token) else ('present, EXPIRED' if env_token else 'missing')}")
    print(f"  saved token ....... {'present, valid' if saved and not token_is_expired(saved) else ('present, EXPIRED' if saved else 'none')}")
    token, source = resolve_token(client_id, access_token=saved or "", api_key=api_key,
                                  api_secret=api_secret, interactive=True, notify=print)
    if token:
        print(f"\n{GR}Access token resolved via {source}.{R}")
        print(f"  Live trading will auto-renew it (RenewToken) until it expires,")
        print(f"  then re-run: python run_terminal.py dhan-auth")
    else:
        key_file = os.environ.get("DHAN_API_KEY_FILE") or "C:\\Athena_X\\dhan API KKEY.txt"
        print(f"\n{RED}Could not resolve a token. Check the API key file:{R}")
        print(f"  {key_file}")


def cmd_ml_train_meta(args=None):
    """Train the meta-label precision layer from backtest trade outcomes."""
    banner()
    days = getattr(args, "days", None) or 120
    print(f"{MG}Generating labeled trades (last {days} days) and training the meta model...{R}\n")
    from proxy.backtest import Backtest
    bt = Backtest(cfg, last_days=days)
    report = bt.run()
    if report["trades"] < 120:
        print(f"{YE}Only {report['trades']} trades - train on more days for a stable model.{R}")
    from proxy.meta_label import train_from_trades
    meta = train_from_trades(bt.trades, model_type=getattr(args, "model", None) or "xgboost")
    print(f"\n  {B}META MODEL READY{R} - {meta}")
    print(f"  Advisory layer active; META_CONFIRM in config.py makes it a gate.")


def cmd_menu():
    banner()
    from proxy.mode import get_mode
    mode = get_mode()
    calc = recommend_lots(cfg)
    print(f"""
  {B}1{R}  Live paper session  (synthetic feed, one trading day)
  {B}2{R}  Live paper session  --fast   (instant replay)
  {B}3{R}  Backtest on historical NIFTY 5m data
  {B}4{R}  Generate HTML dashboard  (open in browser)
  {B}5{R}  Lot-size answer  (NIFTY lot {calc['lot_size']}: {GR}{calc['selected_lots']} lots default{R})
  {B}6{R}  Rules & targets
  {B}7{R}  Option chain  (ATM/ITM strikes + expiries, lowest time-decay)
  {B}8{R}  Stop-loss sweep  (last 40 days)
  {B}9{R}  Full report  (backtest + dashboard)
  {B}0{R}  {B}{'[LIVE]' if mode == 'live' else '[PAPER]'}{R}  toggle trading mode (paper <-> live)
  {B}q{R}  Quit

  Market now: {now_ist().strftime('%A %d %b %Y %H:%M')} IST
               {'OPEN' if is_market_open() else 'closed'}  |  mode: {'LIVE' if mode == 'live' else 'paper'}
""")
    choice = input("  > ").strip().lower()
    if choice == "1":
        cmd_live(argparse.Namespace(fast=False, live_feed=False))
    elif choice == "2":
        cmd_live(argparse.Namespace(fast=True, live_feed=False))
    elif choice == "3":
        cmd_backtest(argparse.Namespace(days=None, verbose=False))
    elif choice == "4":
        cmd_dashboard(argparse.Namespace(serve=False, open=True))
    elif choice == "5":
        cmd_lots()
    elif choice == "6":
        cmd_rules()
    elif choice == "7":
        cmd_chain(None)
    elif choice == "8":
        cmd_sweep(argparse.Namespace(last=40))
    elif choice == "9":
        cmd_report(argparse.Namespace(days=None, verbose=False))
    elif choice == "0":
        from proxy.mode import get_mode, set_mode
        current = get_mode()
        if current == "live":
            set_mode("paper")
            print(f"{GR}Switched to PAPER mode.{R}")
        else:
            print(f"{RED}{B}Switching to LIVE mode places REAL orders on your Dhan account.{R}")
            confirm = input("  Type LIVE to confirm: ").strip()
            if confirm.upper() == "LIVE":
                set_mode("live")
                print(f"{RED}Switched to LIVE mode - real orders enabled.{R}")
            else:
                print(f"{YE}Aborted - still in paper mode.{R}")
    else:
        print("Bye.")


def main():
    parser = argparse.ArgumentParser(description="PrOxy Trading Terminal")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("live")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--live-feed", action="store_true")
    p.add_argument("--dhan", action="store_true", help="use Dhan WebSocket live feed")
    p.add_argument("--live", action="store_true", help="REAL orders on the Dhan account (mode must be live)")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("backtest")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--last", type=int, default=None)
    p.add_argument("--date", type=str, default=None, help="backtest a single day, e.g. 2026-07-07")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("dashboard")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--live-board", action="store_true", help="stream live Dhan market data + option chain")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("report")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_report)

    sub.add_parser("lots").set_defaults(func=cmd_lots)
    sub.add_parser("rules").set_defaults(func=cmd_rules)
    p = sub.add_parser("sweep")
    p.add_argument("--last", type=int, default=40)
    p.set_defaults(func=cmd_sweep)
    p = sub.add_parser("mode")
    p.add_argument("mode", nargs="?", choices=["paper", "live"], default=None)
    p.set_defaults(func=cmd_mode)
    p = sub.add_parser("ml-train")
    p.add_argument("--model", choices=["lstm", "xgboost"], default="lstm")
    p.add_argument("--days", type=int, default=None)
    p.set_defaults(func=cmd_ml_train)
    sub.add_parser("dhan-auth").set_defaults(func=cmd_dhan_auth)
    p = sub.add_parser("ml-train-meta")
    p.add_argument("--model", choices=["xgboost", "gb"], default="xgboost")
    p.add_argument("--days", type=int, default=120)
    p.set_defaults(func=cmd_ml_train_meta)
    p = sub.add_parser("chain")
    p.add_argument("--spot", type=float, default=None)
    p.add_argument("--expiry", type=str, default=None,
                   choices=["current_week", "next_week", "current_month", "next_month"])
    p.set_defaults(func=cmd_chain)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        cmd_menu()


if __name__ == "__main__":
    main()