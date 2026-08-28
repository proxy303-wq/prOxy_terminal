#!/bin/bash
# PrOxy Trading Terminal - post-boot setup (run as root via sudo from orchestrator)
#   $1 = path to the app tarball (default /home/ubuntu/proxy.tar.gz)
#   $2 = path to the .env file   (default /home/ubuntu/.env)
set -euxo pipefail

TARBALL="${1:-/home/ubuntu/proxy.tar.gz}"
ENVFILE="${2:-/home/ubuntu/.env}"
APP_DIR=/opt/proxy

# wait for first-boot user-data packages (python3-venv etc.)
for i in $(seq 1 60); do
  [ -f /opt/proxy/.userdata-done ] && break
  sleep 10
done
[ -f /opt/proxy/.userdata-done ] || echo "[setup] WARNING: user-data not finished after 10 min"

# ---------------------------------------------------------------- data volume
ROOT_DEV=$(findmnt -n -o SOURCE / | sed 's/[0-9]*$//')
DATA_DEV=""
for d in $(lsblk -dnp -o NAME,TYPE | awk '$2=="disk" {print $1}'); do
  if [ "$d" != "$ROOT_DEV" ]; then DATA_DEV="$d"; break; fi
done

if [ -n "$DATA_DEV" ]; then
  echo "[setup] data volume found: $DATA_DEV"
  if ! blkid "$DATA_DEV" >/dev/null 2>&1; then
    echo "[setup] formatting $DATA_DEV (ext4)"
    mkfs.ext4 -q "$DATA_DEV"
  fi
  mkdir -p "$APP_DIR/reports"
  if ! grep -q 'reports' /etc/fstab; then
    echo "$DATA_DEV $APP_DIR/reports ext4 defaults,nofail 0 2" >> /etc/fstab
  fi
  mount -a || true
  df -h "$APP_DIR/reports" | tail -n1
else
  echo "[setup] WARNING: no data volume attached - state will be EPHEMERAL"
fi

# ---------------------------------------------------------------- app files
mkdir -p "$APP_DIR"
tar -xzf "$TARBALL" -C "$APP_DIR"
chmod +x "$APP_DIR/start.sh" 2>/dev/null || true
cp "$ENVFILE" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
# strip carriage returns if the tarball came from Windows
find "$APP_DIR" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +

# ---------------------------------------------------------------- python env
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------- systemd
cp /home/ubuntu/proxy-terminal.service /etc/systemd/system/proxy-terminal.service
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
