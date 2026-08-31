"""Live option-chain features + 5-minute snapshot recorder.

Polls Dhan's live option chain (LTP, OI, volume, IV per strike) and
computes the paper-1 feature set (PCR-volume, PCR-OI, OI support/resistance,
ATM IV, IV skew) plus records full snapshots to
data/options/live_chain_history/chain_<date>.csv every 5 minutes during
market hours - accumulating the training data for the next retrain
(exactly what the IJRTE paper did for 6 months before training).

Usage:
    from mlab.options_live import live_features_for, record_loop
    feat = live_features_for("nifty")          # one-shot feature dict
    record_loop(duration_minutes=375)          # record a full session
"""
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .options_data import live_chain_features, record_live_snapshot, LIVE_HIST_DIR
from .options_features import _band_frame

IST = ZoneInfo("Asia/Kolkata")
NIFTY_ID, BANKNIFTY_ID = 13, 25


def live_features_for(symbol="nifty"):
    """Paper-1 style feature dict from the CURRENT live chain (None on error)."""
    from proxy.athena_env import load_athena_env
    load_athena_env()
    from proxy.dhan_data import fetch_option_chain
    underlying = NIFTY_ID if symbol == "nifty" else BANKNIFTY_ID
    chain = fetch_option_chain(underlying)
    if not chain:
        return None
    feat = live_chain_features(chain)
    feat["symbol"] = symbol
    feat["expiry"] = chain.get("expiry")
    feat["spot"] = chain.get("spot")
    feat["time"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    return feat


def _market_open(now=None):
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minutes <= 15 * 60 + 30


def record_loop(duration_minutes=375, interval_seconds=300, symbols=("nifty", "banknifty")):
    """Record chain snapshots every interval during market hours.

    Runs until duration_minutes elapses or market close.  Each snapshot
    appends a row to data/options/live_chain_history/chain_<date>.csv.
    """
    os.makedirs(LIVE_HIST_DIR, exist_ok=True)
    started = datetime.now(IST)
    last_bar = None
    while (datetime.now(IST) - started).total_seconds() / 60 < duration_minutes:
        now = datetime.now(IST)
        if not _market_open(now):
            # wait until next market open
            print(f"[options-recorder] market closed at {now:%H:%M} - waiting", flush=True)
            time.sleep(600)
            continue
        bar_key = now.strftime("%Y-%m-%d %H:%M")
        if bar_key != last_bar:
            for sym in symbols:
                feat = live_features_for(sym)
                if feat:
                    # reuse the row-writer from options_data with a live chain dict
                    from proxy.dhan_data import fetch_option_chain
                    chain = fetch_option_chain(NIFTY_ID if sym == "nifty" else BANKNIFTY_ID)
                    if chain:
                        path = record_live_snapshot(chain)
                        print(f"[options-recorder] {sym} {now:%H:%M} "
                              f"pcr_vol={feat.get('pcr_vol')} pcr_oi={feat.get('pcr_oi')} "
                              f"atm_iv_ce={feat.get('atm_iv_ce')} -> {path}", flush=True)
            last_bar = bar_key
        time.sleep(min(interval_seconds, 60))





def live_band_features(chain):
    """Compute the SAME 22 option features as the training band, from a live
    chain snapshot (all strikes with ltp/oi/volume/iv + spot).  Returns a
    dict with the FEATURE_COLS names (band-frame equivalents), or {} on error.

    The band features in training use ATM+/-3 rolling strikes; the live chain
    has all strikes, so the band = strikes within 3 steps of ATM.
    """
    import numpy as np
    from .options_features import FEATURE_COLS
    rows = chain.get("rows") or []
    if not rows:
        return {}
    spot = float(chain.get("spot") or 0.0)
    if spot <= 0:
        return {}
    df = pd.DataFrame(rows)
    # band = strikes nearest to spot within +/-3 steps (100-pt NIFTY grid)
    strikes = np.sort(df["strike"].unique())
    if len(strikes) == 0:
        return {}
    atm_i = int(np.argmin(np.abs(strikes - spot)))
    band = strikes[max(0, atm_i - 3): min(len(strikes), atm_i + 4)]
    b = df[df["strike"].isin(band)]
    ce = b[b["option_type"] == "CE"]
    pe = b[b["option_type"] == "PUT"]
    ce_v = float(ce["volume"].sum()) if len(ce) else 0.0
    pe_v = float(pe["volume"].sum()) if len(pe) else 0.0
    ce_o = float(ce["oi"].sum()) if len(ce) else 0.0
    pe_o = float(pe["oi"].sum()) if len(pe) else 0.0
    f = {}
    f["pcr_vol"] = ce_v / pe_v if pe_v > 0 else np.nan
    f["pcr_oi"] = ce_o / pe_o if pe_o > 0 else np.nan
    f["ce_vol_share"] = ce_v / (ce_v + pe_v) if (ce_v + pe_v) > 0 else np.nan
    f["log_vol"] = np.log1p(ce_v + pe_v)
    atm_k = float(strikes[atm_i])
    a_ce = ce[ce["strike"] == atm_k]
    a_pe = pe[pe["strike"] == atm_k]
    f["atm_ce_prem"] = float(a_ce["ltp"].iloc[0]) if len(a_ce) else np.nan
    f["atm_pe_prem"] = float(a_pe["ltp"].iloc[0]) if len(a_pe) else np.nan
    f["atm_prem_ratio"] = f["atm_ce_prem"] / f["atm_pe_prem"] if f["atm_pe_prem"] and f["atm_pe_prem"] > 0 else np.nan
    f["atm_iv_ce"] = float(a_ce["iv"].iloc[0]) if len(a_ce) and pd.notna(a_ce["iv"].iloc[0]) else np.nan
    f["atm_iv_pe"] = float(a_pe["iv"].iloc[0]) if len(a_pe) and pd.notna(a_pe["iv"].iloc[0]) else np.nan
    f["iv_skew"] = f["atm_iv_pe"] - f["atm_iv_ce"] if (pd.notna(f["atm_iv_pe"]) and pd.notna(f["atm_iv_ce"])) else np.nan
    if len(ce) and ce["oi"].notna().any():
        r = ce.loc[ce["oi"].idxmax()]
        f["res_dist_pct"] = (float(r["strike"]) / spot - 1.0) * 100.0
    if len(pe) and pe["oi"].notna().any():
        r = pe.loc[pe["oi"].idxmax()]
        f["sup_dist_pct"] = (float(r["strike"]) / spot - 1.0) * 100.0
    f["ce_oi_tot"] = ce_o
    f["pe_oi_tot"] = pe_o
    # diffs vs the LAST recorded historical band bar (for change features)
    hist = _band_frame(13 if str(chain.get("underlying")) == "13" else 25)
    if not hist.empty:
        last = hist.iloc[-1]
        for col, src_col in (("d_oi_ce_1", "ce_oi_tot"), ("d_oi_pe_1", "pe_oi_tot"),
                             ("ce_prem_chg1", "atm_ce_prem"), ("pe_prem_chg1", "atm_pe_prem"),
                             ("pcr_vol_chg1", "pcr_vol"), ("pcr_oi_chg1", "pcr_oi")):
            f[col] = f.get(src_col, np.nan) - last.get(src_col, np.nan)
        for col in ("d_oi_ce_3", "d_oi_pe_3", "ce_prem_chg3", "pe_prem_chg3"):
            f[col] = np.nan  # no 3-bar history in a snapshot; left for the recorder path
    else:
        for col in FEATURE_COLS:
            f.setdefault(col, np.nan)
    return {k: f.get(k, np.nan) for k in FEATURE_COLS}

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        for sym in ("nifty", "banknifty"):
            print(sym, live_features_for(sym))
    else:
        minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 375
        record_loop(duration_minutes=minutes)