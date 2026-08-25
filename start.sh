#!/bin/bash
# Railway web process: run the PAPER trading worker alongside the dashboard.
# The worker loop is supervised (restarts on crash OR hang via timeout);
# Streamlit runs in the foreground so Railway healthchecks track it.
(
  while true; do
    echo "[supervisor] starting railway_worker.py"
    timeout 12h python railway_worker.py
    echo "[supervisor] worker exited (code $?) - restarting in 30s"
    sleep 30
  done
) &

exec streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port "$PORT" --server.headless true
