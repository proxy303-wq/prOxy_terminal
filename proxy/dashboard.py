"""
PrOxy Trading Terminal - Dashboard Builder
==========================================

Builds a single self-contained HTML dashboard (dark terminal theme,
pure canvas charts - no CDN, works offline).  Data is embedded as JSON
at build time:

    KPI cards (P&L, win rate, monthly progress vs 62,500 INR)
    Candlestick chart of recent bars
    Equity curve
    Trade log
    Rules / plan / lot-size panels

Usage:
    from proxy.dashboard import build_dashboard
    build_dashboard(snapshot, bars=df)   -> writes reports/dashboard.html
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

    # optional backtest panel values
    if backtest_report:
        bt_trades = backtest_report.get("trades", 0)
        bt_winrate = backtest_report.get("win_rate", 0.0)
        bt_net = backtest_report.get("net_pnl", 0.0)
        bt_pf = backtest_report.get("profit_factor", "-")
        bt_dd = backtest_report.get("max_drawdown_pct", 0.0)
        bt_period = backtest_report.get("period", "-") + " | exits " + backtest_report.get("exit_resolution", "5m")
    else:
        bt_trades, bt_winrate, bt_net, bt_pf, bt_dd, bt_period = 0, 0.0, 0.0, "-", 0.0, "-"

    # stop-loss sweep panel
    sweep_panel_html = ""
    if sweep:
        rows = "".join(
            f"<tr><td>{r['stop_pct']*100:.2f}%</td><td>{r['target_pct']*100:.2f}%</td>"
            f"<td>{'on' if r['lock'] else 'off'}</td><td>{r['trades']}</td>"
            f"<td>{r['win_rate']:.1f}%</td>"
            f"<td class=\"{'pos' if r['net_pnl'] > 0 else 'neg'}\">{r['net_pnl']:+,.0f}</td>"
            f"<td>{r['pf'] if r['pf'] is not None else '-'}</td></tr>"
            for r in sweep
        )
        sweep_panel_html = (
            '<div class="grid" style="margin-top:14px">'
            '<div class="panel"><h2>Stop-loss sweep - last 40 trading days (1m exits)</h2>'
            '<div style="overflow-x:auto"><table>'
            '<tr><th>Stop</th><th>Target</th><th>Lock</th><th>Trades</th><th>Win%</th><th>Net INR</th><th>PF</th></tr>'
            + rows +
            '</table></div>'
            '<div class="small muted" style="margin-top:6px">Lock-profit ON is the system default and '
            'is what makes the plan profitable; widening the stop adds margin for winners to run.</div>'
            '</div></div>'
        )

    # option-chain panel (ATM/ITM, time-decay view)
    chain_panel_html = ""
    if chain and chain.get("rows"):
        best = chain.get("best", {})
        best_key = (best.get("strike"), best.get("option_type"))
        trs = ""
        for row in sorted(chain["rows"], key=lambda x: (x["strike"], x["option_type"])):
            mark = "<b>&#9733;</b> " if (row["strike"], row["option_type"]) == best_key else ""
            trs += (f"<tr><td>{mark}{row['strike']:.0f}</td><td>{row['option_type']}</td>"
                    f"<td>{row['premium']:.2f}</td><td>{row['delta']:+.2f}</td>"
                    f"<td>{abs(row['theta_pct_day']):.2f}%</td><td>{row['moneyness']}</td></tr>")
        # expiries rows (time decay by expiry)
        exp_rows = ""
        for e in (chain.get("expiries") or []):
            mark = "<b>&#9654;</b>" if e.get("bucket") == getattr(cfg_module, "OPTION_EXPIRY_BUCKET", "current_week") else ""
            exp_rows += (f"<tr><td>{mark} {e['bucket']}</td><td>{e['date']}</td><td>{e['dte']}d</td>"
                         f"<td>{e['atm_premium']:.2f}</td><td>{e['atm_theta_pct']:.2f}%</td></tr>")
        chain_panel_html = f'''
  <div class="grid" style="margin-top:14px">
    <div class="panel">
      <h2>Expiries - time decay by expiry (&#9654; = trade default)</h2>
      <div style="overflow-x:auto">
      <table>
        <tr><th>Bucket</th><th>Date</th><th>DTE</th><th>ATM prem</th><th>ATM theta %/day</th></tr>
        {exp_rows}
      </table>
      </div>
      <div class="small muted" style="margin-top:6px">
        Shorter expiry = higher theta tax.  Trade a longer expiry to cut
        decay, at the cost of a higher premium per lot.
      </div>
    </div>
  </div>

  <div class="grid" style="margin-top:14px">
    <div class="panel">
      <h2>Option chain - ATM/ITM (lowest time-decay &#9733;)</h2>
      <div style="overflow-x:auto">
      <table>
        <tr><th>Strike</th><th>Type</th><th>Premium</th><th>Delta</th><th>Theta %/day</th><th>Moneyness</th></tr>
        {trs}
      </table>
      </div>
      <div class="small muted" style="margin-top:6px">
        Recommended long strike: {best.get('strike', 0):.0f} {best.get('option_type', 'CE')}
        (theta tax {abs(best.get('theta_pct_day', 0)):.2f}%/day, delta {best.get('delta', 0):.2f}) -
        ITM strikes decay slower than ATM.  Toggle SELECT_BY_DELTA in config.py to auto-select.
      </div>
    </div>
  </div>'''

    bars_data = []
    if bars is not None and len(bars) > 0:
        for _, row in bars.tail(120).iterrows():
            t = str(row.get("date", row.name)) if "date" in bars.columns else str(row.name)
            bars_data.append([t, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])])

    trade_rows = "".join(
        f"""<tr>
          <td>{html.escape(str(t.get('entry_time', ''))[:16])}</td>
          <td>{html.escape(str(t.get('instrument', '')))}</td>
          <td>{html.escape(str(t.get('direction', '')))}</td>
          <td>{t.get('lots', '')}</td>
          <td>{t.get('entry_premium', '')}</td>
          <td>{t.get('exit_premium', '')}</td>
          <td>{html.escape(str(t.get('exit_reason', '')))}</td>
          <td>{html.escape(str(t.get('setup_type', '')))}</td>
          <td>{t.get('confidence', '')}%</td>
          <td class="{'pos' if (t.get('pnl') or 0) > 0 else 'neg'}">{t.get('pnl', 0):+,.2f}</td>
        </tr>""" for t in trades[:25])

    equity_points = json.dumps(equity)
    bars_json = json.dumps(bars_data)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --bg:#0b0f14; --panel:#111822; --panel2:#0f1520; --line:#1e293b;
    --text:#dbe4f0; --muted:#7a8ba3; --green:#3ddc84; --red:#f85149;
    --cyan:#56d4dd; --yellow:#e3b341; --purple:#a78bfa;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text);
         font-family:'Cascadia Code','Consolas','SF Mono',monospace; padding:20px; }}
  header {{ display:flex; justify-content:space-between; align-items:center;
            border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:18px; }}
  .brand {{ font-size:22px; font-weight:700; letter-spacing:1px; color:var(--cyan); }}
  .brand span {{ color:var(--muted); font-weight:400; }}
  .tagline {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .grid {{ display:grid; gap:14px; }}
  .kpis {{ grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; }}
  .panel h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:1px;
               color:var(--muted); margin-bottom:12px; }}
  .kpi-label {{ font-size:11px; color:var(--muted); text-transform:uppercase; }}
  .kpi-value {{ font-size:24px; font-weight:700; margin:4px 0; }}
  .kpi-sub {{ font-size:11px; color:var(--muted); }}
  .cols {{ grid-template-columns:2fr 1fr; }}
  @media(max-width:900px) {{ .cols {{ grid-template-columns:1fr; }} }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ color:var(--muted); font-weight:600; text-transform:uppercase; font-size:10px; }}
  .pos {{ color:var(--green); }} .neg {{ color:var(--red); }}
  .bar-track {{ background:var(--panel2); height:18px; border-radius:9px; overflow:hidden;
                border:1px solid var(--line); }}
  .bar-fill {{ height:100%; background:linear-gradient(90deg,var(--green),var(--cyan));
               width:{min(100, max(0, monthly_progress)):.1f}%; }}
  .muted {{ color:var(--muted); }} .small {{ font-size:11px; }}
  canvas {{ width:100%; display:block; background:var(--panel2); border-radius:8px; }}
  ul {{ padding-left:18px; font-size:12px; line-height:1.8; color:var(--text); }}
  .chip {{ display:inline-block; background:var(--panel2); border:1px solid var(--line);
           border-radius:20px; padding:2px 10px; font-size:11px; margin:2px; color:var(--cyan); }}
  .lot-answer {{ background:var(--panel2); border-left:3px solid var(--cyan);
                padding:12px; border-radius:6px; font-size:13px; line-height:1.9; }}
</style>
</head>
<body>
<header>
  <div>
    <div class="brand">PrOxy <span>TRADING TERMINAL</span></div>
    <div class="tagline">NIFTY options | 5,00,000 capital | 12.5%/month | 62,500 INR/month</div>
  </div>
  <div class="small muted" style="text-align:right">
    <div class="live-strip">
      <span class="chip">NIFTY <b id="liveSpot">--</b></span>
      <span class="chip" id="liveChange">--</span>
      <span class="chip" id="liveDir">--</span>
      <span class="chip" id="liveMode">--</span>
      <span class="chip" id="liveChainState">--</span>
    </div>
    <div style="margin-top:6px">Generated {html.escape(str(snapshot.get('generated_at', '')))} IST</div>
  </div>
</header>

<div class="grid kpis">
  {_kpi("NET P&L", f"{stats.get('net_pnl', 0):+,.2f} INR", f"{stats.get('trades', 0)} trades", 'var(--green)' if stats.get('net_pnl', 0) >= 0 else 'var(--red)')}
  {_kpi("WIN RATE", f"{stats.get('win_rate', 0):.1f}%", f"target 75%", 'var(--cyan)')}
  {_kpi("PROFIT FACTOR", f"{stats.get('profit_factor', 0):.2f}", "gross win / gross loss", 'var(--purple)')}
  {_kpi("MONTH P&L", f"{monthly_pnl:+,.2f}", f"target {monthly_target:,.0f} INR", 'var(--green)' if monthly_pnl >= 0 else 'var(--red)')}
  {_kpi("TODAY", f"{state.get('realized_pnl_today', 0):+,.2f}", f"{state.get('trades_today', 0)} trades", 'var(--yellow)')}
  {_kpi("EQUITY", f"{capital + state.get('realized_pnl_total', 0):,.0f}", "capital + realized", 'var(--cyan)')}
</div>

<div class="grid" style="margin-top:14px">
  <div class="panel">
    <h2>Portfolio analytics</h2>
    <div class="small">
      <span class="chip">Sharpe {pfolio.get('sharpe', '-')}</span>
      <span class="chip">Sortino {pfolio.get('sortino', '-')}</span>
      <span class="chip">Calmar {pfolio.get('calmar', '-')}</span>
      <span class="chip">MaxDD {pfolio.get('max_drawdown_pct', 0)}%</span>
      <span class="chip">Expectancy {pfolio.get('expectancy', '-')} INR</span>
      <span class="chip">PF {pfolio.get('profit_factor', '-')}</span>
      <span class="chip">Kelly {pfolio.get('kelly_fraction', '-')}</span>
      <span class="chip">Avg hold {pfolio.get('avg_hold_minutes', '-')}m</span>
    </div>
  </div>
</div>

<div class="grid" style="margin-top:14px">
  <div class="panel">
    <h2>Live option chain (Dhan WebSocket) - refreshes every 5s</h2>
    <div class="small muted" style="margin-bottom:8px" id="liveChainInfo">Waiting for live data...</div>
    <div style="overflow-x:auto">
      <table id="liveChainTable">
        <tr><th>Strike</th><th>CE LTP</th><th>CE OI</th><th>PE LTP</th><th>PE OI</th><th>Spot</th></tr>
        <tr><td colspan="6" class="muted">Connect with: python run_terminal.py dashboard --serve --live-board</td></tr>
      </table>
    </div>
  </div>
</div>

{sweep_panel_html}

<div class="grid" style="margin-top:14px">
  <div class="panel">
    <h2>Historical backtest (NIFTY 5m)</h2>
    <div class="small">
      <span class="chip">{bt_trades} trades</span>
      <span class="chip">win rate {bt_winrate:.1f}%</span>
      <span class="chip">net P&L {bt_net:+,.0f} INR</span>
      <span class="chip">profit factor {bt_pf}</span>
      <span class="chip">max DD {bt_dd}%</span>
      <span class="chip">{bt_period}</span>
    </div>
  </div>
</div>

{chain_panel_html}

<div class="grid" style="margin-top:14px">
  <div class="panel">
    <h2>Monthly progress vs 62,500 INR target</h2>
    <div class="bar-track"><div class="bar-fill"></div></div>
    <div class="small muted" style="margin-top:6px">
      {monthly_pnl:+,.2f} / {monthly_target:,.0f} INR ({monthly_progress:.1f}%)
      &nbsp;|&nbsp; wins {state.get('wins', 0)} / losses {state.get('losses', 0)}
    </div>
  </div>
</div>

<div class="grid cols" style="margin-top:14px">
  <div class="panel">
    <h2>NIFTY 5-minute candles (last 120 bars)</h2>
    <canvas id="candles" height="320"></canvas>
  </div>
  <div class="panel">
    <h2>Equity curve</h2>
    <canvas id="equity" height="320"></canvas>
  </div>
</div>

<div class="grid cols" style="margin-top:14px">
  <div class="panel">
    <h2>Trade log</h2>
    <div style="overflow-x:auto">
    <table>
      <tr><th>Time</th><th>Instrument</th><th>Side</th><th>Lots</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Setup</th><th>Conf</th><th>P&L</th></tr>
      {trade_rows or '<tr><td colspan="10" class="muted">No trades yet</td></tr>'}
    </table>
    </div>
  </div>
  <div class="panel">
    <h2>The plan</h2>
    <ul>
      <li>Capital 5,00,000 INR | NIFTY lot size <b>65</b></li>
      <li>Profit target <b>+1%</b> | Stop-loss <b>-0.5%</b> (R:R 2:1)</li>
      <li>Risk per trade <b>0.5%</b> (2,500 INR) | Max daily loss 1% | Max monthly loss 5%</li>
      <li>Score = Trend*0.30 + Momentum*0.25 + S/R*0.25 + Volume*0.20</li>
      <li>BUY &gt; +0.15 (CE) | SELL &lt; -0.15 (PE) | WAIT otherwise</li>
      <li>Price-action / candlestick confirmation + confidence &ge; 70%</li>
      <li>Trade window 9:15 - 14:45 | force exit 15:15</li>
    </ul>
    <h2 style="margin-top:16px">Lot-size answer (NIFTY 65)</h2>
    <div class="lot-answer">
      Risk 2,500 / (65 lots &times; 0.75 stop) &asymp; <b>51 lots max by risk</b><br>
      Recommended: <span class="chip">1-2 conservative</span>
      <span class="chip">3-5 balanced (terminal default 3)</span>
      <span class="chip">10 for full 5,000/day target</span>
    </div>
    <div class="small muted" style="margin-top:10px">
      Win-rate target 75% &rarr; 12.5%/month &rarr; 5,00,000 &rarr; ~20.6L in Year 1
    </div>
  </div>
</div>

<script>
const BARS = {bars_json};
const EQUITY = {equity_points};

function drawCandles() {{
  const cv = document.getElementById('candles');
  const ctx = cv.getContext('2d');
  const W = cv.width = cv.clientWidth, H = cv.height;
  ctx.clearRect(0,0,W,H);
  if (!BARS.length) {{ ctx.fillStyle='#7a8ba3'; ctx.fillText('no bar data', 10, 20); return; }}
  let lo = Infinity, hi = -Infinity;
  BARS.forEach(b => {{ lo = Math.min(lo, b[2], b[3]); hi = Math.max(hi, b[1], b[4]); }});
  const pad = 12, n = BARS.length, cw = W / n;
  const y = v => H - pad - (v - lo) / (hi - lo || 1) * (H - pad * 2);
  const x = i => i * cw + cw / 2;
  ctx.strokeStyle = '#7a8ba3'; ctx.fillStyle = '#7a8ba3';
  ctx.font = '10px monospace';
  for (let g = 0; g <= 4; g++) {{
    const v = lo + (hi - lo) * g / 4;
    ctx.beginPath(); ctx.moveTo(0, y(v)); ctx.lineTo(W, y(v));
    ctx.strokeStyle = 'rgba(122,139,163,0.12)'; ctx.stroke();
    ctx.fillStyle = '#7a8ba3'; ctx.fillText(v.toFixed(0), 2, y(v) - 2);
  }}
  BARS.forEach((b, i) => {{
    const up = b[4] >= b[1];
    ctx.strokeStyle = ctx.fillStyle = up ? '#3ddc84' : '#f85149';
    ctx.beginPath(); ctx.moveTo(x(i), y(b[2])); ctx.lineTo(x(i), y(b[3])); ctx.stroke();
    const bw = Math.max(2, cw * 0.6);
    ctx.fillRect(x(i) - bw/2, y(Math.max(b[1], b[4])), bw, Math.max(1, Math.abs(y(b[1]) - y(b[4]))));
  }});
}}

function drawEquity() {{
  const cv = document.getElementById('equity');
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
  ctx.fillStyle = '#7a8ba3'; ctx.font = '10px monospace';
  ctx.fillText(lo.toFixed(0), 2, H - 2);
  ctx.fillText(hi.toFixed(0), 2, y(hi) - 2);
}}

window.addEventListener('resize', () => {{ drawCandles(); drawEquity(); }});
drawCandles();
drawEquity();

// ---- live board polling (Dhan WebSocket data + option chain) ----
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
      if (b.chain && b.chain.length) {{
        const rows = b.chain.map(c =>
          '<tr><td>' + c.strike.toFixed(0) + '</td><td>' + (c.ce_ltp || 0) + '</td><td>' +
          (c.ce_oi || 0) + '</td><td>' + (c.pe_ltp || 0) + '</td><td>' + (c.pe_oi || 0) +
          '</td><td>' + b.spot.toFixed(2) + '</td></tr>').join('');
        document.getElementById('liveChainTable').innerHTML =
          '<tr><th>Strike</th><th>CE LTP</th><th>CE OI</th><th>PE LTP</th><th>PE OI</th><th>Spot</th></tr>' + rows;
        document.getElementById('liveChainInfo').textContent =
          'Updated ' + (b.chain_updated || '').replace('T', ' ').slice(0, 19) + ' IST';
      }}
    }} else {{
      document.getElementById('liveChainInfo').textContent =
        'Live board off (add DHAN creds or use --live-board). Showing modelled chain.';
      document.getElementById('liveMode').textContent = 'PAPER';
    }}
  }} catch (e) {{
    document.getElementById('liveMode').textContent = 'PAPER';
  }}
}}
setInterval(pollBoard, 5000);
pollBoard();

// ---- live trade/state refresh ----
async function pollState() {{
  try {{
    const r = await fetch('/api/state');
    const s = await r.json();
    if (!s || !s.state) return;
    const st = s.state, pf = s.portfolio || {{}};
    const upd = (id, txt, color) => {{
      const el = document.getElementById(id);
      if (el) {{ el.textContent = txt; if (color) el.style.color = color; }}
    }};
    upd('liveMode', s.state && s.state.realized_pnl_today !== undefined ? 'PAPER' : 'PAPER');
  }} catch (e) {{}}
}}
setInterval(pollState, 10000);
pollState();
</script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
