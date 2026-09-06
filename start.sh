#!/bin/bash
# PrOxy supervisor: run the paper/live workers alongside the dashboard.
# All worker loops are supervised (restarts on crash OR hang via timeout).
#
# CAPITAL + ALLOCATION (user decision 2026-09-06 - account topped to ~7L):
#   NIFTY    0.40  (~2.8L basis)  - live (reports/mode.json); 4-lot standard fits 0.5% risk
#   FINNIFTY 0.30  (~2.1L basis)  - paper until mode_finnifty.json (tools/_fin_live.py)
#   FUTURES  0.30  (~2.1L basis)  - PAPER: gated on mode_futures.json + FUTURES_ALLOW_LIVE
#                                    + the measured-spread fill gate (run_futures_day
#                                    hard-aborts to paper until that passes)
#   BANKNIFTY DISABLED (loop removed; mode_banknifty.json deleted 06-Sep)
# Monthly target: 12.5% of 7L = ~87,500 INR across the three engines.
# (06-Sep rebalance: NIFTY 40 so the 4-lot standard fits 0.5% risk/trade)
# Each worker sizes off PROXY_ALLOCATION_PCT x the FULL Dhan balance at the
# session open (paper uses cfg.CAPITAL x the same split).
# Streamlit runs in the foreground so healthchecks track it.
(
  while true; do
    echo "[supervisor] starting railway_worker.py (NIFTY)"
    PROXY_ALLOCATION_PCT="${PROXY_ALLOCATION_PCT_NIFTY:-0.4}" timeout 12h python railway_worker.py
    echo "[supervisor] nifty worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

(
  while true; do
    echo "[supervisor] starting railway_worker.py --variant finnifty (paper until mode_finnifty.json says live)"
    PROXY_ALLOCATION_PCT="${PROXY_ALLOCATION_PCT_FINNIFTY:-0.3}" PAPER_LIVE_LIKE=1 PAPER_MODEL_SPREAD=1 PAPER_SPREAD_PER_SIDE=0.004 timeout 12h python railway_worker.py --variant finnifty
    echo "[supervisor] finnifty worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

(
  while true; do
    echo "[supervisor] starting railway_worker.py --variant futures (PAPER until mode_futures.json + FUTURES_ALLOW_LIVE + fill gate)"
    PROXY_ALLOCATION_PCT="${PROXY_ALLOCATION_PCT_FUTURES:-0.3}" PAPER_LIVE_LIKE=1 timeout 12h python railway_worker.py --variant futures
    echo "[supervisor] futures worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

exec streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port "$PORT" --server.headless true