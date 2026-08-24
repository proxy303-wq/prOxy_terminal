"""
PrOxy Trading Terminal - Dashboard Builder
==========================================

Builds a single self-contained HTML dashboard (dark terminal theme,
pure canvas charts - no CDN, works offline).  Layout is a simple
TAB-BAR navigation (like the Athenscreed page-radio style):

    Dashboard | Live Market | Chain & Expiries | Backtest | Sweep | Trades | System

Data is embedded as JSON at build time; the Live Market tab polls
/api/board (Dhan WebSocket + real option chain) and /api/state every
5s when served with the live board enabled.
"""

import html
import json
import os

from .config import DASHBOARD_HTML, REPORT_DIR
from . import config as cfg_module


def _kpi(label, value, sub="", color="#7ee787"):
    return f"""
    <div class="kpi">
      <div class="kpi-label">{html.escape(label)}</div>
      <div class="kpi-value" style="color:{color}">{html.escape(str(value))}</div>
      <div class="kpi-sub">{html.escape(str(sub))}</div>
    </div>"""


def build_dashboard(snapshot, bars=None, path=DASHBOARD_HTML, title="PrOxy Trading Terminal",
                    backtest_report=None, chain=None, sweep=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    state = snapshot.get("state", {})
    stats = snapshot.get("stats", {})
    trades = snapshot.get("trades", [])
    equity = snapshot.get("equity_curve", [])
    capital = snapshot.get("capital", 500000)
    try:
        from .portfolio import portfolio_report
        pfolio = portfolio_report(snapshot)
    except Exception:
        pfolio = {}
    monthly_target = round(capital * 0.125)
    monthly_pnl = state.get("realized_pnl_month", 0.0)
    monthly_progress = (monthly_pnl / monthly_target * 100.0) if monthly_target else 0.0

    # backtest panel values
    if backtest_report:
        bt_trades = backtest_report.get("trades", 0)
        bt_winrate = backtest_report.get("win_rate", 0.0)
        bt_net = backtest_report.get("net_pnl", 0.0)
        bt_pf = backtest_report.get("profit_factor", "-")
        bt_dd = backtest_report.get("max_drawdown_pct", 0.0)
        bt_period = backtest_report.get("period", "-") + " | exits " + backtest_report.get("exit_resolution", "5m")
        daily = backtest_report.get("daily_pnl", {})
        daily_rows = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td class=\"{'pos' if v >= 0 else 'neg'}\">{v:+,.2f}</td></tr>"
            for k, v in list(daily.items())[-30:])
    else:
        bt_trades, bt_winrate, bt_net, bt_pf, bt_dd, bt_period = 0, 0.0, 0.0, "-", 0.0, "-"
        daily_rows = '<tr><td colspan="2" class="muted">Run a backtest first</td></tr>'

    # sweep table
    if sweep:
        sweep_rows = "".join(
            f"<tr><td>{r['stop_pct']*100:.2f}%</td><td>{r['target_pct']*100:.2f}%</td>"
            f"<td>{'on' if r['lock'] else 'off'}</td><td>{r['trades']}</td>"
            f"<td>{r['win_rate']:.1f}%</td>"
            f"<td class=\"{'pos' if r['net_pnl'] > 0 else 'neg'}\">{r['net_pnl']:+,.0f}</td>"
            f"<td>{r['pf'] if r['pf'] is not None else '-'}</td></tr>"
            for r in sweep)
    else:
        sweep_rows = '<tr><td colspan="7" class="muted">Run the sweep first</td></tr>'

    # chain + expiries tables
    chain_rows = ""
    if chain and chain.get("rows"):
        best = chain.get("best", {})
        best_key = (best.get("strike"), best.get("option_type"))
        for row in sorted(chain["rows"], key=lambda x: (x["strike"], x["option_type"])):
            mark = "&#9733;" if (row["strike"], row["option_type"]) == best_key else ""
            chain_rows += (f"<tr><td>{mark} {row['strike']:.0f}</td><td>{row['option_type']}</td>"
                           f"<td>{row['premium']:.2f}</td><td>{row['delta']:+.2f}</td>"
                           f"<td>{abs(row['theta_pct_day']):.2f}%</td><td>{row['moneyness']}</td></tr>")
    exp_rows = ""
    for e in (chain.get("expiries") or []):
        mark = "&#9654;" if e.get("bucket") == getattr(cfg_module, "OPTION_EXPIRY_BUCKET", "current_week") else ""
        exp_rows += (f"<tr><td>{mark} {e['bucket']}</td><td>{e['date']}</td><td>{e['dte']}d</td>"
                     f"<td>{e['atm_premium']:.2f}</td><td>{e['atm_theta_pct']:.2f}%</td></tr>")

    # trade log
    trade_rows = "".join(
        f"""<tr>
          <td>{html.escape(str(t.get('entry_time', '')))[:16]}</td>
          <td>{html.escape(str(t.get('instrument', '')))}</td>
          <td>{html.escape(str(t.get('direction', '')))}</td>
          <td>{t.get('lots', '')}</td>
          <td>{t.get('entry_premium', '')}</td>
          <td>{t.get('exit_premium', '')}</td>
          <td>{html.escape(str(t.get('exit_reason', '')))}</td>
          <td>{html.escape(str(t.get('setup_type', '')))}</td>
          <td class="{'pos' if (t.get('pnl') or 0) > 0 else 'neg'}">{t.get('pnl', 0):+,.2f}</td>
        </tr>""" for t in trades[:40])
    if not trade_rows:
        trade_rows = '<tr><td colspan="9" class="muted">No trades yet</td></tr>'

    bars_data = []
    if bars is not None and len(bars) > 0:
        for _, row in bars.tail(120).iterrows():
            t = str(row.get("date", row.name)) if "date" in bars.columns else str(row.name)
            bars_data.append([t, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])])

    equity_points = json.dumps(equity)
    bars_json = json.dumps(bars_data)
    mode_tag = "LIVE" if getattr(cfg_module, "LIVE_TRADING", False) else "PAPER"

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  :root {{
    /* Wealthfolio-inspired warm paper palette */
    --paper:#faf8f1; --card:#fffdf7; --line:#e7e3d4; --line2:#d8d3c0;
    --text:#2b2a26; --muted:#8b8778; --faint:#b3af9d;
    --green:#768d21; --green-soft:#eef0dc;
    --red:#c03e35; --red-soft:#fbe9e5;
    --cyan:#2f968d; --cyan-soft:#e0efec;
    --yellow:#be9207; --blue:#4385be;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--paper); color:var(--text);
         font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:22px 18px 48px; }}
  header {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;
           padding:16px 2px; margin-bottom:8px; }}
  .brand {{ font-size:19px; font-weight:700; letter-spacing:-0.2px; color:var(--text); }}
  .brand span {{ color:var(--muted); font-weight:500; }}
  .tagline {{ color:var(--muted); font-size:12px; margin-top:3px; }}
  .chip {{ background:var(--card); border:1px solid var(--line); border-radius:999px;
          padding:3px 12px; font-size:12px; color:var(--text);
          font-family:'JetBrains Mono','Consolas',monospace; }}
  nav.tabs {{ display:flex; gap:2px; flex-wrap:wrap; border-bottom:1px solid var(--line);
             margin-bottom:20px; }}
  nav.tabs button {{ background:transparent; border:none; border-bottom:2px solid transparent;
        color:var(--muted); padding:10px 14px; cursor:pointer; font-size:13px;
        font-family:inherit; margin-bottom:-1px; }}
  nav.tabs button:hover {{ color:var(--text); }}
  nav.tabs button.active {{ color:var(--text); font-weight:600; border-bottom-color:var(--green); }}
  .tabpanel {{ display:none; }}
  .tabpanel.active {{ display:block; }}
  .panel {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:18px; margin-bottom:14px; }}
  .panel h2 {{ font-size:11px; text-transform:uppercase; letter-spacing:1.2px;
              color:var(--muted); margin-bottom:12px; font-weight:600; }}
  .cols {{ display:grid; grid-template-columns:2fr 1fr; gap:14px; }}
  @media(max-width:820px) {{ .cols {{ grid-template-columns:1fr; }} }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:13px 14px; }}
  .kpi-label {{ font-size:11px; color:var(--muted); }}
  .kpi-value {{ font-size:20px; font-weight:700; margin-top:3px; letter-spacing:-0.3px;
               font-family:'JetBrains Mono','Consolas',monospace; }}
  .kpi-sub {{ font-size:11px; color:var(--faint); }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th,td {{ text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:0.5px; }}
  tr:last-child td {{ border-bottom:none; }}
  .pos {{ color:var(--green); }} .neg {{ color:var(--red); }}
  canvas {{ width:100%; background:var(--card); border-radius:8px; }}
  .muted {{ color:var(--muted); }} .small {{ font-size:12px; line-height:1.8; }}
  .bar-track {{ background:var(--green-soft); height:14px; border-radius:7px; overflow:hidden; }}
  .bar-fill {{ height:100%; background:var(--green);
              width:{min(100, max(0, monthly_progress)):.1f}%; }}
  b {{ font-weight:600; }}
  .mode-btn {{ border:1px solid var(--green); background:var(--green); color:#fff; font-weight:700;
        padding:5px 16px; border-radius:999px; cursor:pointer; font-size:12px; letter-spacing:0.6px;
        font-family:'JetBrains Mono','Consolas',monospace; }}
  .mode-btn.live {{ background:var(--red); border-color:var(--red); }}
  .mode-btn:active {{ opacity:0.85; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div>
    <div class="brand">PrOxy <span>TRADING TERMINAL</span></div>
    <div class="tagline">NIFTY options | 5,00,000 capital | 12.5%/mo | 62,500 INR/month | lot {cfg_module.DEFAULT_LOTS}</div>
  </div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
    <span class="chip">NIFTY <b id="liveSpot">--</b></span>
    <span class="chip" id="liveChange">--</span>
    <span class="chip" id="liveDir">--</span>
    <span class="chip" id="liveMode">{mode_tag}</span>
    <button id="modeBtn" class="mode-btn" title="Switch between PAPER and LIVE trading">PAPER</button>
  </div>
</header>

<nav class="tabs">
  <button class="tab active" data-tab="overview">Dashboard</button>
  <button class="tab" data-tab="live">Live Market</button>
  <button class="tab" data-tab="chain">Chain &amp; Expiries</button>
  <button class="tab" data-tab="backtest">Backtest</button>
  <button class="tab" data-tab="sweep">Stop-Loss Sweep</button>
  <button class="tab" data-tab="trades">Trades</button>
  <button class="tab" data-tab="system">System</button>
</nav>

<!-- ============ Dashboard ============ -->
<section id="tab-overview" class="tabpanel active">
  <div class="kpis">
    {_kpi("EQUITY", f"{capital + state.get('realized_pnl_total', 0):,.0f}", "capital + realized", 'var(--cyan)')}
    {_kpi("NET P&L", f"{stats.get('net_pnl', 0):+,.0f} INR", f"{stats.get('trades', 0)} trades", 'var(--green)' if stats.get('net_pnl', 0) >= 0 else 'var(--red)')}
    {_kpi("WIN RATE", f"{stats.get('win_rate', 0):.1f}%", "target 75%", 'var(--cyan)')}
    {_kpi("TODAY", f"{state.get('realized_pnl_today', 0):+,.0f} INR", f"{state.get('trades_today', 0)} trades", 'var(--yellow)')}
    {_kpi("MONTH", f"{monthly_pnl:+,.0f} INR", f"of {monthly_target:,.0f} target", 'var(--green)' if monthly_pnl >= 0 else 'var(--red)')}
    {_kpi("PROFIT FACTOR", f"{stats.get('profit_factor', 0):.2f}", "gross win / loss", 'var(--purple, #a78bfa)')}
  </div>
  <div class="panel" style="margin-top:14px">
    <h2>Monthly progress vs {monthly_target:,.0f} INR target</h2>
    <div class="bar-track"><div class="bar-fill"></div></div>
    <div class="small muted" style="margin-top:6px">{monthly_pnl:+,.2f} / {monthly_target:,.0f} ({monthly_progress:.1f}%)</div>
  </div>
  <div class="cols">
    <div class="panel">
      <h2>Equity curve</h2>
      <canvas id="equity" height="260"></canvas>
    </div>
    <div class="panel">
      <h2>Portfolio analytics</h2>
      <div class="small" style="line-height:2">
        Sharpe <b>{pfolio.get('sharpe', '-')}</b> &nbsp;·&nbsp; Sortino <b>{pfolio.get('sortino', '-')}</b><br>
        Calmar <b>{pfolio.get('calmar', '-')}</b> &nbsp;·&nbsp; Max DD <b>{pfolio.get('max_drawdown_pct', 0)}%</b><br>
        Expectancy <b>{pfolio.get('expectancy', '-')} INR</b> &nbsp;·&nbsp; Kelly <b>{pfolio.get('kelly_fraction', '-')}</b><br>
        Avg hold <b>{pfolio.get('avg_hold_minutes', '-')}m</b> &nbsp;·&nbsp; W/L <b>{state.get('wins', 0)}/{state.get('losses', 0)}</b>
      </div>
    </div>
  </div>
</section>

<!-- ============ Live Market ============ -->
<section id="tab-live" class="tabpanel">
  <div class="panel">
    <h2>Live market (Dhan WebSocket)</h2>
    <div class="small muted" id="liveInfo">Waiting for live data...</div>
    <div class="small" id="liveSpotBig" style="font-size:26px;font-weight:700;margin:10px 0">--</div>
    <div style="overflow-x:auto">
      <table id="liveChainTable">
        <tr><th>Strike</th><th>CE LTP</th><th>CE OI</th><th>PE LTP</th><th>PE OI</th></tr>
        <tr><td colspan="5" class="muted">Serve with --live-board to stream real Dhan data</td></tr>
      </table>
    </div>
  </div>
</section>

<!-- ============ Chain & Expiries ============ -->
<section id="tab-chain" class="tabpanel">
  <div class="panel">
    <h2>Expiries - time decay by expiry (&#9654; = trade default)</h2>
    <div style="overflow-x:auto">
      <table>
        <tr><th>Bucket</th><th>Date</th><th>DTE</th><th>ATM prem</th><th>ATM theta %/day</th></tr>
        {exp_rows or '<tr><td colspan="5" class="muted">No expiry data</td></tr>'}
      </table>
    </div>
  </div>
  <div class="panel">
    <h2>Option chain ATM/ITM (&#9733; = lowest time-decay)</h2>
    <div style="overflow-x:auto">
      <table>
        <tr><th>Strike</th><th>Type</th><th>Premium</th><th>Delta</th><th>Theta %/day</th><th>Moneyness</th></tr>
        {chain_rows or '<tr><td colspan="6" class="muted">No chain data</td></tr>'}
      </table>
    </div>
    {f'<div class="small muted" style="margin-top:6px">Recommended long strike: {chain["best"]["strike"]:.0f} {chain["best"]["option_type"]} (theta {abs(chain["best"]["theta_pct_day"]):.2f}%/day)</div>' if chain and chain.get("best") else ''}
  </div>
</section>

<!-- ============ Backtest ============ -->
<section id="tab-backtest" class="tabpanel">
  <div class="panel">
    <h2>Historical backtest</h2>
    <div class="small" style="line-height:2">
      {bt_period}<br>
      Trades <b>{bt_trades}</b> &nbsp;·&nbsp; Win rate <b>{bt_winrate:.1f}%</b> &nbsp;·&nbsp;
      Net P&L <b class="{'pos' if bt_net >= 0 else 'neg'}">{bt_net:+,.0f}</b> &nbsp;·&nbsp;
      PF <b>{bt_pf}</b> &nbsp;·&nbsp; Max DD <b>{bt_dd}%</b>
    </div>
    <div style="overflow-x:auto;margin-top:10px">
      <table>
        <tr><th>Day</th><th>P&L</th></tr>
        {daily_rows}
      </table>
    </div>
  </div>
</section>

<!-- ============ Stop-Loss Sweep ============ -->
<section id="tab-sweep" class="tabpanel">
  <div class="panel">
    <h2>Stop-loss sweep - last 40 trading days (1m exits)</h2>
    <div style="overflow-x:auto">
      <table>
        <tr><th>Stop</th><th>Target</th><th>Lock</th><th>Trades</th><th>Win%</th><th>Net INR</th><th>PF</th></tr>
        {sweep_rows}
      </table>
    </div>
    <div class="small muted" style="margin-top:8px">
      Lock-profit ON is the default and is what makes the plan profitable; widening the stop adds margin for winners to run.
    </div>
  </div>
</section>

<!-- ============ Trades ============ -->
<section id="tab-trades" class="tabpanel">
  <div class="panel">
    <h2>Trade log</h2>
    <div style="overflow-x:auto">
      <table>
        <tr><th>Time</th><th>Instrument</th><th>Side</th><th>Lots</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Setup</th><th>P&L</th></tr>
        {trade_rows}
      </table>
    </div>
  </div>
</section>

<!-- ============ System ============ -->
<section id="tab-system" class="tabpanel">
  <div class="panel">
    <h2>System</h2>
    <div class="small" style="line-height:2">
      Mode <b>{mode_tag}</b> &nbsp;·&nbsp; Lots <b>{cfg_module.DEFAULT_LOTS}</b> &nbsp;·&nbsp; Max trades/day <b>{cfg_module.MAX_TRADES_PER_DAY}</b><br>
      Target <b>{cfg_module.PROFIT_TARGET_PCT*100:.1f}%</b> &nbsp;·&nbsp; Stop <b>{cfg_module.STOP_LOSS_PCT*100:.1f}%</b> &nbsp;·&nbsp;
      Risk/trade <b>{cfg_module.RISK_PER_TRADE_PCT*100:.1f}%</b> &nbsp;·&nbsp; Daily loss cap <b>{cfg_module.MAX_DAILY_LOSS_PCT*100:.0f}%</b><br>
      Lock-profit <b>{'ON' if getattr(cfg_module, 'LOCK_PROFIT_ENABLED', False) else 'OFF'}</b> &nbsp;·&nbsp;
      ML <b>{'ON' if getattr(cfg_module, 'ML_ENABLED', False) else 'OFF'}</b> ({getattr(cfg_module, 'ML_MODEL', '-')}) &nbsp;·&nbsp;
      Meta-label <b>{'ON' if getattr(cfg_module, 'META_ENABLED', False) else 'OFF'}</b><br>
      Expiry <b>{getattr(cfg_module, 'OPTION_EXPIRY_BUCKET', 'current_week')}</b> &nbsp;·&nbsp;
      Score = Trend*0.30 + Momentum*0.25 + S/R*0.25 + Volume*0.20
    </div>
  </div>
</section>

</div>

<script>
// ---- tabs ----
document.querySelectorAll('nav.tabs button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tabpanel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  }});
}});

// ---- equity curve ----
const EQUITY = {equity_points};
function drawEquity() {{
  const cv = document.getElementById('equity');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const W = cv.width = cv.clientWidth, H = cv.height;
  ctx.clearRect(0,0,W,H);
  if (!EQUITY.length) {{ ctx.fillStyle='#7a8ba3'; ctx.fillText('no equity data', 10, 20); return; }}
  const pts = EQUITY.map(e => e[1]);
  let lo = Math.min(...pts), hi = Math.max(...pts);
  const pad = 14;
  const y = v => H - pad - (v - lo) / ((hi - lo) || 1) * (H - pad * 2);
  const x = i => i * W / (pts.length - 1 || 1);
  ctx.strokeStyle = '#56d4dd'; ctx.lineWidth = 2; ctx.beginPath();
  pts.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)));
  ctx.stroke();
  ctx.fillStyle = '#7a8ba3'; ctx.font = '10px sans-serif';
  ctx.fillText(lo.toFixed(0), 2, H - 2);
  ctx.fillText(hi.toFixed(0), 2, y(hi) - 2);
}}

