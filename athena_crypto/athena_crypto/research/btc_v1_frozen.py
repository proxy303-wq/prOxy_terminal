"""ATHENA-BTC-V1.0 frozen benchmark - exact-spec backtester.

Reproduces the frozen specification from
`Athena_BTC_Strategy_Master_Document.docx` (section 11 asks for exactly this:
"Add a backtest mode that reproduces this specification exactly before adding
new features").

Frozen spec (do not tune the constants here; create versioned variants instead):
  instrument      BTCUSDT 5m candles (the supplied monthly files are the SPOT
                  klines channel; the doc specifies the perp - see FINDINGS)
  trend           EMA(192) / EMA(384) crossover on completed candles
  trend filter    ADX(14) >= 38
  vol filter      ATR(14) / close >= 0.10%
  direction       long and short, all hours, no volume/body/pullback filters
  entry           immediate crossover, fill at NEXT bar open with adverse slippage
  stop / target   3 x ATR(14) / 7 x ATR(14)
  sizing          risk 0.5% of current equity per trade, notional <= 10 x equity
  costs           maker 0.02% + 18% GST = 0.0236%/side, slippage 0.01%/side
  funding         NOT modelled (no funding data in the supplied datasets)
  intrabar        when a bar touches both stop and target, stop fills first
  one position at a time, no time stop, hold until SL/TP

This module deliberately shares no code with the athena_crypto strategy/risk
layer: the point is an independent reproduction of the frozen benchmark so the
engine implementation can be compared against something that cannot inherit its
bugs.

Usage (run from the athena_crypto/ project root):

    python -m athena_crypto.research.btc_v1_frozen --data-dir <dir> --json out.json
    python -m athena_crypto.research.btc_v1_frozen --data-dir <dir> --sweep
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# ----------------------------------------------------------------- frozen constants
EMA_FAST = 192
EMA_SLOW = 384
ADX_PERIOD = 14
ADX_MIN = 38.0
ATR_PERIOD = 14
ATR_PCT_MIN = 0.10          # percent of price
STOP_ATR = 3.0
TARGET_ATR = 7.0
RISK_FRAC = 0.005           # 0.5% of current equity per trade
LEVERAGE_CAP = 10.0         # notional <= 10 x equity
MAKER_FEE = 0.0002 * 1.18   # 0.02% + 18% GST = 0.0236% per side
SLIPPAGE = 0.0001           # 0.01% per side
START_EQUITY = 300000.0     # Rs 3,00,000 baseline


# ----------------------------------------------------------------- data loading
def load_binance(paths):
    """Load Binance monthly kline CSVs (1m or 5m, no header) into one frame.

    Columns: open_time(us), open, high, low, close, volume, close_time, ...
    The 12-column no-header layout is the SPOT monthly-klines export; the
    futures (um) export ships a header row. Returns a UTC-indexed frame with a
    `gap_bars` column marking missing candles.
    """
    frames = []
    for p in sorted(paths):
        df = pd.read_csv(p, header=None, usecols=[0, 1, 2, 3, 4, 5],
                         names=["t_us", "open", "high", "low", "close", "volume"])
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["time"] = (df["t_us"] // 1_000_000).astype("int64")
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    step = int(df["time"].diff().dropna().mode().iloc[0]) if len(df) > 2 else 300
    df["gap_bars"] = df["time"].diff().div(step).fillna(1).astype(int) - 1
    return df[["time", "dt", "open", "high", "low", "close", "volume", "gap_bars"]]


def load_binance_5m(paths):
    """Backwards-compatible alias (5m monthly klines)."""
    return load_binance(paths)


def build_minute_index(df1):
    """Index 1-minute bars by the 5-minute bucket they belong to.

    Returns (index, highs, lows) where index maps a 5m bucket start (epoch
    seconds) to the half-open [start, end) slice of the 1m arrays. Used to
    resolve intrabar sequencing instead of assuming the stop fills first.
    """
    bucket = (df1["time"].to_numpy(dtype="int64") // 300) * 300
    uniq, starts = np.unique(bucket, return_index=True)
    ends = np.append(starts[1:], len(bucket))
    index = {int(u): (int(s), int(e)) for u, s, e in zip(uniq, starts, ends)}
    return index, df1["high"].to_numpy(dtype=float), df1["low"].to_numpy(dtype=float)


# ----------------------------------------------------------------- indicators
def ema(series: pd.Series, length: int) -> pd.Series:
    """TA-Lib style EMA: seeded with the SMA of the first `length` values."""
    s = series.astype(float)
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if len(s) < length:
        return out
    seed = float(s.iloc[:length].mean())
    out.iloc[length - 1] = seed
    alpha = 2.0 / (length + 1.0)
    prev = seed
    vals = s.to_numpy(dtype=float)
    res = np.array(out.to_numpy(dtype=float), dtype=float, copy=True)
    for i in range(length, len(vals)):
        prev = prev + alpha * (vals[i] - prev)
        res[i] = prev
    return pd.Series(res, index=s.index)


def wilder_rma(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing, SMA-seeded (TA-Lib RMA). Input must be NaN-free."""
    n = len(values)
    out = np.full(n, np.nan)
    if n < period:
        return out
    first = float(np.mean(values[:period]))
    out[period - 1] = first
    prev = first
    for i in range(period, n):
        v = values[i]
        if math.isnan(v):
            v = 0.0
        prev = (prev * (period - 1) + v) / period
        out[i] = prev
    return out


