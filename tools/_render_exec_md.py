"""Render reports/v41/walkforward_2026_conf_gt80_exec.json -> reports/
walkforward_2026_conf_gt80_exec.md (mirrors the earlier mid report style)."""
import os, sys, json

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SRC = "reports/v41/walkforward_2026_conf_gt80_exec.json"
DST = "reports/walkforward_2026_conf_gt80_exec.md"

with open(SRC, encoding="utf-8") as fh:
    data = json.load(fh)

engines = data["engines"]
w = data["window"]
L = []
A = L.append
A("# 2026 YTD Walk-Forward Backtest — conf>80, MID vs EXECUTABLE fills")
A("")
A("Window: **" + w + "** (data CSVs through the 07-Sep-2026 session).  Harness: honest warm backtest, "
  "1m exit resolution, V4 delayed reverse, month-reset, per-engine live profiles.")
A("")
A("## Fill models compared (same harness, same window)")
A("")
A("| model | entry fill | protective exits (lock/stop/target) | market exits (time/reverse/day-end) |")
A("|---|---|---|---|")
A("| **mid baseline** | mid premium (the published ceiling) | trigger when the MID crosses the level | at mid |")
A("| **exec (spread-charged)** | pays the ASK once (0.5/1.0/2.0% of premium per side) | trigger when the EXECUTABLE bid/ask crosses; fill AT the set level | cross the bid/ask (pay the second side) |")
A("")
A("NIFTY is shown at exec 0.5%/side (the live paper model default is 0.4-0.9% per side).  FINNIFTY runs the "
  "spread LADDER 0.5/1.0/2.0%/side because its real two-way is still unmeasured - the ladder is the honest "
  "sensitivity, not one pretending number.")
A("")
A("> These are backtest numbers under the honest warm harness.  They are CEILINGS, not live claims - live NIFTY "
  "runs 0.5% risk and FINNIFTY is still PAPER gated.  The per-month view is a walk-forward slice; do not "
  "over-read any single month.")
A("")
for tag in ("NIFTY", "FINNIFTY"):
    eng = engines[tag]
    A("## " + tag + " options — conf>80 (count of trades and how many were positive)")
    A("")
    A("| fill model | trades | positive | win% | net INR | PF |")
    A("|---|---|---|---|---|---|")
    order = [k for k in eng if k.startswith("mid")] +             sorted([k for k in eng if k.startswith("exec")], key=lambda x: float(x.split("%")[0].split()[-1]))
    for label in order:
        s = eng[label]["conf_gt80"]
        if not s.get("trades"):
            A("| " + label + " | 0 | 0 | - | - | - |")
            continue
        net = ("{0:+,.0f}".format(s["net"]) if s.get("net") is not None else "-")
        A("| " + label + " | " + str(s["trades"]) + " | **" + str(s.get("positive", 0)) +
          "** | " + str(s.get("win_pct")) + " | " + net + " | " + str(s.get("pf")) + " |")
    A("")
    # monthly tables per model
    for label in order:
        monthly = eng[label].get("monthly_conf_gt80") or {}
        if not monthly:
            continue
        A("### " + tag + " — " + label + ", conf>80 by month")
        A("")
        A("| month | trades | positive | win% | net |")
        A("|---|---|---|---|---|")
        for mk in sorted(monthly):
            ms = monthly[mk]
            net_s = ("{0:+,.0f}".format(ms["net"]) if ms.get("net") is not None else "-")
            A("| " + mk + " | " + str(ms["trades"]) + " | " + str(ms.get("positive", 0)) +
              " | " + str(ms.get("win_pct")) + " | " + net_s + " |")
        A("")

# summary reading
def fmt(s):
    if not s.get("trades"):
        return "0 trades"
    net = ("{0:+,.0f}".format(s["net"]) if s.get("net") is not None else "-")
    return str(s["trades"]) + " tr, " + str(s.get("positive", 0)) + " positive (" +            str(s.get("win_pct")) + "%), net " + net

A("## Reading")
A("")
for tag in ("NIFTY", "FINNIFTY"):
    eng = engines[tag]
    mid = eng.get("mid baseline", {}).get("conf_gt80", {})
    ex = next((v for k, v in eng.items() if k.startswith("exec 0.5%")), {})
    ex80 = ex.get("conf_gt80", {}) if ex else {}
    A("- **" + tag + "** mid baseline: " + fmt(mid) + ".  Exec 0.5%/side: " + fmt(ex80) + ".")
if "FINNIFTY" in engines:
    fin = engines["FINNIFTY"]
    e2 = fin.get("exec 2.0%/side", {}).get("conf_gt80", {})
    if e2.get("trades"):
        A("- FINNIFTY at exec 2.0%/side: " + fmt(e2) +
          " - at that spread most of the mid-model edge is crossing cost (the real-chain spread question).")
with open(DST, "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print("wrote", DST)