// ---- live board polling ----
async function pollBoard() {{
  try {{
    const r = await fetch('/api/board');
    const b = await r.json();
    if (b && b.status === 'live' && b.spot) {{
      document.getElementById('liveSpot').textContent = b.spot.toFixed(2);
      const ch = document.getElementById('liveChange');
      ch.textContent = (b.day_change_pct >= 0 ? '▲ +' : '▼ ') + b.day_change_pct.toFixed(2) + '%';
      ch.style.color = b.day_change_pct >= 0 ? '#3ddc84' : '#f85149';
      const dir = document.getElementById('liveDir');
      dir.textContent = b.direction;
      dir.style.color = b.direction === 'BULLISH' ? '#3ddc84' : b.direction === 'BEARISH' ? '#f85149' : '#e3b341';
      document.getElementById('liveMode').textContent = 'LIVE';
      document.getElementById('liveSpotBig').textContent = 'NIFTY ' + b.spot.toFixed(2);
      document.getElementById('liveInfo').textContent =
        (b.day_change_pct >= 0 ? '▲ +' : '▼ ') + b.day_change_pct.toFixed(2) + '% today' +
        (b.chain_updated ? ' | chain ' + b.chain_updated.replace('T',' ').slice(0,19) : '');
      if (b.chain && b.chain.length) {{
        const rows = b.chain.map(c =>
          '<tr><td>' + c.strike.toFixed(0) + '</td><td>' + (c.ce_ltp || (c.model_premium ? c.model_premium.toFixed(2) : '-')) +
          '</td><td>' + (c.ce_oi || 0) + '</td><td>' + (c.pe_ltp || '-') + '</td><td>' + (c.pe_oi || 0) + '</td></tr>').join('');
        document.getElementById('liveChainTable').innerHTML =
          '<tr><th>Strike</th><th>CE LTP</th><th>CE OI</th><th>PE LTP</th><th>PE OI</th></tr>' + rows;
      }}
    }} else {{
      document.getElementById('liveInfo').textContent =
        'Live board off (add DHAN creds or use --live-board). Pre-market shows modelled chain.';
      document.getElementById('liveMode').textContent = 'PAPER';
    }}
  }} catch (e) {{
    document.getElementById('liveMode').textContent = 'PAPER';
  }}
}}
setInterval(pollBoard, 5000);
pollBoard();
window.addEventListener('resize', drawEquity);
drawEquity();

