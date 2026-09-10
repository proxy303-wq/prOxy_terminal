"""TOML loading with fallbacks so the package runs on Python 3.9+.

Order of preference: stdlib tomllib (3.11+) -> tomli (if installed) -> a small
built-in parser covering the subset used by config/config.toml (tables, strings,
numbers, booleans, inline arrays and comments). The VPS venv runs Python 3.9, so
the fallback path is the one that actually executes in production.
"""
import os
import re


def _strip_comment(line):
    out = []
    in_str = False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        if ch == "#" and not in_str:
            break
        out.append(ch)
    return "".join(out).strip()


def _coerce(raw):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p) for p in inner.split(",") if p.strip()]
    try:
        if re.match(r"^[+-]?[0-9]+$", raw):
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def minimal_parse(text):
    """Parse the config.toml subset used by this project."""
    root = {}
    current = root
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line)
        if not line:
            continue
        if line.startswith("[[") and line.endswith("]]"):
            continue  # arrays of tables are not used here
        if line.startswith("[") and line.endswith("]"):
            path = [p.strip() for p in line[1:-1].split(".") if p.strip()]
            current = root
            for part in path:
                nxt = current.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    current[part] = nxt
                current = nxt
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = _coerce(value)
    return root


def load_toml(path):
    if not path or not os.path.isfile(path):
        return {}
    text = None
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:
        pass
    except Exception:
        return {}
    try:
        import tomli  # third-party backport
        with open(path, "rb") as fh:
            return tomli.load(fh)
    except ImportError:
        pass
    except Exception:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    return minimal_parse(text)
