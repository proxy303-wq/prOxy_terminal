#!/bin/bash
# PrOxy Trading Terminal - one-command redeploy from git (run as root on the VPS)
# Usage: bash /opt/proxy/deploy.sh
set -euo pipefail

cd /opt/proxy

echo "== git pull =="
git pull --ff-only origin main

# reinstall deps only if requirements.txt changed
if git diff --quiet HEAD@{1} HEAD -- requirements.txt; then
  echo "== requirements unchanged, skipping pip =="
else
  echo "== requirements changed, reinstalling =="
  /opt/proxy/venv/bin/pip install -r requirements.txt --quiet
fi

echo "== restarting service =="
systemctl restart proxy-terminal

sleep 15
systemctl status proxy-terminal --no-pager | head -n 8 || true
echo "== health =="
curl -fsS -o /dev/null -w 'health: %{http_code}\n' http://127.0.0.1:8080/_stcore/health || echo 'health: FAILED'
echo "== heartbeat =="
head -c 300 /opt/proxy/reports/worker_heartbeat.json 2>/dev/null || echo '(no heartbeat yet)'
echo "== deploy done =="
