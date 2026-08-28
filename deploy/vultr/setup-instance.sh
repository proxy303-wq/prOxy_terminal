#!/bin/bash
# PrOxy Trading Terminal - Vultr instance setup (run as root)
#   $1 = app tarball path  (default /root/proxy.tar.gz)
#   $2 = .env path         (default /root/.env)
#   $3 = systemd unit path (default /root/proxy-terminal.service)
set -euxo pipefail

TARBALL="${1:-/root/proxy.tar.gz}"
ENVFILE="${2:-/root/.env}"
UNIT="${3:-/root/proxy-terminal.service}"
APP_DIR=/opt/proxy

# base packages (fresh VPS, no lock conflicts)
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip python3-venv curl

# app files
mkdir -p "$APP_DIR"
tar -xzf "$TARBALL" -C "$APP_DIR"
chmod +x "$APP_DIR/start.sh" 2>/dev/null || true
cp "$ENVFILE" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
# strip carriage returns (files shipped from Windows)
find "$APP_DIR" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +

# python env + deps (takes a few minutes)
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# systemd service
cp "$UNIT" /etc/systemd/system/proxy-terminal.service
chmod 644 /etc/systemd/system/proxy-terminal.service
systemctl daemon-reload
systemctl enable --now proxy-terminal

sleep 20
systemctl status proxy-terminal --no-pager | head -n 15 || true
echo "--- health ---"
curl -fsS -o /dev/null -w 'health: %{http_code}\n' http://127.0.0.1:8080/_stcore/health || echo 'health: FAILED'
echo "--- heartbeat ---"
head -c 400 "$APP_DIR/reports/worker_heartbeat.json" 2>/dev/null || echo '(no heartbeat yet - worker may be warming up)'
echo "--- setup done ---"
