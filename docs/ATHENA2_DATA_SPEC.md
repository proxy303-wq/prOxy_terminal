# ATHENA 2.0 - DATA SPEC (athena2/data.py + config defaults)

Implementation-true record of the Athena 2.0 data layer for the NIFTY
short-premium research stack.  Read from `athena2/data.py` and
`athena2/config.py` and verified against the files on disk (2026-09-10,
read-only).  Nothing here is invented: every schema, default path, loader
signature and caveat was checked against source and the real CSV rows.
Roadmap items are marked **(roadmap)**.

## 1. Role of the layer

`athena2/data.py` is the pure, offline, network-free loading front door of
Athena 2.0.  It turns the repo's stored CSVs into standardized frames and
typed snapshots consumed by the quant/strategy/backtest layers:

* spot index bars      `data/NIFTY_5m.csv`          -> date,ohlc,volume
* futures bars         `data/futures/*_5m.csv`      -> continuous + per-expiry
* option history       `data/options/history/`     -> per-strike/per-expiry 5m bars
* chain snapshots      typed `ChainSnapshot` / `MarketSnapshot` objects
  (dataclasses defined in `athena2/contracts.py`)

All loaders tolerate missing files except `load_option_expiry` /
`expiry_chain_at` / `market_snapshot_at`, which raise when the requested
history does not exist.  Defaults are repo-CWD-relative strings
(`data/NIFTY_5m.csv`, `data/futures/NIFTY_FUT_5m.csv`,
`data/options/history`); `Athena2Config` carries the mirroring knobs
(`data_root="data"`, `option_history_dir="data/options/history"`,
`event_tz="Asia/Kolkata"`, `bars_per_day=75`, `symbol="NIFTY"`,
`lot_size=75`, `strike_interval=50.0`).

## 2. Source inventory (verified on disk)

| Source | Path (repo root) | Verified shape / span |
|---|---|---|
| NIFTY spot 5m | data/NIFTY_5m.csv | 38,328 rows; 2024-08-26 09:15 .. 2026-09-07 15:25; 5-min, ~75 bars/session |
| NIFTY futures 5m (current month) | data/futures/NIFTY_FUT_5m.csv | 3,803 rows; 2026-07-01 .. 2026-09-08; single expiry 2026-09-29 column |
| NIFTY continuous (back-adjusted) | data/futures/NIFTY_CONT_5m.csv | adds adj_close, adj_factor; same 2026-07-01 .. 2026-09-08 window |
| NIFTY per-expiry future | data/futures/NIFTY_2026-09-29_5m.csv | one contract file (BANKNIFTY/FINNIFTY siblings exist) |
| NIFTY option history | data/options/history/opt_13_<expiry>_<band>_{CALL,PUT}.csv | 350 files, 25 monthly expiries 2024-08-01 .. 2026-08-15, 14 files/expiry |
| Sibling option series | data/options/history/opt_25_<expiry>_<band>_{CALL,PUT}.csv | 350 more files, same scheme, different instrument scale (NOT loaded) |
| Instrument master | data/scrip_master/api-scrip-master.csv | ~25 MB, 16-col SEM_* Dhan-style schema (trading symbol, expiry, strike, option type, lot units) |
| Derived caches | data/options/feature_cache/ (optfeat_13_*.pkl), data/options/live_chain_history/ (chain_*.csv) | not consumed by data.py loaders |

Spot file sample rows (verbatim):

    date,open,high,low,close,volume
    2024-08-26 09:15:00+05:30,24906.099609375,24917.30078125,24875.099609375,24879.85,0.0
    2024-08-26 09:20:00+05:30,24879.85,24893.45,24874.69921875,24880.9,0.0

Option file sample rows (verbatim, ATM CALL 2024-08-01 expiry label):

    time,strike,open,high,low,close,iv,oi,volume,spot
    2024-08-01 09:15:00+05:30,25050.0,52.15,67.85,47.0,64.9,23.330678,5036550,15461175,25054.05
    2024-08-01 09:20:00+05:30,25050.0,64.75,85.0,63.75,72.8,25.563303,6544550,15674475,25058.75

