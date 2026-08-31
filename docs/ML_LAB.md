# PrOxy ML Lab — NIFTY 50 & BANKNIFTY Movement Prediction

A research-grade machine-learning pipeline that forecasts the **direction** of
NIFTY 50 and BANKNIFTY over intraday horizons, trained and validated with the
discipline of the project's reference books:

| Source | Contribution |
|---|---|
| **Gupta et al. (IJRTE 2021)** — NIFTY Forecasting using ML on Option Chain | Horizons matter: accuracy rose from ~57% (5 min) to ~70% (30 min) in their experiments; binary up/down target; PCR-of-volume as the dominant feature |
| **Ukhalkar et al. (2025)** — Bank Nifty: Stock Price Predictions using ML & Sentiment | BankNifty is more volatile and macro-driven; hybrid ensembles (boosting) and LSTM-family nets are the recommended approaches |
| **Aronson** — Evidence-Based Technical Analysis | Out-of-sample walk-forward testing, null-hypothesis (permutation) significance, no curve-fitting on the test set |
| **Volman** — Understanding Price Action | 5-minute price-action structure: body/wick anatomy, inside bars, gaps, session-phase context |
| **Williams** — Long-Term Secrets to Short-Term Trading | Close-position-in-range, time-of-day seasonality |
| **Miner** — High Probability Trading Strategies | Multi-timeframe confluence via both index and cross-index features |

## What is predicted

* **Targets** (per horizon): direction of the index close H bars ahead.
  * h1 -> next 5 minutes; h3 -> 15 min; h6 -> 30 min; h12 -> 60 min.
* **Labels**: 1 if close[t+H] > close[t], else 0. A "meaningful move"
  target (|move| >= threshold) is also tracked to quantify noise-filtered hits.
* **Symbols**: NIFTY and BANKNIFTY, each modeled with features from **both**
  indices (cross-index relative strength is information).

## Features (about 102, all causal)

Per index: return lags, cumulative returns, realized volatility (5/20 bar) and
their ratio, RSI(6/14), MACD, Bollinger %B/width, ATR%, EMA ratios,
SMA-distance, close position in bar range, body/wick ratios, up/down streaks,
inside bars, gaps, stochastic, CCI, Williams %R, volume ratio/z (NaN before
Nov-2025, treated as missing), day-return and distance from running day
high/low.

Cross-index: NIFTY-BANKNIFTY return spreads (1/3/6/12 bars), the
BANKNIFTY/NIFTY log-ratio z-score, session-phase (minutes into session, time
to close, hour sin/cos), day-of-week sin/cos.

## Validation (Aronson walk-forward)

* **5 expanding-window folds**, strictly chronological: every fold trains only
  on data strictly before its test window. No shuffling, no leakage.
* Baselines: majority-class accuracy.
* **Permutation test** (200 shuffles of labels keeping the positive rate):
  the p-value answers "could a no-edge model produce this accuracy?"
* **Confidence bands**: hit rate of directional calls at P(up) >= 60% (long)
  and P(up) <= 40% (short) - the tradable tail of the distribution.
* Strategy sanity: hit rate of confident signals (no-cost upper bound).

## Models

| Model | Notes |
|---|---|
| LightGBM | fast histogram GBM, NaN-native |
| XGBoost | second GBM for diversity |
| MLP (sklearn) | tabular NN baseline (median-impute + standardize) |
| GRU (TF) | 2-layer recurrent net on 30-bar windows (last-fold benchmark; CPU) |
| Ensemble | probability average of LightGBM + XGBoost |

Hyperparameters are fixed and modest - deliberately **not** tuned on the test
set (curve-fit avoidance).

## Files

    mlab/config.py     paths, horizons, model params
    mlab/data.py       load/align, targets, walk-forward splits
    mlab/features.py   102-feature engineering
    mlab/models.py     model zoo
    mlab/evaluate.py   metrics, permutation test, strategy sanity
    mlab/train.py      walk-forward training + artifact saving
    mlab/predict.py    live prediction from latest bars
    mlab/report.py     readable report generator

