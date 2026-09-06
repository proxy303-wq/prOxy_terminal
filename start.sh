#!/bin/bash
# PrOxy supervisor: run the paper/live workers alongside the dashboard.
# All worker loops are supervised (restarts on crash OR hang via timeout).
# NIFTY reads reports/mode.json (the Telegram master switch).  BANKNIFTY
# reads reports/mode_banknifty.json, FINNIFTY reads mode_finnifty.json -
# each stays PAPER while its file is absent - flip with tools/_bn_live.py /
# tools/_fin_live.py (never live by accident).
#
# PROXY_ALLOCATION_PCT per worker = that engine's share of the FULL Dhan
# balance (NIFTY/BN defaults keep the current LIVE 0.5/0.5 basis).
# FINNIFTY starts PAPER at a small 0.2 basis; the live split is a USER
# decision when FINNIFTY is flipped (rebalance all three via the envs).
# Streamlit runs in the foreground so healthchecks track it.
(
  while true; do
    echo "[supervisor] starting railway_worker.py (NIFTY)"
    PROXY_ALLOCATION_PCT="${PROXY_ALLOCATION_PCT_NIFTY:-0.5}" timeout 12h python railway_worker.py
    echo "[supervisor] nifty worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

(
  while true; do
    echo "[supervisor] starting railway_worker.py --variant banknifty (paper until mode_banknifty.json says live)"
    PROXY_ALLOCATION_PCT="${PROXY_ALLOCATION_PCT_BANKNIFTY:-0.5}" timeout 12h python railway_worker.py --variant banknifty
    echo "[supervisor] banknifty worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

(
  while true; do
    echo "[supervisor] starting railway_worker.py --variant finnifty (paper until mode_finnifty.json says live)"
    PROXY_ALLOCATION_PCT="${PROXY_ALLOCATION_PCT_FINNIFTY:-0.2}" timeout 12h python railway_worker.py --variant finnifty
    echo "[supervisor] finnifty worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

exec streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port "$PORT" --server.headless true