def true_range(high, low, close):
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    return tr


def atr_wilder(high, low, close, period=ATR_PERIOD):
    return wilder_rma(true_range(high, low, close), period)


def adx_wilder(high, low, close, period=ADX_PERIOD):
    """Wilder ADX(14): +DM/-DM -> smoothed DI -> DX -> smoothed ADX.

    TR/+DM/-DM are averaged with Wilder's RMA, so the first DI/DX print sits at
    index period-1 and the first ADX print at index 2*period-1 (TA-Lib layout).
    """
    n = len(close)
    up = np.zeros(n)
    dn = np.zeros(n)
    up[1:] = np.diff(high)
    dn[1:] = -np.diff(low)
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(high, low, close)
    atr_ = wilder_rma(tr, period)
    plus_s = wilder_rma(plus_dm, period)
    minus_s = wilder_rma(minus_dm, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_s / atr_
        minus_di = 100.0 * minus_s / atr_
        denom = plus_di + minus_di
        dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / denom, 0.0)
    di_start = period - 1
    dx[:di_start] = np.nan
    adx = np.full(n, np.nan)
    first = di_start
    if n >= first + period:
        seed = float(np.mean(dx[first:first + period]))
        adx[first + period - 1] = seed
        prev = seed
        for i in range(first + period, n):
            prev = (prev * (period - 1) + dx[i]) / period
            adx[i] = prev
    return adx, plus_di, minus_di


# ----------------------------------------------------------------- spec / config
@dataclass
class Spec:
    ema_fast: int = EMA_FAST
    ema_slow: int = EMA_SLOW
    adx_period: int = ADX_PERIOD
    adx_min: float = ADX_MIN
    atr_period: int = ATR_PERIOD
    atr_pct_min: float = ATR_PCT_MIN
    stop_atr: float = STOP_ATR
    target_atr: float = TARGET_ATR
    risk_frac: float = RISK_FRAC
    leverage_cap: float = LEVERAGE_CAP
    fee_per_side: float = MAKER_FEE
    slippage_per_side: float = SLIPPAGE
    slippage_mode: str = "levels"      # 'levels' shifts fills and SL/TP; 'cost' charges P&L only
    intrabar: str = "stop_first"       # stop_first | target_first | minute_1m
    start_equity: float = START_EQUITY
    allow_long: bool = True
    allow_short: bool = True
    stop_first: bool = True
    stop_from_fill: bool = True
    funding_per_8h: float = 0.0

    def label(self):
        return (f"f{self.ema_fast}/{self.ema_slow} adx{self.adx_min:g} "
                f"atr{self.atr_pct_min:.3f}% sl{self.stop_atr:g} tp{self.target_atr:g} "
                f"risk{self.risk_frac*100:.2f}% fee{self.fee_per_side*100:.4f}% "
                f"slip{self.slippage_per_side*100:.3f}%({self.slippage_mode})")


