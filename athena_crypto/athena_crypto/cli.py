"""ATHENA CRYPTO command line interface.

Subcommands:
  verify    - check connectivity, products, data and auth readiness
  fetch     - backfill candles into the local store
  backtest  - run the cost-aware backtester and print the scorecard
  run       - run the bot (paper by default; --live arms real orders)
  status    - show portfolio/equity/journal summary
  journal   - print journal entries
"""
import argparse
import json
import logging
import os
import sys
import time

from .logging_util import get_logger

log = get_logger("athena.cli", logfile=os.path.join("data", "athena.log"))


def _config(args):
    from .config import load_config
    return load_config(env_path=getattr(args, "env", None))


def _client(cfg, auth=True):
    from .exchange.delta_rest import DeltaRestClient
    if auth and not cfg.secrets.has_keys:
        raise SystemExit("Missing DELTA_API_KEY / DELTA_API_SECRET in env file. See .env.example.")
    return DeltaRestClient(api_key=cfg.secrets.api_key, api_secret=cfg.secrets.api_secret,
                           base_url=cfg.secrets.venue.rest_base)


def _state_path():
    return os.path.join("data", "state", "paper.json")


def _portfolio_path():
    return os.path.join("data", "state", "portfolio.json")


def _save_state(portfolio, marks=None, extras=None):
    """Persist equity AND open positions so the dashboard can show live state."""
    import time as _t
    stats = portfolio.stats(marks or {})
    positions = []
    for sym, pos in portfolio.positions.items():
        px = None
        if marks and sym in marks:
            m = marks[sym]
            px = getattr(m, "mark_price", None) or getattr(m, "last", None) or (m if isinstance(m, (int, float)) else None)
        upnl = pos.pnl_at(float(px)) if px else 0.0
        risk_amt = abs(pos.entry_price - pos.stop_price) * pos.contract_value * abs(pos.size) if pos.stop_price else 0.0
        positions.append({
            "symbol": sym, "product_id": pos.product_id, "direction": pos.direction,
            "size": pos.size, "entry_price": pos.entry_price, "mark_price": px,
            "contract_value": pos.contract_value, "notional": abs(pos.size) * pos.contract_value * (float(px) if px else pos.entry_price),
            "stop_price": pos.stop_price, "target_price": pos.target_price,
            "unrealized_pnl": upnl, "risk_usd": risk_amt,
            "r_multiple": (upnl / risk_amt) if risk_amt else 0.0,
            "setup_type": pos.setup_type, "opened_at": pos.opened_at,
            "bar_time": pos.meta.get("bar_time"),
        })
    payload = {
        "last_equity": stats["equity"],
        "start_equity": stats["start_equity"],
        "realized_pnl": stats["realized_pnl"],
        "unrealized_pnl": stats["unrealized_pnl"],
        "day_pnl": stats["day_pnl"],
        "fees_paid": stats.get("fees_paid", 0.0),
        "closed_count": stats["closed_count"],
        "open_positions": positions,
        "saved_at": _t.time(),
    }
    if extras:
        payload.update(extras)
    os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    with open(_portfolio_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _load_start_equity(cfg):
    default = float(cfg.account_config.get("paper_equity", 1000.0))
    if os.path.isfile(_state_path()):
        try:
            with open(_state_path(), "r", encoding="utf-8") as fh:
                return float(json.load(fh).get("last_equity", default))
        except Exception:
            pass
    return default


def cmd_verify(cfg, args):
    print("== ATHENA CRYPTO environment check ==")
    print("venue :", cfg.secrets.venue.name, cfg.secrets.venue.rest_base)
    print("env   :", cfg.secrets.env, "| keys present:", cfg.secrets.has_keys,
          "| key tail:", (cfg.secrets.api_key or "")[-6:])
    # which public address the exchange actually sees (this is what must be whitelisted)
    from .exchange.net import egress_info, ipv4_enabled
    forced = ipv4_enabled()
    print("egress: IPv4 forced =", forced, "(set DELTA_FORCE_IPV4=0 to use IPv6 instead)")
    info = egress_info()
    print("  public IPv4 :", info.get("ipv4"), "  <-- whitelist THIS (we call over IPv4)")
    print("  public IPv6 :", info.get("ipv6"), "  <-- only needed if you disable IPv4 forcing")
    print("  (a LAN address like 192.168.x.x / 10.x.x.x is never seen by Delta - do not whitelist it)")
    client = _client(cfg, auth=False)
    prods = client.get_products(contract_types="perpetual_futures", states="live")
    bysym = {p.symbol: p for p in prods}
    print("live perpetuals on venue :", len(prods))
    for sym in cfg.symbols:
        p = bysym.get(sym)
        if p is None:
            print("  !", sym, "NOT FOUND on this venue")
            continue
        t = client.get_ticker(sym)
        print("  %-8s id=%-6s tick=%-8s cv=%s mark=%s funding=%s" %
              (sym, p.product_id, p.tick_size, p.contract_value,
               round(t.mark_price, 4), t.funding_rate))
    # auth readiness
    if cfg.secrets.has_keys:
        try:
            ac = _client(cfg, auth=True)
            ac.get_balances()
            print("auth: OK (private API reachable - IP whitelisted)")
        except Exception as exc:
            print("auth: FAILED ->", str(exc)[:220])
            print("      (whitelist this machine's IP for the API key on the Delta India site)")
    else:
        print("auth: skipped (no keys) - run needs keys")
    print("symbols:", cfg.symbols, "| timeframe:", cfg.timeframe)


def cmd_fetch(cfg, args):
    client = _client(cfg, auth=False)
    from .data.market_data import MarketDataService
    tf = args.timeframe or cfg.timeframe
    svc = MarketDataService(client, store_root=os.path.join("data", "history"),
                            symbols=cfg.symbols, timeframe=tf)
    svc.load_products()
    out = svc.backfill(days=args.days)
    for sym, candles in out.items():
        print("%-8s %s bars (%s..%s)" % (sym, len(candles),
              time.strftime("%Y-%m-%d %H:%M", time.gmtime(candles[0].time)) if candles else "-",
              time.strftime("%Y-%m-%d %H:%M", time.gmtime(candles[-1].time)) if candles else "-"))


def cmd_backtest(cfg, args):
    client = _client(cfg, auth=False)
    from .data.market_data import MarketDataService
    from .backtest.engine import Backtester
    from .backtest.metrics import full_report
    from .strategies.registry import get_enabled_strategies

    tf = args.timeframe or cfg.timeframe
    svc = MarketDataService(client, store_root=os.path.join("data", "history"),
                            symbols=list(args.symbols or cfg.symbols), timeframe=tf)
    svc.load_products()
    candles_map = svc.backfill(days=args.days)
    product_map = svc.products
    strategies = get_enabled_strategies(cfg.toml)
    bt = Backtester(cfg.symbols, strategies, product_map, cfg)
    reports = []
    for sym in cfg.symbols:
        candles = candles_map.get(sym) or svc.history(sym, limit=100000)
        if not candles:
            print("no data for", sym)
            continue
        rep = bt.run_symbol(candles, sym, start_equity=_load_start_equity(cfg))
        if rep.get("error"):
            print("%s: %s" % (sym, rep["error"]))
            continue
        curve = rep["equity_curve"]
        trades = rep["closed_trades"]
        report = full_report(trades, curve)
        reports.append({"symbol": sym, **report})
        print("== %s ==" % sym)
        print("  trades=%d  net_pnl=%.2f  win_rate=%.0f%%  profit_factor=%.2f  expectancy=%.3f" %
              (report["trades"], report["net_pnl"], report["win_rate"] * 100,
               report["profit_factor"], report["expectancy"]))
        print("  final_equity=%.2f  total_return=%.1f%%  max_dd=%.2f (%.1f%%)  sharpe=%.2f" %
              (report["final_equity"], report["total_return"] * 100,
               report["max_drawdown"], report["max_drawdown_pct"] * 100, report["sharpe_annual"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(reports, fh, indent=2, default=str)
        print("report ->", args.json)


def cmd_run(cfg, args):
    from .data.market_data import MarketDataService
    from .execution.brokers import LiveBroker, PaperBroker
    from .execution.portfolio import Portfolio
    from .journal.journal import TradeJournal
    from .agent.controller import TradingController

    client = _client(cfg, auth=bool(args.live))
    live = bool(args.live)
    start_equity = _load_start_equity(cfg)
    tf = args.timeframe or cfg.timeframe
    svc = MarketDataService(client, store_root=os.path.join("data", "history"),
                            symbols=cfg.symbols, timeframe=tf)
    svc.load_products()
    # warmup must cover the strategy warmup_bars at this timeframe (4h needs ~14 days)
    from .data.market_data import TF_SECONDS
    warmup_bars = int(cfg.toml.get("backtest", {}).get("warmup_bars", 80))
    need_days = int(warmup_bars * TF_SECONDS.get(tf, 900) / 86400) + 3
    svc.backfill(days=max(args.warmup_days, need_days))
    portfolio = Portfolio(start_equity=start_equity,
                          fee_rate=float(cfg.costs_config.get("taker_fee_rate", 0.0005)))
    journal = TradeJournal(os.path.join("data", "journal", "athena.jsonl"))
    if live:
        # size off the real wallet balance, optionally capped so a funded demo
        # account can be traded as if it held a smaller amount (ATHENA_EQUITY)
        demo = cfg.secrets.env.endswith("_test")
        try:
            bal = client.get_wallet_balance("USD")
            if bal and bal.balance:
                wallet = float(bal.balance)
                cap = os.environ.get("ATHENA_EQUITY")
                used = min(wallet, float(cap)) if cap else wallet
                portfolio.set_start_equity(used)
                print("wallet USD balance %.2f -> trading equity %.2f%s"
                      % (wallet, used, "  (capped by ATHENA_EQUITY=%s)" % cap if cap and used < wallet else ""))
                start_equity = used
        except Exception as exc:
            print("could not read wallet balance (%s); using configured equity" % exc)
        broker = LiveBroker(client, portfolio, product_map=svc.products)
        broker.arm_live()
        if demo:
            print("*** DEMO ACCOUNT: real orders against Delta TESTNET - no real money ***")
        else:
            log.warning("LIVE MODE ENABLED - real capital at risk")
    else:
        broker = PaperBroker(portfolio,
                             taker_fee=float(cfg.costs_config.get("taker_fee_rate", 0.0005)),
                             slippage_bps=float(cfg.costs_config.get("slippage_bps", 2.0)))
    controller = TradingController(cfg, svc, portfolio, broker, svc.products,
                                   journal, equity_provider=lambda: portfolio.equity())
    controller.load_strategies()

    print("== ATHENA CRYPTO %s run (paper=%s live=%s) ==" %
          (tf, not live, live))
    print("symbols:", cfg.symbols, "| start equity:", round(start_equity, 2))
    cycles = 0
    try:
        if args.once:
            # one deterministic sweep over the latest closed bars
            svc.refresh_tickers()
            for sym in cfg.symbols:
                hist = svc.history(sym, limit=1000)
                if len(hist) < 20:
                    print("  skip %s (only %d bars cached)" % (sym, len(hist)))
                    continue
                controller.on_new_bar(sym, hist, ticker=svc.latest_tickers.get(sym))
                cycles += 1
            _save_state(portfolio, svc.latest_tickers,
                       extras={"timeframe": tf, "symbols": cfg.symbols, "mode": "paper"})
            stats = portfolio.stats(svc.latest_tickers)
            print(json.dumps(stats, indent=2, default=str))
            print("journal summary:", json.dumps(journal.summary(), default=str))
            return
        while True:
            newly = svc.poll_closed()
            for sym, candles_new in newly.items():
                hist = svc.history(sym, limit=1000)
                if len(hist) < 20:
                    continue
                ticker = svc.latest_tickers.get(sym)
                controller.on_new_bar(sym, hist, ticker=ticker)
                cycles += 1
            svc.refresh_tickers()
            stats = portfolio.stats(svc.latest_tickers)
            _save_state(portfolio, svc.latest_tickers,
                        extras={"timeframe": tf, "symbols": cfg.symbols,
                                "mode": "live" if live else "paper"})
            if live and args.exit_when_flat and not portfolio.has_position():
                print("flat & exit requested; stopping")
                break
            time.sleep(float(args.interval))
    except KeyboardInterrupt:
        print("interrupted")
    stats = portfolio.stats(svc.latest_tickers)
    print(json.dumps(stats, indent=2, default=str))
    print("journal summary:", json.dumps(journal.summary(), default=str))


def cmd_status(cfg, args):
    from .journal.journal import TradeJournal
    journal = TradeJournal(os.path.join("data", "journal", "athena.jsonl"))
    if os.path.isfile(_state_path()):
        with open(_state_path(), "r", encoding="utf-8") as fh:
            st = json.load(fh)
        print("last paper equity:", st.get("last_equity"))
    print("journal:", json.dumps(journal.summary(), default=str))


def cmd_journal(cfg, args):
    from .journal.journal import TradeJournal
    journal = TradeJournal(os.path.join("data", "journal", "athena.jsonl"))
    for rec in journal.read(kind=args.kind or None, limit=args.limit):
        print(json.dumps(rec, default=str)[:args.width])


def cmd_halt(cfg, args):
    """Trip the kill switch (halts ALL order placement immediately)."""
    from .safety.guard import OrderGuard
    guard = OrderGuard(cfg.risk_config)
    payload = guard.trip_halt(by="cli", reason=args.reason or "manual halt", symbol=args.symbol)
    print("kill switch tripped:", json.dumps(payload))
    print("sentinel:", guard.halt_path if not args.symbol else guard._halt_file(args.symbol))


def cmd_resume(cfg, args):
    """Clear the kill switch."""
    from .safety.guard import OrderGuard
    guard = OrderGuard(cfg.risk_config)
    cleared = guard.clear_halt(symbol=args.symbol)
    print("kill switch cleared" if cleared else "no halt sentinel present")


def cmd_guard_status(cfg, args):
    from .safety.guard import OrderGuard
    guard = OrderGuard(cfg.risk_config)
    print("kill switch tripped :", guard.halt_flag_set())
    print("orders today        :", guard.orders_today(),
          "/", cfg.risk_config.get("max_orders_per_day", "unlimited"))
    print("daily loss limit    :", cfg.risk_config.get("daily_loss_limit_frac"))
    print("drawdown limit      :", cfg.risk_config.get("drawdown_limit_frac"))
    print("audit log           :", guard.audit_path,
          "(exists)" if os.path.exists(guard.audit_path) else "(empty)")
    if guard.halt_flag_set():
        print("halt payload        :", json.dumps(guard.halt_reason()))


def build_parser():
    p = argparse.ArgumentParser(prog="athena-crypto", description="ATHENA CRYPTO bot CLI")
    p.add_argument("--env", help="path to an explicit .env file")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="environment/connectivity check")
    v.set_defaults(func=cmd_verify)

    f = sub.add_parser("fetch", help="backfill candles")
    f.add_argument("--days", type=int, default=14)
    f.add_argument("--symbols", nargs="*")
    f.add_argument("--timeframe", default=None)
    f.set_defaults(func=cmd_fetch)

    b = sub.add_parser("backtest", help="cost-aware backtest")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--symbols", nargs="*")
    b.add_argument("--timeframe", default=None)
    b.add_argument("--json", default=None, help="write report JSON here")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("run", help="run the bot")
    r.add_argument("--timeframe", default=None)
    r.add_argument("--live", action="store_true", help="place real orders (requires whitelisted IP)")
    r.add_argument("--once", action="store_true", help="stop after first decision cycle")
    r.add_argument("--exit-when-flat", action="store_true", help="live: stop after positions close")
    r.add_argument("--warmup-days", type=int, default=7)
    r.add_argument("--interval", type=float, default=20.0)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="portfolio/journal summary")
    s.set_defaults(func=cmd_status)

    h = sub.add_parser("halt", help="trip the kill switch (blocks all orders)")
    h.add_argument("--reason", default=None)
    h.add_argument("--symbol", default=None, help="halt a single symbol only")
    h.set_defaults(func=cmd_halt)

    rs = sub.add_parser("resume", help="clear the kill switch")
    rs.add_argument("--symbol", default=None)
    rs.set_defaults(func=cmd_resume)

    gs = sub.add_parser("guard-status", help="show kill switch / guard state")
    gs.set_defaults(func=cmd_guard_status)

    j = sub.add_parser("journal", help="print journal entries")
    j.add_argument("--kind", default=None, help="intent|cycle|result|exit")
    j.add_argument("--limit", type=int, default=40)
    j.add_argument("--width", type=int, default=400)
    j.set_defaults(func=cmd_journal)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # ensure cwd is project root so data/ paths resolve predictably
    from .config import PROJECT_ROOT
    os.chdir(PROJECT_ROOT)
    cfg = _config(args)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
