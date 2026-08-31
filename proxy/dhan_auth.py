r"""
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


def token_type(token):
    """tokenConsumerType from the JWT: 'SELF' (Dhan-Web login - market data OK)
    vs 'APP' (TOTP/app-generated - NO market data, NOT renewable)."""
    try:
        if not token:
            return None
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        import base64
        import json as _json
        return _json.loads(base64.urlsafe_b64decode(payload)).get("tokenConsumerType")
    except Exception:
        return None


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
            with open(path, "r", encoding="utf-8-sig") as fh:
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

    The credentials in C:/Athena_X/dhan API KKEY.txt are Dhan APP
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




# ------------------------------------------------------------
# TOTP (RFC 6238) - fully automatic 24h tokens, no browser needed
# ------------------------------------------------------------
# Set DHAN_PIN (your Dhan trading PIN) + DHAN_TOTP_SECRET (base32 secret
# from the authenticator app QR) to skip the consent flow entirely:
# the terminal generates + renews the access token itself.

_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _b32decode(s):
    s = (s or "").upper().strip().replace(" ", "").replace("-", "")
    bits = 0
    value = 0
    out = bytearray()
    for ch in s:
        idx = _B32_ALPHABET.find(ch)
        if idx < 0:
            continue
        value = (value << 5) | idx
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((value >> bits) & 0xFF)
    return bytes(out)


def totp(secret, for_time=None, digits=6, period=30):
    """RFC 6238 TOTP code for a base32 secret (authenticator-app format)."""
    import hashlib
    import hmac
    import struct
    if for_time is None:
        for_time = time.time()
    counter = int(for_time // period)
    key = _b32decode(secret)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def generate_access_token(client_id, pin, totp_code):
    """POST /app/generateAccessToken - programmatic 24h token."""
    return _http_json(f"{AUTH_BASE}/app/generateAccessToken", method="POST",
                      params={"dhanClientId": client_id, "pin": pin, "totp": totp_code})


def auto_token_from_totp(client_id, pin, totp_secret, notify=print):
    """Generate a fresh access token from the TOTP secret + trading PIN."""
    try:
        code = totp(totp_secret)
        data = generate_access_token(client_id, pin, code)
        token = data.get("accessToken", "")
        if token:
            save_token(token)
            notify("access token generated automatically via TOTP (no browser needed)")
            return token
        notify(f"TOTP token generation failed: {data}")
    except Exception as exc:
        notify(f"TOTP token generation failed: {exc}")
    return None


def validate_token(client_id, token):
    """Ask Dhan whether the token is actually accepted (catches corrupted
    copies that pass structural checks but fail DH-906).

    Tries the official dhanhq SDK (get_fund_limits) first; if the SDK is not
    installed or that endpoint misbehaves, falls back to a lightweight raw
    REST ping (/v2/optionchain/expirylist) - a 200 with status success is
    sufficient proof the token works for market data."""
    try:
        from dhanhq import DhanContext, dhanhq
        client = dhanhq(DhanContext(client_id, token))
        r = client.get_fund_limits()
        if r and r.get('status') == 'success':
            return True
    except Exception:
        pass
    try:
        data = _http_json(f"{API_BASE}/optionchain/expirylist", method="POST",
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json",
                                   "access-token": token, "client-id": client_id},
                          data={"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"})
        return bool(data and data.get("status") == "success")
    except Exception:
        return False


def auto_renew_token(client_id, access_token=None, pin=None, totp_secret=None, notify=print, margin_hours=2):
    """Fully automatic 24-hour token management (no browser, no consent code).

    Candidate priority:
      1. env DHAN_ACCESS_TOKEN (explicit user intent) if still valid
      2. saved token file if still valid
      3. RenewToken on the best candidate (+24h, silent - SELF tokens only)
      4. TOTP-generate an APP token (funds/portfolio only, NO market data)
    Returns (token, source) or (None, reason).
    """
    margin_s = int(margin_hours * 3600)
    candidates = []
    if access_token:
        candidates.append(("env token", access_token))
    saved = load_saved_token()
    if saved:
        candidates.append(("saved token", saved))
    # 1) first candidate that is still valid beyond the margin AND passes a
    #    real API validation (catches corrupted copies - DH-906)
    for name, tok in candidates:
        if tok and not token_is_expired(tok, margin_s=margin_s):
            if validate_token(client_id, tok):
                return tok, name
            notify(f"{name} failed Dhan validation (corrupted copy?) - trying the next candidate")
    # 2) try RenewToken on the best candidate (works ONLY on SELF Dhan-Web
    #    tokens; preserves the SELF type which market data needs)
    for name, tok in candidates:
        if not tok:
            continue
        renewed = renew_token(client_id, tok)
        if renewed and not token_is_expired(renewed, margin_s=margin_s):
            save_token(renewed)
            notify(f"access token auto-renewed via RenewToken (+24h, expires "
                   f"{time.strftime('%d %b %H:%M', time.localtime(token_expiry(renewed)))})")
            return renewed, f"renewed via RenewToken ({name})"
        notify(f"{name} could not be renewed via RenewToken "
               "(SELF tokens only) - a fresh SELF token is needed for market data")
    # 3) last resort: TOTP generates an APP token.  APP tokens CANNOT access
    #    market data (WS feed / REST quotes) and cannot be renewed, so only
    #    use them when nothing else is available - and say so loudly.
    if pin and totp_secret:
        tok = auto_token_from_totp(client_id, pin, totp_secret, notify=notify)
        if tok and not token_is_expired(tok, margin_s=0):
            notify("access token auto-regenerated via TOTP - fully automatic daily renewal (no browser needed)")
            return tok, "TOTP auto-regenerated"
        return None, "TOTP token generation failed"
    return None, "no usable token and no DHAN_PIN/DHAN_TOTP_SECRET configured"


def is_token_generator():
    """Only ONE process should generate tokens: every TOTP generateAccessToken
    call invalidates the previous token, so multiple generators (local machine
    + Railway container) keep killing each other's tokens.

    Default TRUE (local = generator).  The deployed container sets
    PROXY_AUTO_GENERATE_TOKEN=false and only CONSUMES."""
    return os.environ.get("PROXY_AUTO_GENERATE_TOKEN", "true").lower() == "true"


def _push_token_to_railway(token, notify=print):
    """Optional: push the fresh local token into Railway's DHAN_ACCESS_TOKEN.

    Enabled ONLY on the local generator machine via
    PROXY_PUSH_TOKEN_TO_RAILWAY=true (never set it on the container, or the
    container would redeploy itself in a loop).  Throttled by a marker file
    so a redeploy happens at most once per token change (once a day, after
    market close).  Never raises.
    """
    if os.environ.get("PROXY_PUSH_TOKEN_TO_RAILWAY", "").lower() != "true":
        return False
    if not token:
        return False
    marker = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "reports",
        "railway_pushed_token.txt"))
    try:
        if os.path.exists(marker) and open(marker, encoding="ascii").read().strip() == token:
            return False  # this exact token was already pushed
        import shutil
        import subprocess
        cli = shutil.which("railway")
        if not cli:
            notify("Railway token push skipped - 'railway' CLI not found on PATH")
            return False
        result = subprocess.run(
            [cli, "variables", "--set", f"DHAN_ACCESS_TOKEN={token}"],
            capture_output=True, text=True, timeout=180,
            cwd=os.path.dirname(marker))
        ok = result.returncode == 0
        if ok:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="ascii") as fh:
                fh.write(token)
            notify("Railway token auto-refreshed (+24h) - redeploy triggered")
        else:
            notify(f"Railway token push failed: {(result.stderr or result.stdout or '')[:120]}")
        return ok
    except Exception as exc:
        notify(f"Railway token push error: {exc}")
        return False


def resolve_token_safe(client_id, notify=print):
    """Token resolution that respects the generator/consumer split.

    Generator (local): auto_renew_token (validate -> RenewToken -> TOTP),
    then pushes the fresh token to Railway when PROXY_PUSH_TOKEN_TO_RAILWAY
    is set, so the container's env token auto-refreshes daily too.
    Consumer (container): env/saved token AS-IS, never generates."""
    if is_token_generator():
        tok, src = auto_renew_token(
            client_id, access_token=os.environ.get("DHAN_ACCESS_TOKEN"),
            pin=os.environ.get("DHAN_PIN"), totp_secret=os.environ.get("DHAN_TOTP_SECRET"),
            notify=notify)
        if tok:
            _push_token_to_railway(tok, notify=notify)
        return tok, src
    # consumer (container): the token pasted via the dashboard System tab
    # (reports/dhan_token.txt) takes priority - it is refreshed daily
    saved = load_saved_token()
    if saved:
        return saved, "saved token (pasted via dashboard)"
    tok = os.environ.get("DHAN_ACCESS_TOKEN")
    return (tok, "env/saved token (container, no generation)") if tok else (None, "no token available")


def resolve_token(client_id, access_token="", api_key=None, api_secret=None,
                  interactive=True, notify=print, pin=None, totp_secret=None):
    """
    Best-effort token resolution (the replacement for the expiring token):
      1. current access token (from env / saved file) if not expired
      2. RenewToken on an active token (silent +24h)
      3. API key consent flow (one browser login per refresh)
    Returns (token, source) or (None, reason).
    """
    if access_token and not token_is_expired(access_token):
        if validate_token(client_id, access_token):
            return access_token, "env/saved token"
        notify("saved/env token failed Dhan validation (corrupted copy?) - resolving fresh")
    if access_token:
        renewed = renew_token(client_id, access_token)
        if renewed and not token_is_expired(renewed):
            save_token(renewed)
            return renewed, "renewed via RenewToken"
        notify("access token expired and could not be renewed.")
    if pin and totp_secret:
        tok = auto_token_from_totp(client_id, pin, totp_secret, notify=notify)
        if tok and not token_is_expired(tok):
            return tok, "TOTP auto-generated"
        return None, "TOTP token generation failed"
    if api_key and api_secret:
        if interactive:
            token = consent_refresh(api_key, api_secret, client_id=client_id, notify=notify)
            if token and not token_is_expired(token):
                return token, "consent flow (long-lived API key)"
            return None, "consent flow failed"
        return None, "token expired; run 'python run_terminal.py dhan-auth' once"
    return None, "no usable token and no API key/secret configured"