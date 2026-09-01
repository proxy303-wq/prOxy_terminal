"""PrOxy commodity data - MCX futures via Dhan (scrip master + charts API).

Everything MCX lives here so the NIFTY path (proxy/dhan_data.py) stays
untouched:

    master = load_mcx_master()            # data/scrip_master/api-scrip-master.csv
    c = resolve_mcx_contract("CRUDEOIL")  # near-month FUTCOM dict
    df = fetch_mcx_intraday("CRUDEOIL", days=5)
    ltp, prev = mcx_ticker("CRUDEOIL")    # live quote (REST)

Dhan charts API needs the security_id from the official scrip master
(https://images.dhan.co/api-data/api-scrip-master.csv - 24 MB, download
once into data/scrip_master/).  Segment = MCX_COMM, instrument = FUTCOM.
"""
import os
import csv
import io
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .dhan_data import fetch_intraday, _client
from .athena_env import load_athena_env

load_athena_env()   # Dhan creds (DHAN_CLIENT_ID/token) without the caller remembering

IST = ZoneInfo("Asia/Kolkata")
MCX_SEGMENT = "MCX_COMM"
MCX_INSTRUMENT = "FUTCOM"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
MASTER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "scrip_master", "api-scrip-master.csv")


def download_mcx_master(url=MASTER_URL, path=None, force=False):
    """Fetch the Dhan scrip-master CSV (blocked without a browser UA)."""
    path = path or MASTER_PATH
    if os.path.exists(path) and not force:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "Chrome/126.0 Safari/537.36",
        "Accept": "text/csv,*/*",
    })
    blob = urllib.request.urlopen(req, timeout=120).read()
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


_MCX_CACHE = {"rows": None, "path": None}


def load_mcx_master(path=None):
    """All MCX_COMM rows of the scrip master as dicts (cached)."""
    path = path or MASTER_PATH
    if _MCX_CACHE["rows"] is not None and _MCX_CACHE["path"] == path:
        return _MCX_CACHE["rows"]
    if not os.path.exists(path):
        download_mcx_master(path=path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        rows = [r for r in csv.DictReader(fh)
                if (r.get("SEM_EXM_EXCH_ID") or "").upper() == "MCX"]
    _MCX_CACHE["rows"] = rows
    _MCX_CACHE["path"] = path
    return rows


# Known MCX lot sizes (contract specs; the scrip master's SEM_LOT_UNITS is
# always '1.0' for MCX and cannot be used).  Keyed by the symbol STEM as it
# appears in SEM_TRADING_SYMBOL (e.g. "GOLDM" for GOLDM-04Sep2026-FUT).
MCX_LOT_SIZES = {
    "CRUDEOIL": 100, "CRUDEOILM": 10,
    "GOLD": 1000, "GOLDM": 100, "GOLDTEN": 10, "GOLDGUINEA": 8, "GOLDPETAL": 1,
    "SILVER": 30, "SILVERM": 5, "SILVERMIC": 1, "SILVER100": 100,
    "NATURALGAS": 1250, "NATGASMINI": 250,
    "COPPER": 2500,
    "ZINC": 5000, "ZINCMINI": 1000,
    "ALUMINIUM": 5000, "ALUMINI": 1000,
    "LEAD": 5000, "LEADMINI": 1000,
    "NICKEL": 250, "STEELREBAR": 10000,
    "MENTHAOIL": 480, "COTTON": 25, "KAPAS": 2000,
}


def mcx_symbol_stem(symbol_or_contract):
    """Normalise a symbol / trading symbol to its MCX stem."""
    stem = (symbol_or_contract or "").split("-")[0].upper()
    if not stem and symbol_or_contract:
        stem = symbol_or_contract.upper()
    return stem


def mcx_lot_size(symbol_or_contract):
    """MCX lot size by symbol stem (contract specs; master is unreliable)."""
    stem = mcx_symbol_stem(symbol_or_contract)
    if stem in MCX_LOT_SIZES:
        return MCX_LOT_SIZES[stem]
    # e.g. "GOLDM" requested as "GOLD" -> longest stem prefix match
    best = None
    for k, v in MCX_LOT_SIZES.items():
        if k.startswith(stem) or stem.startswith(k):
            if best is None or len(k) > len(best[0]):
                best = (k, v)
    return best[1] if best else 1


def resolve_mcx_contract(symbol, expiry=None, near=True):
    """Pick the FUTCOM contract for an MCX symbol.

    symbol: base symbol (CRUDEOIL, GOLD, SILVER, NATURALGAS, COPPER, ...).
    expiry: optional 'YYYY-MM-DD'; None + near=True -> nearest FUTCOM.
    Returns a row dict or None.
    """
    rows = load_mcx_master()
    stem = mcx_symbol_stem(symbol)
    cands = [r for r in rows
             if (r.get("SEM_INSTRUMENT_NAME") or "").upper() == MCX_INSTRUMENT
             and mcx_symbol_stem(r.get("SEM_TRADING_SYMBOL")) == stem]
    if not cands:
        return None
    if expiry:
        for r in cands:
            if (r.get("SEM_EXPIRY_DATE") or "").startswith(expiry):
                return r
        return None
    cands.sort(key=lambda r: (r.get("SEM_EXPIRY_DATE") or "9999"))
    return cands[0] if near else cands[-1]


def fetch_mcx_intraday(symbol, days=5, interval=5, contract=None, retries=2):
    """5-min OHLCV for an MCX futures symbol (charts API, last N trading
    days).  Returns a DataFrame (date/open/high/low/close/volume) or empty.
    Dhan's charts API intermittently returns empty for a wide window - retry
    once on a shorter window.
    """
    c = contract or resolve_mcx_contract(symbol)
    if c is None:
        return pd.DataFrame()
    sid = c.get("SEM_SMST_SECURITY_ID")
    end = pd.Timestamp.now(IST)
    df = pd.DataFrame()
    for attempt in range(retries + 1):
        start = end - pd.Timedelta(days=days * 2)
        df = fetch_intraday(start.date(), end.date(), interval=interval,
                            security_id=str(sid), segment=MCX_SEGMENT,
                            instrument=MCX_INSTRUMENT)
        if not df.empty:
            break
        days = max(2, days - 1)   # shrink the window and retry
    if not df.empty:
        stem = mcx_symbol_stem(symbol)
        df.attrs["symbol"] = symbol
        df.attrs["contract"] = c.get("SEM_TRADING_SYMBOL")
        df.attrs["security_id"] = sid
        df.attrs["lot_size"] = mcx_lot_size(stem)
    return df


def mcx_ticker(symbol, contract=None):
    """Latest quote for an MCX symbol via the charts API's last bar
    (no websocket needed).  Returns (ltp, previous_close) or (None, None)."""
    df = fetch_mcx_intraday(symbol, days=2, contract=contract)
    if df.empty:
        return None, None
    last = df.iloc[-1]
    prev = df[df.index.date < last["date"].date()]
    prev_close = float(prev.iloc[-1]["close"]) if len(prev) else float(last["close"])
    return float(last["close"]), prev_close


def mcx_symbols():
    """Available MCX FUTCOM base symbols (for dashboards/menus)."""
    seen = []
    for r in load_mcx_master():
        if (r.get("SEM_INSTRUMENT_NAME") or "").upper() != MCX_INSTRUMENT:
            continue
        sym = (r.get("SEM_TRADING_SYMBOL") or "").split("-")[0].upper()
        if sym not in seen:
            seen.append(sym)
    return sorted(seen)