# ----------------------------------------------------------------- backtest
def run_spec(df: pd.DataFrame, spec: Spec, warmup_bars: int = None, trade_log: bool = False,
             m1=None):
    """Run one pass of the frozen spec over `df`.

    Bar-by-bar order (no look-ahead): a signal is produced from the completed
    bar i; the entry fills at the open of bar i+1; the stop/target are then
    checked against bar i+1's own range (the position is live from that open)
    and every later bar until it exits.
    """
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    openp = df["open"].to_numpy(dtype=float)
    time = df["time"].to_numpy()
    n = len(df)

    ef = ema(df["close"], spec.ema_fast).to_numpy()
    es = ema(df["close"], spec.ema_slow).to_numpy()
    atr_arr = atr_wilder(high, low, close, spec.atr_period)
    adx_arr, _, _ = adx_wilder(high, low, close, spec.adx_period)

    warmup = warmup_bars if warmup_bars is not None else max(
        spec.ema_slow + 5, spec.atr_period + spec.adx_period * 3)

    equity = spec.start_equity
    peak = equity
    max_dd_pct = 0.0
    trades = []
    equity_curve = []
    pos = None
    pending = None

    stats = {"conflicts": 0}      # bars that touch both stop and target

    def resolve_minute(p, i):
        """Walk the 1-minute bars inside 5m bar i to see which level was hit first."""
        if m1 is None:
            return None
        index, m_high, m_low = m1
        span = index.get(int(time[i]))
        if span is None:
            return None
        long_ = p["dir"] == "long"
        for k in range(span[0], span[1]):
            hit_stop = (m_low[k] <= p["stop"]) if long_ else (m_high[k] >= p["stop"])
            hit_tp = (m_high[k] >= p["target"]) if long_ else (m_low[k] <= p["target"])
            if hit_stop:                      # a single minute inside both -> stop (doc fallback)
                return p["stop"], "stop"
            if hit_tp:
                return p["target"], "target"
        return None

    def exit_for_bar(p, i):
        """(exit_price, reason) if bar i touches the stop or the target."""
        long_ = p["dir"] == "long"
        hit_stop = (low[i] <= p["stop"]) if long_ else (high[i] >= p["stop"])
        hit_tp = (high[i] >= p["target"]) if long_ else (low[i] <= p["target"])
        if hit_stop and hit_tp:
            stats["conflicts"] += 1
        if hit_stop and hit_tp and spec.intrabar == "minute_1m":
            resolved = resolve_minute(p, i)
            if resolved is not None:
                return resolved
        if hit_stop and (spec.intrabar != "target_first" or not hit_tp):
            gapped = (openp[i] < p["stop"]) if long_ else (openp[i] > p["stop"])
            return (openp[i] if gapped else p["stop"]), "stop"
        if hit_tp:
            gapped = (openp[i] > p["target"]) if long_ else (openp[i] < p["target"])
            return (openp[i] if gapped else p["target"]), "target"
        return None, None

    def settle(p, exit_price, reason, i):
        """Charge slippage + fees, book P&L, append the trade."""
        nonlocal equity
        long_ = p["dir"] == "long"
        slip_level = spec.slippage_per_side if spec.slippage_mode == "levels" else 0.0
        fill = exit_price * (1 - slip_level) if long_ else exit_price * (1 + slip_level)
        gross = (fill - p["entry_price"]) * p["size"] * (1 if long_ else -1)
        fees = spec.fee_per_side * (abs(p["size"]) * p["entry_price"] + abs(p["size"]) * fill)
        slip_cost = 0.0
        if spec.slippage_mode == "cost":
            slip_cost = spec.slippage_per_side * (
                abs(p["size"]) * p["entry_price"] + abs(p["size"]) * fill)
        pnl = gross - fees - slip_cost
        equity += pnl
        trades.append({
            "entry_time": int(p["entry_time"]), "exit_time": int(time[i]),
            "entry_iso": str(pd.to_datetime(int(p["entry_time"]), unit="s", utc=True)),
            "exit_iso": str(pd.to_datetime(int(time[i]), unit="s", utc=True)),
            "dir": p["dir"], "entry": p["entry_price"], "exit": fill,
            "size": p["size"], "notional": abs(p["size"]) * p["entry_price"],
            "leverage": abs(p["size"]) * p["entry_price"] / equity,
            "stop": p["stop"], "target": p["target"], "reason": reason,
            "atr": p["atr"], "atr_pct": p["atr"] / p["signal_close"] * 100,
            "adx": p["adx"], "risk_budget": p["risk_budget"],
            "r_multiple": pnl / p["risk_budget"] if p["risk_budget"] else 0.0,
            "pnl": pnl, "fees": fees, "slippage_cost": slip_cost, "equity_after": equity,
        })

    for i in range(n):
        # (1) pending entry fills at this bar's open
        if pending is not None and pos is None and i > 0:
            sig = pending
            slip_level = spec.slippage_per_side if spec.slippage_mode == "levels" else 0.0
            fill = openp[i] * (1 + slip_level) if sig["dir"] == "long" else openp[i] * (1 - slip_level)
            dist = spec.stop_atr * sig["atr"]
            base = fill if spec.stop_from_fill else sig["close"]
            stop = base - dist if sig["dir"] == "long" else base + dist
            target = (base + spec.target_atr * sig["atr"]) if sig["dir"] == "long"                 else (base - spec.target_atr * sig["atr"])
            risk_budget = equity * spec.risk_frac
            size = risk_budget / dist if dist > 0 else 0.0
            max_size = (spec.leverage_cap * equity) / fill if fill > 0 else 0.0
            size = min(size, max_size)
            if size > 0:
                pos = {"dir": sig["dir"], "entry_price": fill, "size": size, "stop": stop,
                       "target": target, "entry_time": time[i], "atr": sig["atr"],
                       "adx": sig["adx"], "risk_budget": risk_budget,
                       "signal_close": sig["close"]}
            pending = None

        # (2) manage the open position across bar i, including the bar it entered on
        if pos is not None:
            if spec.funding_per_8h:
                equity -= (abs(pos["size"]) * pos["entry_price"]) * spec.funding_per_8h * (300.0 / 28800.0)
            px, reason = exit_for_bar(pos, i)
            if px is not None:
                settle(pos, px, reason, i)
                pos = None

        # (3) mark equity and track drawdown
        equity_curve.append(equity + (position_mark(pos, close[i]) if pos else 0.0))
        peak = max(peak, equity_curve[-1])
        dd_pct = (peak - equity_curve[-1]) / peak * 100.0
        max_dd_pct = max(max_dd_pct, dd_pct)

        # (4) signal evaluation on the completed bar (fill at the next open)
        if pos is None and pending is None and i >= warmup and i < n - 1:
            if not any(math.isnan(v) for v in (ef[i], es[i], ef[i - 1], es[i - 1],
                                               adx_arr[i], atr_arr[i])):
                atr_pct = atr_arr[i] / close[i] * 100.0
                if adx_arr[i] >= spec.adx_min and atr_pct >= spec.atr_pct_min:
                    direction = None
                    if ef[i] > es[i] and ef[i - 1] <= es[i - 1] and spec.allow_long:
                        direction = "long"
                    elif ef[i] < es[i] and ef[i - 1] >= es[i - 1] and spec.allow_short:
                        direction = "short"
                    if direction:
                        pending = {"dir": direction, "atr": atr_arr[i], "close": close[i],
                                   "adx": adx_arr[i], "time": time[i]}

    if pos is not None:      # close any open trade at the last close for accounting
        settle(pos, close[n - 1], "end_of_data", n - 1)
        pos = None
        equity_curve[-1] = equity

    out = summarize(trades, spec, equity, equity_curve, max_dd_pct, n)
    out["intrabar_conflicts"] = stats["conflicts"]
    out["intrabar_mode"] = spec.intrabar
    if trade_log:
        out["trade_list"] = trades
    return out