## 3. Option history semantics (what 25 expiries really buys you)

* Per-strike files: `opt_13_<iso-expiry>_<band>_{CALL,PUT}.csv` with band in
  ATM, ATM+1..ATM+3, ATM-1..ATM-3 (7 offsets x CALL/PUT = 14 files per expiry,
  350 files for the 25 monthly labels 2024-08-01..2026-08-15).  The loader
  glob anchors on `opt_13_<expiry>_*.csv` only.
* One expiry label = one monthly contract series.  Each file holds the month's
  trading life at 5-minute cadence: checked files carry 1,580-1,585 rows
  (~21 sessions x 75-76 bars/day, 09:15 -> ~15:25/15:30 IST).  File labels
  (e.g. 2024-08-01) are the series/expiry identity used by the code; a file's
  bars run from the label month's start to near month end (2024-08-01 label
  file ends 2024-08-30 15:25).
* Band-following strikes, not fixed strikes: the strike column re-anchors to
  the ATM reference as spot moves.  In the checked ATM_CALL month the file
  contains 28 distinct strikes (25050.0 on 2024-08-01 -> 25250.0 on
  2024-08-30, spot drifting 25054 -> 25247); during fast moves the ladder
  skips strikes (e.g. 24950.0 -> 24850.0 with no 24900.0 row).  A row is the
  bar of whichever strike the band points at that timestamp.
* Row schema: time,strike,open,high,low,close,iv,oi,volume,spot.  Every row
  carries IV, OI, traded volume and a per-bar underlying spot reference - the
  fields a short-premium study needs.  There is NO bid/ask column anywhere.
* Monthly expiries only: no weekly chains, no serial-month depth, and only the
  near-ATM corridor (|offset| <= 3 x 50 pt).  Long-dated and far-OTM premium
  selling cannot be studied from this store.

## 4. Limitations for short-premium research (all verified)

* No full chain: at any timestamp you see at most the ATM corridor, not the
  complete strike grid, so deep-OTM short puts/calls, skew tails and
  wide-strangle economics are out of reach of stored history.
* No true bid/ask: option history is bars only.  Fills must be derived (see
  section 6) and carry a conservative bias against the seller.
* Spot index volume is zero: every sampled NIFTY_5m.csv row has volume 0.0
  (checked across a 1,000-row sample) - volume-based filters cannot be applied
  to the index series.
* Short spot history vs options: spot 5m begins 2024-08-26 while option
  history begins 2024-08-01; option rows carry their own spot column, but a
  backtest needing the index bar series before 2024-08-26 must rely on the
  option-file spot reference.
* Futures history is short: NIFTY_FUT_5m / NIFTY_CONT_5m cover only
  2026-07-01..2026-09-08 (one 2026-09-29 contract), so roll/basis research is
  limited.
* Synthetic scale check (data-quality): sampled rows
  (2024-08-01 09:15, ATM CALL 25050 close 64.9 / iv 23.330678 / spot 25054.05;
  ATM-3 PUT 24900 close 17.75 / iv 26.29) do not reproduce under BSM with a
  one-month term under either a decimal or percent IV reading.  Validate the
  generator's price/IV/time conventions before trusting pricing math.
* IV unit convention: the stored `iv` column passes through data.py unchanged
  (values read like percent-scaled, e.g. 23.330678); `bsm.py` expects an
  annualized decimal sigma.  The data layer does not normalize; confirm where
  the /100 belongs before using IV-derived deltas/vega.

## 5. Timestamp normalization rule

`_normalize_ts()` parses with `pd.to_datetime(errors="coerce")`; if the
series is timezone-aware (repo files carry +05:30) it is converted to
Asia/Kolkata and stripped of tz (`tz_convert("Asia/Kolkata").tz_localize(None)`).
Result: **tz-naive IST wall-clock** timestamps everywhere, so spot / futures /
option frames and snapshot comparisons line up.  `read_ohlc` drops rows with
NaT time and sorts ascending.

## 6. Conservative bid/ask proxy (fill bias AGAINST the seller)

