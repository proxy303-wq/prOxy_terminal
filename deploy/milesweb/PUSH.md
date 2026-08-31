# Push PrOxy Terminal to MilesWeb (Indian VPS, Indian IP)

MilesWeb = Indian hosting provider with Mumbai/Bangalore datacenters,
Indian IPs, and INR billing (UPI/card - no credit card needed).

## 1. Order the VPS (milesweb.in)
- Product: **Unmanaged Linux VPS** (KVM) - managed is fine too but unmanaged
  is cheaper and we administer it ourselves.
- OS: **Ubuntu 22.04 or 24.04**
- RAM: **2 GB minimum, 4 GB recommended** (2GB runs fine with ML skipped)
- Disk: 40 GB+
- Datacenter: **Mumbai or Bangalore** (Indian IP)
- Bandwidth: any (our traffic is tiny: REST polling + dashboard)
- You get: public IP + root SSH password (or key)

## 2. Hand me these 3 things
- VPS public IP
- SSH user (usually root) + password (or your private key path)
- Confirm Ubuntu version

## 3. I push (from the home PC)
    scp .oracle/proxy.tar.gz .env deploy/aws/setup-instance.sh deploy/aws/proxy-terminal.service root@<IP>:/root/
    ssh root@<IP>  "bash /root/setup-instance.sh /root/proxy.tar.gz /root/.env"
    curl -fsS http://<IP>:8080/_stcore/health
Then I set the env flags + paper mode + verify the worker heartbeat.

## 4. Box env (set on the VPS)
    PROXY_ML_ENABLED=false        # skip TensorFlow on a small box (advisory only anyway)
    PROXY_AUTO_GENERATE_TOKEN=false  # token CONSUMER - never generate
    PROXY_PUSH_TOKEN_TO_RAILWAY=false
    reports/mode.json = {"mode": "paper"}

## Why MilesWeb over Oracle free
- INSTANT (no ARM capacity lottery), Indian IP, INR billing.
- ~Rs.500-900/mo for 2-4GB - small cost for seamless 24/7 automation.
- The Oracle HYD ARM watcher can keep running in the background; if it
  lands, migrate the same tarball there and cancel MilesWeb.
