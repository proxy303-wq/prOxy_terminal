"""Recompute confident-tail correctness from the replay logs (correct logic:
market_up = (signal==BUY)==won ; ML-correct = (ml_dir==BUY)==market_up)."""
import re

def read_text(path):
    try:
        return open(path, encoding="utf-16").read()
    except Exception:
        return open(path, encoding="utf-8", errors="replace").read()

ROW = re.compile(
    r"^\s*(\d+)\s+(BUY|SELL)\s+@(\d\d:\d\d)\s+(NIFTY.*?\s(?:CE|PE))\s+"
    r"(LOCK_PROFIT|REVERSE_SIGNAL|STOP_LOSS_HIT[^ ]*|TIME_STOP[^ ]*)\s+"
    r"([+-][\d,]+)\s*->\s*(ALLOW|BLOCK@\w+)\s+ML\s+(BUY|SELL)\s+(\d+)%")

def tally(path):
    rows = []
    for line in read_text(path).splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        tid, sig, t, inst, reason, pnl_s, verdict, ml_dir, p = m.groups()
        pnl = float(pnl_s.replace(",", ""))
        won = pnl > 0
        market_up = (sig == "BUY") == won
        ml_bull = ml_dir == "BUY"
        p = float(p)
        conf = (p >= 60) if ml_bull else ((100 - p) >= 60)
        correct = ml_bull == market_up
        rows.append((tid, sig, won, market_up, ml_dir, p, conf, correct, verdict, pnl))
    n = len(rows)
    if n == 0:
        print(f"=== {path} ===  (no rows parsed)")
        return
    conf_rows = [r for r in rows if r[6]]
    agree = sum(1 for r in rows if r[7])
    conf_ok = sum(1 for r in conf_rows if r[7])
    wrong_hi = [r for r in conf_rows if not r[7]]
    print(f"=== {path} ===")
    print(f"trades {n} | direction-vs-outcome {agree}/{n} ({agree/n*100:.0f}%)")
    if conf_rows:
        print(f"confident (>=60%) calls: {len(conf_rows)}/{n} | correct {conf_ok}/{len(conf_rows)} "
              f"({conf_ok/len(conf_rows)*100:.0f}%)")
        print(f"high-confidence WRONG calls: {len(wrong_hi)} "
              f"(their trades' pnl: {sum(r[9] for r in wrong_hi):+,.0f})")
    print()

for p in ("logs/_gate_replay2.log", "logs/_gate_replay2_h6.log"):
    tally(p)