def position_mark(pos, price):
    if pos is None:
        return 0.0
    return (price - pos["entry_price"]) * pos["size"] * (1 if pos["dir"] == "long" else -1)


def summarize(trades, spec, equity, equity_curve, max_dd_pct, bars):
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    return {
        "spec": spec.label(),
        "bars": bars,
        "trades": len(trades),
        "winners": len(wins),
        "losers": len(losses),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "net_pnl": equity - spec.start_equity,
        "return_pct": (equity / spec.start_equity - 1.0) * 100.0,
        "end_equity": equity,
        "max_dd_pct": max_dd_pct,
        "expectancy_r": float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0,
        "avg_win": float(np.mean([t["pnl"] for t in wins])) if wins else 0.0,
        "avg_loss": float(np.mean([t["pnl"] for t in losses])) if losses else 0.0,
        "avg_atr_pct": float(np.mean([t["atr_pct"] for t in trades])) if trades else 0.0,
        "avg_adx": float(np.mean([t["adx"] for t in trades])) if trades else 0.0,
        "max_leverage": float(np.max([t["leverage"] for t in trades])) if trades else 0.0,
        "long_trades": sum(1 for t in trades if t["dir"] == "long"),
        "short_trades": sum(1 for t in trades if t["dir"] == "short"),
        "avg_bars_held": float(np.mean([(t["exit_time"] - t["entry_time"]) / 300.0 for t in trades])) if trades else 0.0,
        "total_fees": float(sum(t["fees"] for t in trades)) if trades else 0.0,
        "exits_stop": sum(1 for t in trades if t["reason"] == "stop"),
        "exits_target": sum(1 for t in trades if t["reason"] == "target"),
    }


