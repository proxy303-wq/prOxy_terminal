"""
PrOxy Trading Terminal - Dhan auth (long-lived API key)
=======================================================

Replaces the expiring 24-hour access-token chore with the long-lived
Dhan partner credentials found in:

    C:\Athena_X\dhan API KKEY.txt      (API KEY  +  API Secret, ~12-month)

Token resolution order (port of dhan-auto-trader/src/broker/auth.py):
    1. DHAN_ACCESS_TOKEN (or reports/dhan_token.txt) if not expired
    2. RenewToken        - silently extends an ACTIVE token by 24h
    3. API key + secret  - interactive consent flow (browser login once
                           per refresh), then the fresh token is saved
                           to reports/dhan_token.txt for future runs

Secrets are never printed; logs mask them.
"""

import base64
import json
import os
import time
import urllib.parse
import urllib.request

from .config import REPORT_DIR

AUTH_BASE = "https://auth.dhan.co"
API_BASE = "https://api.dhan.co/v2"
API_KEY_FILE = os.getenv("DHAN_API_KEY_FILE", r"C:\Athena_X\dhan API KKEY.txt")
TOKEN_FILE = os.path.join(REPORT_DIR, "dhan_token.txt")


def _http_json(url, method="GET", headers=None, params=None, data=None, timeout=20):
    """urllib-based JSON request (no requests dependency needed)."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers=headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"status": "error", "http": exc.code, "text": exc.read().decode(errors="replace")[:300]}


# ------------------------------------------------------------
# JWT helpers
# ------------------------------------------------------------

def _b64url_decode(s):
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def token_expiry(token):
    """Epoch seconds when the Dhan JWT expires (0 if unparseable)."""
    try:
        payload = token.split(".")[1]
        return float(json.loads(_b64url_decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


def token_is_expired(token, margin_s=3600):
    exp = token_expiry(token)
    return exp <= 0 or exp - time.time() < margin_s


# ------------------------------------------------------------
# credential loading (masked in logs)
# ------------------------------------------------------------

def _value_after_label(line, label):
    """Strip 'label' and any of ':', '-', '=' separators from the value."""
    rest = line[len(label):].strip()
    for sep in (":", "-", "="):
        if rest.startswith(sep):
            return rest[len(sep):].strip()
    return rest


def load_api_keypair(path=API_KEY_FILE):
    """Read 'API KEY / API Secret' lines from the file. Returns (key, secret) or (None, None)."""
    if not os.path.exists(path):
        return None, None
    key = secret = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                low = line.lower()
                if low.startswith("api key"):
                    key = _value_after_label(line, "API KEY") or _value_after_label(line, "api key")
                elif low.startswith("api secret"):
                    secret = _value_after_label(line, "API Secret") or _value_after_label(line, "api secret")
    except Exception:
        return None, None
    return (key or None), (secret or None)


def load_saved_token(path=TOKEN_FILE):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                token = fh.read().strip()
                if token:
                    return token
    except Exception:
        pass
    return None


def save_token(token, path=TOKEN_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(token)


# ------------------------------------------------------------
# token flows
# ------------------------------------------------------------

def renew_token(client_id, token):
    """GET /v2/RenewToken - extend an ACTIVE token by 24h."""
    data = _http_json(f"{API_BASE}/RenewToken", method="GET",
                      headers={"access-token": token, "dhanClientId": client_id})
    tok = data.get("accessToken", "")
    if tok:
        return tok
    return None


def api_key_consent(api_key, secret, client_id=None):
    """POST /app/generate-consent?client_id=... -> {consentAppId}.

    The credentials in C:\Athena_X\dhan API KKEY.txt are Dhan APP
    credentials (app_id/app_secret) - the APP flow is the one Dhan
    accepts (verified: consentAppId GENERATED).  Headers app_id/app_secret.
    """
    params = {"client_id": client_id} if client_id else {}
    return _http_json(f"{AUTH_BASE}/app/generate-consent", method="POST",
                      params=params,
                      headers={"app_id": api_key, "app_secret": secret})


def consent_login_url(consent_id):
    return f"{AUTH_BASE}/login/consentApp-login?consentAppId={urllib.parse.quote(consent_id)}"


def consume_consent(api_key, secret, token_id):
    """POST /app/consumeApp-consent?tokenId=... -> {accessToken, expiryTime}."""
    return _http_json(f"{AUTH_BASE}/app/consumeApp-consent", method="POST",
                      params={"tokenId": token_id},
                      headers={"app_id": api_key, "app_secret": secret})


def consent_refresh(api_key, secret, client_id=None, ask=input, notify=print):
    """
    Interactive consent flow: prints the login URL, waits for the user to
    paste the tokenId from the redirect, exchanges it for a fresh access
    token and saves it.  Returns the token or None.
    """
    consent = api_key_consent(api_key, secret, client_id=client_id)
    consent_id = (consent.get("consentAppId") or consent.get("consentId")
                  or consent.get("consent_id") or "")
    if not consent_id:
        notify(f"consent failed: {consent}")
        return None
    notify("1. Open this URL in a browser and log in with your Dhan account:")
    notify("   " + consent_login_url(consent_id))
    token_id = ask("2. Paste the full redirect URL or just the tokenId: ").strip()
    if "tokenId=" in token_id:
        token_id = token_id.split("tokenId=")[-1].split("&")[0]
    data = consume_consent(api_key, secret, token_id)
    token = data.get("accessToken", "")
    if not token:
        notify(f"consume-consent failed: {data}")
        return None
    save_token(token)
    notify(f"access token saved ({data.get('expiryTime', '?')}); will auto-renew thereafter.")
    return token


def resolve_token(client_id, access_token="", api_key=None, api_secret=None,
                  interactive=True, notify=print):
    """
    Best-effort token resolution (the replacement for the expiring token):
      1. current access token (from env / saved file) if not expired
      2. RenewToken on an active token (silent +24h)
      3. API key consent flow (one browser login per refresh)
    Returns (token, source) or (None, reason).
    """
    if access_token and not token_is_expired(access_token):
        return access_token, "env/saved token"
    if access_token:
        renewed = renew_token(client_id, access_token)
        if renewed and not token_is_expired(renewed):
            save_token(renewed)
            return renewed, "renewed via RenewToken"
        notify("access token expired and could not be renewed.")
    if api_key and api_secret:
        if interactive:
            token = consent_refresh(api_key, api_secret, client_id=client_id, notify=notify)
            if token and not token_is_expired(token):
                return token, "consent flow (long-lived API key)"
            return None, "consent flow failed"
        return None, "token expired; run 'python run_terminal.py dhan-auth' once"
    return None, "no usable token and no API key/secret configured"
