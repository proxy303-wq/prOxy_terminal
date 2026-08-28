#!/bin/bash
# PrOxy Trading Terminal - Lightsail instance bootstrap (first boot, root)
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y python3 python3-pip python3-venv git rsync ufw curl jq

# app home + mount point for the data volume (created in setup-instance.sh)
mkdir -p /opt/proxy/reports

# marker so setup-instance.sh can wait for first-boot packages
touch /opt/proxy/.userdata-done
echo "[userdata] bootstrap done"
