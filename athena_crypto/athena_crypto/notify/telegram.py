"""Telegram notifications for ATHENA CRYPTO.

Reuses the same bot token and chat as the rest of the terminal (TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID from the service environment), so crypto alerts land in the same chat
as the NIFTY ones.

Design rules:
  * never raise - a notification failure must never affect trading
  * no third-party dependency (plain HTTPS POST to the Bot API)
  * rate limited, so a burst of cycles cannot spam the chat
  * secrets are never logged or echoed
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request

log = logging.getLogger("athena.notify")

API = "https://api.telegram.org/bot%s/sendMessage"


class TelegramNotifier:
    def __init__(self, token=None, chat_id=None, enabled=True, min_interval=1.0,
                 transport=None, timeout=10):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(enabled) and bool(self.token) and bool(self.chat_id)
        self.min_interval = float(min_interval)
        self._last = 0.0
        self._transport = transport or self._http_post
        self.timeout = timeout
        self.sent = 0
        self.errors = 0
        if enabled and not self.enabled:
            log.info("telegram notifications unavailable (token/chat not configured)")

    # ------------------------------------------------------------------ transport
    def _http_post(self, url, payload):
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(url, data=data)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode()

    # ------------------------------------------------------------------ api
    def send(self, text, silent=False):
        """Send a message. Returns True when delivered, False otherwise (never raises)."""
        if not self.enabled:
            return False
        try:
            now = time.time()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                time.sleep(min(wait, 2.0))
            payload = {"chat_id": self.chat_id, "text": text[:4000],
                       "disable_web_page_preview": "true"}
            if silent:
                payload["disable_notification"] = "true"
            self._transport(API % self.token, payload)
            self._last = time.time()
            self.sent += 1
            return True
        except Exception as exc:
            self.errors += 1
            log.warning("telegram send failed: %s", exc)
            return False

    # ------------------------------------------------------------------ messages
    def entry(self, symbol, direction, size, price, stop, target, notional, venue_tag):
        arrow = "\U0001F7E2 LONG" if direction == "long" else "\U0001F534 SHORT"
        self.send("%s %s  %s\n"
                  "%s  size %s @ %s\n"
                  "stop %s  |  target %s  |  notional $%.0f\n"
                  "venue: %s"
                  % (venue_tag, symbol, arrow, direction.upper(), size, _num(price),
                     _num(stop), _num(target), notional or 0.0, venue_tag))

    def exit(self, symbol, direction, entry, exit_price, reason, r_multiple, pnl):
        icon = "\u2705" if (pnl or 0) > 0 else "\u274C"
        r_txt = ("%+.2fR" % r_multiple) if r_multiple is not None else "n/a"
        self.send("%s CLOSED %s (%s)\n"
                  "entry %s -> exit %s\n"
                  "reason %s  |  P&L %+.2f  |  %s"
                  % (icon, symbol, (direction or "").upper(), _num(entry), _num(exit_price),
                     reason or "-", pnl or 0.0, r_txt))

    def halt(self, reason, by="cli"):
        self.send("\U0001F6D1 ATHENA CRYPTO HALTED - all new orders blocked\n"
                  "by: %s\nreason: %s" % (by, reason or "-"))

    def resumed(self, by="cli"):
        self.send("\u25B6\uFE0F ATHENA CRYPTO resumed - trading enabled again (by %s)" % by)

    def denied(self, symbol, code, reason):
        self.send("\u26A0\uFE0F order blocked: %s (%s)\n%s" % (symbol, code, reason), silent=True)

    def startup(self, venue, mode, equity, symbols, timeframe):
        self.send("\U0001F680 ATHENA CRYPTO started\n"
                  "venue: %s\nmode: %s  |  equity $%.2f\n"
                  "symbols: %s  |  timeframe: %s"
                  % (venue, mode, equity or 0.0, ", ".join(symbols or []), timeframe))

    def daily(self, date, equity, day_pnl, realized, open_positions, trades, venue_tag):
        self.send("%s ATHENA CRYPTO daily summary\n"
                  "equity $%.2f  |  day P&L %+.2f\n"
                  "realised %+.2f  |  open %d  |  trades %d\nvenue: %s"
                  % (venue_tag, equity or 0.0, day_pnl or 0.0, realized or 0.0,
                     open_positions or 0, trades or 0, venue_tag), silent=True)


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if abs(x) >= 1000:
        return "%.1f" % x
    if abs(x) >= 1:
        return "%.4g" % x
    return "%.6g" % x


def _credentials_from_files(env_path=None, extra_candidates=None):
    """Resolve TELEGRAM_* from os.environ, else from the usual .env files.

    The systemd unit already exports these (EnvironmentFile), but a CLI run does not,
    so fall back to reading the env files directly.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat:
        return token, chat
    try:
        import os as _os
        from ..env_loader import load_env
        from ..config import PROJECT_ROOT, WORKSPACE_ROOT, DEFAULT_ENV_CANDIDATES
    except Exception:
        return token, chat
    candidates = []
    if env_path:
        candidates.append(env_path if _os.path.isabs(env_path) else _os.path.join(PROJECT_ROOT, env_path))
    candidates.extend(DEFAULT_ENV_CANDIDATES)
    candidates.extend(extra_candidates or [])
    env = load_env(None, candidates)
    return (token or env.get("TELEGRAM_BOT_TOKEN", ""),
            chat or env.get("TELEGRAM_CHAT_ID", ""))


def from_config(cfg, enabled=None, env_path=None, extra_candidates=None):
    """Build a notifier from [notify] config, the environment and the env files."""
    section = cfg.toml.get("notify", {}) if hasattr(cfg, "toml") else {}
    if enabled is None:
        enabled = bool(section.get("enabled", True))
    demo = getattr(cfg.secrets, "env", "").endswith("_test")
    tag = "DEMO" if demo else "LIVE"
    token, chat = _credentials_from_files(env_path=env_path, extra_candidates=extra_candidates)
    n = TelegramNotifier(token=token, chat_id=chat, enabled=enabled,
                         min_interval=float(section.get("min_interval", 1.0)))
    n.tag = tag
    return n
