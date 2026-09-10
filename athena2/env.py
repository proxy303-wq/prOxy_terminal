"""Athena 2.0 - credential loading for live-data paths (no secrets in code).

The preserved proxy code expects DHAN_* credentials from ATHENA_ENV_FILE
(default C:\Athena_X\.env), which is where this host stores DHAN_CLIENT_ID,
DHAN_ACCESS_TOKEN plus the auto-renew pair DHAN_PIN / DHAN_TOTP_SECRET.  The
repo also keeps a deployment copy in .oracle/box.env and a local .env.

load_creds_env() prefers the canonical file (overriding an already-exported,
possibly EXPIRED token) and then fills any missing keys from the repo copies.
Values are read from disk at runtime and never embedded in source.
"""
from __future__ import annotations

import os
from typing import List, Optional

CANONICAL = r"C:\Athena_X\.env"
REPO_CANDIDATES = [".oracle/box.env", ".env"]
_PREFIXES = ("DHAN_", "TELEGRAM_", "DELTA_", "ATHENA_")


def _parse(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip(chr(34)).strip(chr(39)).strip()
    except OSError:
        return {}
    return out


def load_creds_env(canonical: Optional[str] = None,
                   repo_files: Optional[List[str]] = None) -> str:
    """Load credentials into os.environ; returns the primary file used."""
    canonical = canonical or os.environ.get("ATHENA_ENV_FILE") or CANONICAL
    repo_files = repo_files if repo_files is not None else REPO_CANDIDATES
    primary = ""
    if canonical and os.path.exists(canonical):
        for k, v in _parse(canonical).items():
            if k.startswith(_PREFIXES) and v:
                os.environ[k] = v        # canonical wins (refreshes stale tokens)
        primary = canonical
    for cand in repo_files:
        p = cand if os.path.isabs(cand) else os.path.join(os.getcwd(), cand)
        if not os.path.exists(p):
            continue
        if not primary:
            primary = p
        for k, v in _parse(p).items():
            if k.startswith(_PREFIXES) and v and not os.environ.get(k):
                os.environ[k] = v
    if primary and not os.environ.get("ATHENA_ENV_FILE"):
        os.environ["ATHENA_ENV_FILE"] = primary
    return primary


def token_status() -> str:
    """Human-readable auth state for logs (never prints secret values)."""
    tok = os.environ.get("DHAN_ACCESS_TOKEN") or ""
    has_pin = bool(os.environ.get("DHAN_PIN"))
    has_totp = bool(os.environ.get("DHAN_TOTP_SECRET"))
    exp = "unknown"
    try:
        from proxy.dhan_auth import token_is_expired
        exp = "yes" if token_is_expired(tok) else "no"
    except Exception:
        pass
    return ("client_id=" + ("set" if os.environ.get("DHAN_CLIENT_ID") else "MISSING")
            + " token=" + ("set" if tok else "MISSING")
            + " token_expired=" + exp
            + " renew_creds=" + ("yes" if (has_pin and has_totp) else "no"))
