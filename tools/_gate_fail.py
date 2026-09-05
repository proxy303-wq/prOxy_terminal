"""Which gate kills BUY vs SELL asymmetrically?  For every raw-score signal,
test each gate component independently (mirrors scoring.generate_signal)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from collections import Counter
from proxy.indicators import calculate_indicators, rsi as _rsi
from proxy.price_action import analyze_price_action
from proxy.scoring import (_trend_component, _momentum_component, _sr_component,
                           _volume_component)
from proxy.backtest import load_csv as bt_load_csv
from tools._nifty_honesty import live_profile, months

WIN = "2026-07..2026-08"
df = bt_load_csv("data/NIFTY_5m.csv")
df = df[df["date"].dt.strftime("%Y-%m").isin(months(WIN))].reset_index(drop=True)
cfg = live_profile()

fail = {"BUY": Counter(), "SELL": Counter()}
passed = {"BUY": 0, "SELL": 0}
hist = []
for _, row in df.iterrows():
    bar = {"time": row["date"].to_pydatetime(), "open": float(row["open"]),
           "high": float(row["high"]), "low": float(row["low"]),
           "close": float(row["close"]), "volume": float(row.get("volume", 0.0) or 0.0)}
    hist.append(bar)
    if len(hist) > 160:
        hist = hist[-160:]
    if len(hist) < 40:
        continue
    frame = pd.DataFrame(hist).set_index(pd.to_datetime([b["time"] for b in hist]))
    frame = calculate_indicators(frame)
    pa = analyze_price_action(frame, cfg)
    structure, sr = pa["structure"], pa["support_resistance"]
    setup, patterns = pa["setup"], pa["patterns"]
    score = (cfg.SCORE_TREND_W * _trend_component(frame, structure, cfg) +
             cfg.SCORE_MOMENTUM_W * _momentum_component(frame, structure, cfg) +
             cfg.SCORE_SR_W * _sr_component(sr, structure["trend"], cfg) +
             cfg.SCORE_VOLUME_W * _volume_component(frame, structure["trend"], cfg))
    direction = "BUY" if score > cfg.SCORE_BUY_THRESHOLD else (
        "SELL" if score < cfg.SCORE_SELL_THRESHOLD else None)
    if not direction:
        continue

    # PA confirmation (mirror scoring.py)
    bull_pa = (setup and setup["bias"] == "BULLISH") or any(
        p.get("bullish") is True and p.get("bar", 0) == 0 and p["strength"] >= 50.0
        for p in patterns.values())
    bear_pa = (setup and setup["bias"] == "BEARISH") or any(
        p.get("bullish") is False and p.get("bar", 0) == 0 and p["strength"] >= 50.0
        for p in patterns.values())
    pa_ok = bull_pa if direction == "BUY" else bear_pa

    # confidence (mirror scoring._confidence)
    best = max(patterns.values(), key=lambda p: p["strength"]) if patterns else None
    conf = 50.0 + abs(score) * 40.0
    if setup:
        conf += setup["strength"] * 0.30
    if best:
        conf += best.get("strength", 0.0) * 0.20
    if (score > 0 and (setup and setup["strength"] > 0)) or (score < 0 and (setup and setup["strength"] > 0)):
        conf += 5.0
    rsi_val = float(_rsi(frame["close"], cfg.RSI_PERIOD).iloc[-1]) if len(frame) > cfg.RSI_PERIOD else 50.0
    rsi_ok = (rsi_val > cfg.RSI_ENTRY_GATE_BULL) if direction == "BUY" else (rsi_val < cfg.RSI_ENTRY_GATE_BEAR)
    conf_ok = conf >= cfg.MIN_CONFIDENCE_PCT
    adx_ok = float(frame["adx"].iloc[-1]) >= cfg.MIN_TREND_ADX if "adx" in frame.columns else True

    for name, ok in (("PA", pa_ok), ("RSI", rsi_ok), ("CONF", conf_ok), ("ADX", adx_ok)):
        if not ok:
            fail[direction][name] += 1
    if pa_ok and rsi_ok and conf_ok and adx_ok:
        passed[direction] += 1

for d in ("BUY", "SELL"):
    tot = passed[d] + sum(fail[d].values())
    print(f"{d:<5} raw {tot:>5} | passed {passed[d]:>4} ({passed[d]/tot*100:.1f}%) | failed: "
          + ", ".join(f"{k} {v} ({v/tot*100:.1f}%)" for k, v in fail[d].items()))