Outputs:

    models/ml_lab/<symbol>_<horizon>_<model>.joblib|.keras   deployed models
    models/ml_lab/<...>_meta.json                            metadata + OOS metrics
    reports/oos_<symbol>_<horizon>.csv                       OOS predictions
    reports/ml_lab_report.json / .txt                        aggregated results

## Usage

    python -m mlab.train --symbol all --horizons all            # train everything
    python -m mlab.train --symbol nifty --horizons h3,h6        # targeted
    python run_terminal.py ml-lab                               # train + report
    python run_terminal.py ml-lab --predict nifty,h3            # live forecast
    python -m mlab.report                                       # re-render report



## Option-chain features (Dhan) - FULL historical dataset

**Update (2026-08-31): Dhan serves up to 5 YEARS of rolling expired-option
intraday data** via /charts/rollingoption (SDK: expired_options_data) -
30 days per call, ATM+/-3 strikes, with close/iv/oi/volume/spot per 5-min
bar.  We downloaded the full 2-year window matching NIFTY_5m.csv
(2024-08 .. 2026-08) for both NIFTY and BANKNIFTY:

    data/options/history/opt_<uid>_<chunk>_<strike>_<type>.csv   (700 files)

That is exactly the dataset of the IJRTE paper (they collected 6 months of
5-minute option-chain snapshots; we now have 2 years of OI, IV, volume,
premium and spot - plus PCR-OI, PCR-volume, OI buildup, max-OI
support/resistance derived from it).

Feature set per 5-min bar (mlab/options_features.py, 22 features x 2
underlyings = 44 columns):

    pcr_vol, pcr_oi, ce_vol_share, log_vol,
    atm_ce_prem, atm_pe_prem, atm_prem_ratio,
    atm_iv_ce, atm_iv_pe, iv_skew,
    d_oi_ce_1/3, d_oi_pe_1/3 (OI buildup),
    res_dist_pct (max-CE-OI resistance), sup_dist_pct (max-PE-OI support),
    ce_prem_chg1/3, pe_prem_chg1/3, pcr_vol_chg1, pcr_oi_chg1

Usage:

    python -m mlab.options_hist                 # (re)download the raw history
    python -m mlab.train --symbol all --horizons all --with-options   # train with option features

The 5-day pilot (08-24..28) is superseded by the full-history experiment:
the walk-forward comparison price-only vs price+option for every horizon
and both indices is reported in reports/ml_lab_report*.json / .txt.


## VPS / Railway deployment notes

The ML Lab code is committed (mlab/, tests, docs).  What the VPS still needs:

1. **Code + deps**: `requirements.txt` now includes `lightgbm` (and
   `xgboost`, already present).  No tensorflow needed on the VPS unless you
   retrain GRU models there (CPU-only; recommended to skip).
2. **Data** (gitignored, must be provisioned):
   - `data/NIFTY_5m.csv`, `data/BANKNIFTY_5m.csv` (price history)
   - `data/options/history/*.csv` - regenerate with
     `python -m mlab.options_hist` (needs a valid Dhan token) OR copy the
     95 MB folder from this machine.
   - Option feature cache rebuilds automatically on first train.
3. **Models**: `models/ml_lab/` is gitignored.  Either copy the trained
   artifacts from this machine or retrain on the VPS with
   `python -m mlab.train --symbol all --horizons all --with-options`.
4. **Dhan creds**: `DHAN_CLIENT_ID` / access token via the Athena env or
   the token file (the terminal's existing auth handles this).
5. **Live use**: `ml-lab --predict` fetches the live option chain from
   Dhan on the VPS; the snapshot recorder (`ml-lab --record`) accumulates
   chain history there too.

Sanity check on the VPS after deploy:

    python -m unittest tests.test_mlab -v        # 10 tests
    python -m mlab.train --symbol nifty --horizons h6 --with-options --quick
    python run_terminal.py ml-lab --predict nifty,h6
