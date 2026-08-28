#!/bin/bash
# PrOxy Trading Terminal - MilesWeb (AlmaLinux) setup, run as root
#   $1 = app tarball path  (default /root/proxy.tar.gz)
#   $2 = .env path         (default /root/.env)
#   $3 = systemd unit path (default /root/proxy-terminal.service)
set -euxo pipefail

TARBALL="${1:-/root/proxy.tar.gz}"
ENVFILE="${2:-/root/.env}"
UNIT="${3:-/root/proxy-terminal.service}"
APP_DIR=/opt/proxy

# --- Python 3.11 (AppStream); fallback 3.9 ---
if ! command -v python3.11 >/dev/null 2>&1; then
  dnf install -y python3.11 python3.11-pip python3.11-venv python3.11-devel || dnf install -y python39 python39-pip
fi
PY=$(command -v python3.11 || command -v python3.9 || command -v python3)
echo "[setup] python: $PY"

# --- app files ---
mkdir -p "$APP_DIR"
tar -xzf "$TARBALL" -C "$APP_DIR"
chmod +x "$APP_DIR/start.sh" 2>/dev/null || true
cp "$ENVFILE" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
find "$APP_DIR" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +

# --- venv + deps (takes a few minutes) ---
"$PY" -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# --- systemd service ---
cp "$UNIT" /etc/systemd/system/proxy-terminal.service
chmod 644 /etc/systemd/system/proxy-terminal.service
systemctl daemon-reload
systemctl enable --now proxy-terminal

# --- firewall: open 8080 ---
if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=8080/tcp || true
  firewall-cmd --reload || true
fi

sleep 20
systemctl status proxy-terminal --no-pager | head -n 15 || true
echo "--- health ---"
curl -fsS -o /dev/null -w 'health: %{http_code}\n' http://127.0.0.1:8080/_stcore/health || echo 'health: FAILED'
echo "--- heartbeat ---"
head -c 400 "$APP_DIR/reports/worker_heartbeat.json" 2>/dev/null || echo '(no heartbeat yet - worker may be warming up)'
echo "--- setup done ---"
