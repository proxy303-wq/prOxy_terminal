"""PrOxy Terminal - OPT-SELL dashboard generator.

Writes a self-contained reports/dashboard_optsell.html from the live engine
state (optsell_state.json) + journal (proxy_state_optsell.sqlite).

    python tools/opt_dashboard.py
"""
import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from proxy import mode as _mode


def esc(x):
    return (str(x).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def build(state_path=None, db_path=None, out_html=None):
    base = os.path.join(_ROOT, 'reports')
    state_path = state_path or os.path.join(base, 'optsell_state.json')
    db_path = db_path or os.path.join(base, 'proxy_state_optsell.sqlite')
    out_html = out_html or os.path.join(base, 'dashboard_optsell.html')
    st = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding='utf-8') as fh:
                st = json.load(fh)
        except Exception:
            st = {}
    mode = _mode.get_mode('optsell')
    rows = []
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect('file:' + db_path + '?mode=ro', uri=True)
            rows = conn.execute(
                'SELECT ts, family, lots, credit_inr, max_loss_inr, exit_reason, pnl_inr '
                'FROM optsell_trades ORDER BY id DESC LIMIT 20').fetchall()
            conn.close()
        except Exception:
            rows = []
    active = st.get('active') or {}
    today = float(st.get('realized_pnl_today') or 0.0)
    total = float(st.get('realized_pnl_total') or 0.0)
    cap = float(st.get('capital') or 0.0)
    act_txt = 'none (flat)'
    if active:
        act_txt = str(active.get('family')) + ' ' + str(active.get('lots')) + 'L expiry ' + \
                  str(active.get('expiry')) + ' credit ' + format(active.get('credit_inr') or 0, ',.0f')
    L = []
    L.append('<!doctype html><html><head><meta charset=utf-8>')
    L.append('<title>OPT-SELL Dashboard</title><style>')
    L.append('body{font-family:system-ui;margin:24px;background:#0f1420;color:#e8ecf4}')
    L.append('h1{color:#7fd1ff}.card{background:#1a2130;border:1px solid #2b3550;border-radius:10px;padding:14px 18px;margin:12px 0}')
    L.append('table{border-collapse:collapse;width:100%}td,th{border:1px solid #2b3550;padding:6px 10px;text-align:left}')
    L.append('th{color:#9fb6d9}.pos{color:#7ce38b}.neg{color:#ff8b8b}</style></head><body>')
    L.append('<h1>NIFTY OPT-SELL &mdash; ' + mode.upper() + '</h1>')
    L.append('<div class=card>Capital ' + format(cap, ',.0f') + ' | Today ' +
             format(today, '+,.0f') + ' | Net ' + format(total, '+,.0f') + '</div>')
    L.append('<div class=card><b>Active structure:</b> ' + esc(act_txt) + '</div>')
    L.append('<h2>Recent journal</h2><table><tr><th>ts</th><th>family</th><th>lots</th>')
    L.append('<th>credit</th><th>max loss</th><th>exit</th><th>pnl</th></tr>')
    for r in rows:
        cls = ''
        if r[6] and r[6] > 0:
            cls = 'pos'
        elif r[6] and r[6] < 0:
            cls = 'neg'
        L.append('<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td><td>' + str(r[2]) +
                 '</td><td>' + format(r[3] or 0, ',.0f') + '</td><td>' + format(r[4] or 0, ',.0f') +
                 '</td><td>' + esc(r[5]) + '</td><td class="' + cls + '">' +
                 format(r[6] or 0, '+,.0f') + '</td></tr>')
    L.append('</table></body></html>')
    html = chr(10).join(L)
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, 'w', encoding='utf-8') as fh:
        fh.write(html)
    return out_html


def main():
    print('dashboard ->', build())


if __name__ == '__main__':
    main()