def monthly_table(df, spec, m1=None):
    """Run the spec continuously and slice the realised P&L by entry month."""
    res = run_spec(df, spec, trade_log=True, m1=m1)
    trades = res["trade_list"]
    equity = spec.start_equity
    rows = []
    months = sorted(set(pd.to_datetime(df["time"], unit="s").dt.strftime("%Y-%m")))
    for m in months:
        mt = [t for t in trades if pd.to_datetime(t["entry_time"], unit="s").strftime("%Y-%m") == m]
        start_eq = equity
        for t in mt:
            equity += t["pnl"]
        gp = sum(t["pnl"] for t in mt if t["pnl"] > 0)
        gl = -sum(t["pnl"] for t in mt if t["pnl"] <= 0)
        wins = [t for t in mt if t["pnl"] > 0]
        rows.append({
            "month": m, "trades": len(mt), "winners": len(wins),
            "win_rate": (len(wins) / len(mt) * 100.0) if mt else 0.0,
            "return_pct": (equity / start_eq - 1.0) * 100.0 if start_eq else 0.0,
            "pnl": equity - start_eq,
            "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        })
    return res, rows


# ----------------------------------------------------------------- robustness helpers
def monte_carlo(trades, spec, runs=5000, seed=7):
    """Bootstrap trade order (with replacement) to price sequence risk.

    The doc quotes "98.2% probability of ending above the starting capital"
    from a 29-trade baseline; this is the same test on the trades actually
    produced by the reproduction.
    """
    if not trades:
        return {"runs": 0, "p_profit": 0.0, "median_end": spec.start_equity}
    rng = np.random.default_rng(seed)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    eq = np.array([t["equity_after"] for t in trades], dtype=float)
    scale = spec.start_equity / eq[-1]     # start the bootstrap at the frozen capital
    ends = []
    for _ in range(runs):
        draw = rng.choice(pnls, size=len(pnls), replace=True) * scale
        # compounding approximation: fixed fractional sizing means R-multiples scale
        ends.append(spec.start_equity + draw.sum())
    ends = np.array(ends)
    return {
        "runs": runs,
        "p_profit": float((ends > spec.start_equity).mean() * 100.0),
        "median_end": float(np.median(ends)),
        "p5_end": float(np.percentile(ends, 5)),
        "p95_end": float(np.percentile(ends, 95)),
        "median_return_pct": float((np.median(ends) / spec.start_equity - 1.0) * 100.0),
    }


def perturbation_study(df, spec, n_each=25, seed=11):
    """The doc's "25/27 nearby variants profitable" claim, tested here.

    Perturbs each frozen parameter by a small multiplicative jitter and counts
    how many single-parameter variants stay profitable over the same window.
    """
    rng = np.random.default_rng(seed)
    results = []
    grids = {
        "ema_fast": lambda v: int(round(v * rng.uniform(0.9, 1.1))),
        "ema_slow": lambda v: int(round(v * rng.uniform(0.9, 1.1))),
        "adx_min": lambda v: float(v * rng.uniform(0.85, 1.15)),
        "atr_pct_min": lambda v: float(v * rng.uniform(0.9, 1.1)),
        "stop_atr": lambda v: float(v * rng.uniform(0.85, 1.15)),
        "target_atr": lambda v: float(v * rng.uniform(0.85, 1.15)),
        "risk_frac": lambda v: float(v * rng.uniform(0.5, 2.0)),
    }
    base = asdict(spec)
    for name, fn in grids.items():
        for _ in range(n_each):
            kw = dict(base)
            kw[name] = fn(base[name])
            if kw["ema_fast"] >= kw["ema_slow"]:
                continue
            r = run_spec(df, Spec(**kw))
            results.append({"param": name, "value": kw[name],
                            "trades": r["trades"], "return_pct": r["return_pct"],
                            "profit_factor": r["profit_factor"]})
    profitable = [r for r in results if r["return_pct"] > 0]
    per_param = {}
    for r in results:
        d = per_param.setdefault(r["param"], {"n": 0, "profitable": 0, "rets": []})
        d["n"] += 1
        d["profitable"] += 1 if r["return_pct"] > 0 else 0
        d["rets"].append(r["return_pct"])
    summary = {k: {"n": v["n"], "profitable": v["profitable"],
                   "median_return_pct": float(np.median(v["rets"]))}
               for k, v in per_param.items()}
    return {
        "variants": len(results),
        "profitable": len(profitable),
        "share_profitable_pct": (len(profitable) / len(results) * 100.0) if results else 0.0,
        "median_return_pct": float(np.median([r["return_pct"] for r in results])) if results else 0.0,
        "per_param": summary,
        "detail": results,
    }


# ----------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="ATHENA-BTC-V1.0 frozen benchmark backtest")
    ap.add_argument("--data-dir", default=r"C:\PrOxyTradingTerminal\.research\btc_master_strat\data")
    ap.add_argument("--pattern", default="BTCUSDT-5m-*.csv")
    ap.add_argument("--pattern-1m", default="BTCUSDT-1m-*.csv")
    ap.add_argument("--start", default=None, help="inclusive UTC date, e.g. 2026-01-01")
    ap.add_argument("--end", default=None, help="exclusive UTC date")
    ap.add_argument("--intrabar", default=None,
                    choices=["stop_first", "target_first", "minute_1m"])
    ap.add_argument("--intrabar-compare", action="store_true",
                    help="compare stop-first against 1-minute execution resolution")
    ap.add_argument("--json", default=None)
    ap.add_argument("--sweep", action="store_true", help="run the robustness variant matrix")
    ap.add_argument("--perturb", action="store_true", help="run the parameter perturbation study")
    ap.add_argument("--monte-carlo", action="store_true", help="bootstrap the trade sequence")
    ap.add_argument("--dump-trades", default=None, help="write the baseline trade list to JSON")
    ap.add_argument("--per-month-fresh", action="store_true",
                    help="also run each month in isolation (no history carry-in)")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.data_dir, args.pattern)))
    if not paths:
        raise SystemExit("no data files matched %s\%s" % (args.data_dir, args.pattern))
    df = load_binance_5m(paths)
    if args.start:
        df = df[df["dt"] >= pd.Timestamp(args.start, tz="UTC")].reset_index(drop=True)
    if args.end:
        df = df[df["dt"] < pd.Timestamp(args.end, tz="UTC")].reset_index(drop=True)
    print(f"data: {len(df)} bars  {df['dt'].iloc[0]} -> {df['dt'].iloc[-1]}"
          f"  files={len(paths)}  missing_bars={int(df['gap_bars'].sum())}")

    m1 = None
    if args.intrabar_compare or args.intrabar == "minute_1m":
        m1_paths = sorted(glob.glob(os.path.join(args.data_dir, args.pattern_1m)))
        if not m1_paths:
            raise SystemExit("no 1m files matched %s" % args.pattern_1m)
        df1 = load_binance(m1_paths)
        if args.start:
            df1 = df1[df1["dt"] >= pd.Timestamp(args.start, tz="UTC")].reset_index(drop=True)
        if args.end:
            df1 = df1[df1["dt"] < pd.Timestamp(args.end, tz="UTC")].reset_index(drop=True)
        m1 = build_minute_index(df1)
        print(f"1m data: {len(df1)} bars  buckets indexed={len(m1[0])}")

    spec = Spec(**({"intrabar": args.intrabar} if args.intrabar else {}))
    res, rows = monthly_table(df, spec, m1=m1)
    print(f"\n=== {spec.label()} ===")
    print(f"{'month':8} {'trades':>6} {'win%':>6} {'PF':>6} {'return%':>9} {'pnl':>12}")
    for r in rows:
        print(f"{r['month']:8} {r['trades']:6d} {r['win_rate']:6.1f} "
              f"{r['profit_factor']:6.2f} {r['return_pct']:9.2f} {r['pnl']:12,.0f}")
    print(f"{'TOTAL':8} {res['trades']:6d} {res['win_rate']:6.1f} "
          f"{res['profit_factor']:6.2f} {res['return_pct']:9.2f} {res['net_pnl']:12,.0f}")
    print(f"max DD {res['max_dd_pct']:.2f}%  expectancy {res['expectancy_r']:+.3f}R  "
          f"long/short {res['long_trades']}/{res['short_trades']}  "
          f"stops/targets {res['exits_stop']}/{res['exits_target']}  "
          f"avg bars held {res['avg_bars_held']:.1f}  max leverage {res['max_leverage']:.2f}x  "
          f"fees {res['total_fees']:,.0f}")

    out = {"data": {"bars": len(df), "start": str(df['dt'].iloc[0]), "end": str(df['dt'].iloc[-1]),
                    "missing_bars": int(df["gap_bars"].sum()), "files": paths},
           "baseline": {k: v for k, v in res.items() if k != "trade_list"}, "months": rows,
           "variants": [], "per_month_fresh": [], "perturbation": None, "monte_carlo": None,
           "intrabar": []}

    if args.intrabar_compare:
        print("\n--- intrabar sequencing: assumption vs 1-minute resolution ---")
        for mode in ("stop_first", "target_first", "minute_1m"):
            s = Spec(**{**asdict(spec), "intrabar": mode})
            r = run_spec(df, s, m1=m1)
            out["intrabar"].append(r)
            print(f"  {mode:13} trades={r['trades']:3d} WR={r['win_rate']:5.1f}% "
                  f"PF={r['profit_factor']:5.2f} return={r['return_pct']:+7.2f}% "
                  f"DD={r['max_dd_pct']:5.2f}% both-level bars={r['intrabar_conflicts']}")

    if args.dump_trades:
        with open(args.dump_trades, "w", encoding="utf-8") as fh:
            json.dump(res["trade_list"], fh, indent=1, default=float)
        print(f"trades written to {args.dump_trades}")

    if args.per_month_fresh:
        print("\n--- each month in isolation (indicators restart at the month boundary) ---")
        for p in paths:
            sub = load_binance_5m([p])
            r = run_spec(sub, spec)
            out["per_month_fresh"].append({"file": os.path.basename(p), **{k: v for k, v in r.items()}})
            print(f"  {os.path.basename(p):28} trades={r['trades']:2d} "
                  f"return={r['return_pct']:+6.2f}% PF={r['profit_factor']:.2f} "
                  f"WR={r['win_rate']:.1f}% DD={r['max_dd_pct']:.2f}%")

    if args.monte_carlo:
        mc = monte_carlo(res["trade_list"], spec)
        out["monte_carlo"] = mc
        print(f"\n--- Monte Carlo trade-order bootstrap ({mc['runs']} runs) ---")
        print(f"  P(end above start) = {mc['p_profit']:.1f}%   median end = {mc['median_end']:,.0f} "
              f"({mc['median_return_pct']:+.2f}%)   5th pct = {mc['p5_end']:,.0f}")

    if args.perturb:
        pt = perturbation_study(df, spec)
        out["perturbation"] = {k: v for k, v in pt.items() if k != "detail"}
        print(f"\n--- parameter perturbation: {pt['profitable']}/{pt['variants']} variants profitable "
              f"({pt['share_profitable_pct']:.0f}%), median {pt['median_return_pct']:+.2f}% ---")
        for k, v in pt["per_param"].items():
            print(f"  {k:14} {v['profitable']:3d}/{v['n']:<3d} profitable   median {v['median_return_pct']:+7.2f}%")

    if args.sweep:
        variants = []

        def add(name, **kw):
            s = Spec(**{**asdict(spec), **kw})
            r = run_spec(df, s)
            r["variant"] = name
            variants.append(r)
            print(f"  {name:36} trades={r['trades']:3d} return={r['return_pct']:+7.2f}% "
                  f"PF={r['profit_factor']:5.2f} WR={r['win_rate']:5.1f}% DD={r['max_dd_pct']:6.2f}%")

        print("\n--- cost sensitivity ---")
        add("baseline (maker+GST, slip shifts px)")
        add("baseline, slip as cost only", slippage_mode="cost")
        add("taker 0.05% + 2bp (levels)", fee_per_side=0.0005, slippage_per_side=0.0002)
        add("zero costs", fee_per_side=0.0, slippage_per_side=0.0)
        add("maker, no slippage", slippage_per_side=0.0)
        add("slippage 5bp (cost only)", slippage_per_side=0.0005, slippage_mode="cost")
        add("slippage 10bp (cost only)", slippage_per_side=0.001, slippage_mode="cost")
        add("slippage 15bp (cost only)", slippage_per_side=0.0015, slippage_mode="cost")
        add("slippage 5bp (levels)", slippage_per_side=0.0005)
        add("slippage 10bp (levels)", slippage_per_side=0.001)
        add("slippage 15bp (levels)", slippage_per_side=0.0015)

        add("engine cost model (taker 0.05%+2bp+funding)", fee_per_side=0.0005,
            slippage_per_side=0.0002, funding_per_8h=0.0001)
        add("taker 0.05% + funding, no slippage", fee_per_side=0.0005,
            slippage_per_side=0.0, funding_per_8h=0.0001)
        print("\n--- filter sensitivity ---")
        add("ADX >= 25", adx_min=25.0)
        add("ADX >= 30", adx_min=30.0)
        add("ADX >= 45", adx_min=45.0)
        add("ADX >= 55", adx_min=55.0)
        add("no ADX filter", adx_min=0.0)
        add("ATR% >= 0.075", atr_pct_min=0.075)
        add("ATR% >= 0.125", atr_pct_min=0.125)
        add("ATR% >= 0.15", atr_pct_min=0.15)
        add("no ATR filter", atr_pct_min=0.0)
        add("no ADX, no ATR filter", adx_min=0.0, atr_pct_min=0.0)
        print("\n--- structure sensitivity ---")
        add("EMA 180/360", ema_fast=180, ema_slow=360)
        add("EMA 200/400", ema_fast=200, ema_slow=400)
        add("EMA 96/192", ema_fast=96, ema_slow=192)
        add("EMA 100/200", ema_fast=100, ema_slow=200)
        add("EMA 48/96", ema_fast=48, ema_slow=96)
        add("long only", allow_short=False)
        add("short only", allow_long=False)
        add("target-first convention", stop_first=False)
        add("stop/target from signal close", stop_from_fill=False)
        add("SL2/TP6", stop_atr=2.0, target_atr=6.0)
        add("SL1/TP3", stop_atr=1.0, target_atr=3.0)
        add("SL3/TP3 (1R)", stop_atr=3.0, target_atr=3.0)
        add("SL3/TP7 long-only", allow_short=False)
        add("risk 1.0%", risk_frac=0.01)
        add("risk 0.25%", risk_frac=0.0025)
        add("leverage cap 3x", leverage_cap=3.0)
        add("entry at signal close (look-ahead)", stop_from_fill=False)
        out["variants"] = variants

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=float)
        print(f"\nwrote {args.json}")
    return out


if __name__ == "__main__":
    main()
