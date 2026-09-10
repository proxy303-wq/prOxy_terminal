#!/bin/bash
# PrOxy Trading Terminal - redeploy from git (run as root on the VPS).
# Usage: bash /opt/proxy/deploy/deploy.sh   (the GitHub Action calls this)
#
# Why this script is defensive: git pull --ff-only aborts when the working tree
# is not clean, and this host is never clean -
#   (a) the running workers rewrite tracked files, and
#   (b) files that an incoming commit adds often already exist here as untracked
#       files, which git refuses to overwrite.
# Both aborted every automatic deploy silently, leaving the host many commits
# behind. So we now move those files aside (and stash local edits) first, keeping
# a recoverable backup under /root/deploy_backup_<timestamp>/.
set -euo pipefail

cd /opt/proxy
ts=$(date +%Y%m%d_%H%M%S)
backup="/root/deploy_backup_$ts"
mkdir -p "$backup"

echo "== fetch =="
git fetch origin --quiet

# --- 1. untracked files that this update would create block a fast-forward pull
git diff --name-only --diff-filter=A HEAD origin/main | sort > "$backup/incoming.txt" || true
git ls-files --others --exclude-standard | sort > "$backup/untracked.txt" || true
comm -12 "$backup/incoming.txt" "$backup/untracked.txt" > "$backup/conflicts.txt" || true
if [ -s "$backup/conflicts.txt" ]; then
  echo "== moving aside $(wc -l < "$backup/conflicts.txt") untracked file(s) that this update adds =="
  while read -r f; do
    [ -z "$f" ] && continue
    mkdir -p "$backup/untracked/$(dirname "$f")"
    mv "$f" "$backup/untracked/$f"
  done < "$backup/conflicts.txt"
fi

# --- 2. local modifications to tracked files (runtime churn or hotfixes)
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "== stashing local modifications to tracked files (recoverable) =="
  git diff > "$backup/local_changes.patch" || true
  git status --short > "$backup/status_before.txt" || true
  git stash push -m "deploy autostash $ts" || true
fi

echo "== git pull =="
git pull --ff-only origin main
echo "now at: $(git log --oneline -1)"

# reinstall deps only if requirements.txt changed
if git rev-parse HEAD@{1} >/dev/null 2>&1; then
  if git diff --quiet HEAD@{1} HEAD -- requirements.txt; then
    echo "== requirements unchanged, skipping pip =="
  else
    echo "== requirements changed, reinstalling =="
    /opt/proxy/venv/bin/pip install -r requirements.txt --quiet
  fi
else
  echo "== first deploy, ensuring deps =="
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

echo "== preserved on this host (recover with git stash pop / copying back) =="
git stash list | head -n 5 || true
echo "backup dir: $backup"
echo "== deploy done =="
