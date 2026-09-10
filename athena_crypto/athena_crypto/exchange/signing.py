"""Delta Exchange HMAC-SHA256 request signing (mirrors the official python-rest-client)."""
import hashlib
import hmac
import json
import time
import urllib.parse


def get_timestamp() -> str:
    return str(int(time.time()))


def query_string(query) -> str:
    """Serialise query params exactly as the official client does for signing."""
    if not query:
        return ""
    pairs = []
    for key, value in query.items():
        if value is None:
            continue
        pairs.append(key + "=" + urllib.parse.quote_plus(str(value)))
    return "?" + "&".join(pairs)


def body_string(payload) -> str:
    if payload is None:
        return ""
    return json.dumps(payload, separators=(",", ":"))


def generate_signature(secret: str, message: str) -> str:
    h = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return h.hexdigest()


def sign_request(api_secret: str, method: str, path: str, query=None, payload=None, timestamp=None):
    """Return (signature, timestamp_str) for a REST request."""
    ts = timestamp or get_timestamp()
    message = method + ts + path + query_string(query) + body_string(payload)
    return generate_signature(api_secret, message), ts