`snapshot_bid_ask_from_ohlc(df)`: `bid = bar low`, `ask = bar high`.
Because stored option history has no quotes, execution references are derived
from bar extremes:

* selling (opening a short) fills at the bar **low** -> the worst price for a
  seller that day;
* buying back (closing the short) fills at the bar **high** -> again the worst
  price for the closing buyer.

This is a deliberate research fill-bias against the short-premium seller
(never a free lunch), and the same conservative proxy is used by the backtester
(`athena2/backtest.py`).  Until true bid/ask ticks are persisted this remains
the documented stand-in.

## 7. Loader API surface (exact signatures from source)

| Callable | Returns / behavior |
|---|---|
| `read_ohlc(path, time_col="date") -> DataFrame` | generic OHLC(V) reader; normalizes+sorts on the time column, renames it to `time`; extra columns (open_interest, expiry, ...) pass through |
| `load_spot(path=None) -> DataFrame` | `read_ohlc` on data/NIFTY_5m.csv by default |
| `load_futures(path=None) -> DataFrame` | `read_ohlc` on data/futures/NIFTY_FUT_5m.csv by default |
| `option_history_paths(expiry: date, root=None) -> List[str]` | sorted glob of `opt_13_<iso>_*.csv` for one expiry |
| `load_option_expiry(expiry: date, root=None) -> DataFrame` | long-form per-expiry frame: time, strike, opt_type, open, high, low, close, iv, oi, volume, spot; type from file suffix _CALL/_PUT; concat sorted by time/strike/opt_type; raises FileNotFoundError when no files |
| `snapshot_bid_ask_from_ohlc(df) -> DataFrame` | adds bid=low, ask=high columns |
| `expiry_chain_at(df, expiry, ts, symbol="NIFTY", lot_size=75, use_bid_ask=True) -> ChainSnapshot` | nearest bar <= ts of a per-expiry frame; auto-derives bid/ask when use_bid_ask and missing; builds ChainRow list with last/bid/ask/iv/oi/volume/spot; spot = first non-null row spot; raises KeyError when nothing <= ts |
| `market_snapshot_at(spot_df, expiry_df, expiry, ts, symbol="NIFTY", lot_size=75, fut_df=None) -> MarketSnapshot` | UnderlyingState (spot close at nearest bar <= ts, optional future + basis) plus one ChainSnapshot; raises KeyError without spot rows <= ts |

Chain/underlying dataclasses (contracts.py): `OptionContract` (symbol,
expiry, strike, opt_type, lot_size, instrument_token, `key()`), `ChainRow`,
`ChainSnapshot` (`row()`, `strikes()`), `UnderlyingState`,
`MarketSnapshot`.  ChainRow.iv/oi/volume/spot are optional and default None/0.

## 8. Quality checks and known caveats

* Loaders are pure and never guess; missing history raises loudly at snapshot
  time rather than producing silent empty chains.
* Per-day option bars count 75-76 (not a strict 75), and option sessions run
  to ~15:25/15:30; alignment against the 75-bar spot grid is approximate.
* Timestamps are IST wall-clock only after normalization; raw files are
  +05:30-aware and must not be compared before `load_*`.
* The stored option file is band-following (see section 3): chain width can
  shrink or shift abruptly after large spot moves; treat snapshots as the
  corridor actually visible at ts.
* Volume/OI magnitudes look like aggregated share counts; do not divide by
  lot size without confirming the generator's unit convention.

## 9. Roadmap **(roadmap)**

* Persist true bid/ask (Dhan feed) tick/bar data and back it into the option
  store so the conservative bar-extreme proxy can be retired.
* Persist full-chain snapshots (all strikes x CALL/PUT per expiry) at 5m (or
  finer) cadence instead of the ATM +/-3 corridor.
* Per-expiry continuous replay of the option series so strikes stop
  re-anchoring inside one file and far-OTM / weeklies become researchable.
* An event calendar (results, RBI, holidays) to feed regime event-risk gates.
* A loader-side data-quality harness: unit-convention checks for iv,
  premium/iv/no-arb consistency, gap and missing-bar reports.
