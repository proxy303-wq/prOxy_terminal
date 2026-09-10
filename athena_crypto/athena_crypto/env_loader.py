"""Small .env loader (KEY=VALUE lines) with optional dotenv fallback."""
import os


def _parse_env_text(text):
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # strip optional quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def load_env_file(path):
    """Load KEY=VALUE pairs from a file into a dict (no os.environ mutation)."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _parse_env_text(fh.read())
    except OSError:
        return {}


def load_env(path=None, candidates=None):
    """Return merged env dict from an explicit file or the first existing candidate."""
    if path:
        return load_env_file(path)
    if candidates:
        for cand in candidates:
            d = load_env_file(cand)
            if d:
                return d
    return {}