// ---- paper/live mode trigger button ----
let MODE_KEY = '';
async function pollMode() {{
  try {{
    const r = await fetch('/api/mode');
    const m = await r.json();
    const btn = document.getElementById('modeBtn');
    if (!btn) return;
    if (m.mode === 'live') {{
      btn.textContent = 'LIVE';
      btn.classList.add('live');
      document.getElementById('liveMode').textContent = 'LIVE';
    }} else {{
      btn.textContent = 'PAPER';
      btn.classList.remove('live');
      document.getElementById('liveMode').textContent = 'PAPER';
    }}
  }} catch (e) {{}}
}}
async function toggleMode() {{
  const btn = document.getElementById('modeBtn');
  const isLive = btn.classList.contains('live');
  if (!isLive) {{
    const ok = confirm('SWITCH TO LIVE TRADING?\\n\\nReal orders will be placed on your Dhan account.\\nDaily loss halt 1% | monthly halt 5% | 5 lots.');
    if (!ok) return;
  }}
  const body = JSON.stringify({{ mode: isLive ? 'paper' : 'live' }});
  const headers = {{ 'Content-Type': 'application/json' }};
  if (MODE_KEY) headers['X-PROXY-KEY'] = MODE_KEY;
  const r = await fetch('/api/mode', {{ method: 'POST', headers, body }});
  if (r.status === 403) {{
    MODE_KEY = prompt('Remote mode switch needs the PROXY_MODE_KEY:');
    if (MODE_KEY) return toggleMode();
    alert('Mode switch denied (set PROXY_MODE_KEY env to allow remote toggling).');
    return;
  }}
  const m = await r.json();
  if (m.mode) {{ btn.textContent = m.mode === 'live' ? 'LIVE' : 'PAPER'; btn.classList.toggle('live', m.mode === 'live'); }}
}}
const mb = document.getElementById('modeBtn');
if (mb) mb.addEventListener('click', toggleMode);
setInterval(pollMode, 5000);
pollMode();
</script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path