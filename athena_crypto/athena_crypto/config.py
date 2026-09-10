"""Application configuration.

Secrets are read from the workspace .env (or ATHENA_CRYPTO/.env).
Everything else comes from config/config.toml with code defaults.
"""
import os
from dataclasses import dataclass, field

from .exchange.venue import resolve_env, VENUES
from .toml_compat import load_toml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_ROOT = os.path.dirname(PROJECT_ROOT)  # C:\PrOxyTradingTerminal
DEFAULT_ENV_CANDIDATES = [
    os.path.join(PROJECT_ROOT, ".env"),
    os.path.join(WORKSPACE_ROOT, ".env"),
    os.path.join(os.path.expanduser("~"), ".env"),
]

DEFAULT_TOML = os.path.join(PROJECT_ROOT, "config", "config.toml")

_REQUIRED_SECRETS = ("DELTA_API_KEY", "DELTA_API_SECRET")


@dataclass
class SecretConfig:
    api_key: str = ""
    api_secret: str = ""
    env: str = "india_prod"

    @property
    def has_keys(self):
        return bool(self.api_key and self.api_secret)

    @property
    def venue(self):
        return VENUES[resolve_env(self.env)]


@dataclass
class Config:
    secrets: SecretConfig
    toml: dict = field(default_factory=dict)

    # -- convenience accessors -------------------------------------------------
    @property
    def symbols(self):
        return list(self.toml.get("markets", {}).get("symbols", ["BTCUSD"]))

    @property
    def timeframe(self):
        return self.toml.get("markets", {}).get("timeframe", "15m")

    @property
    def strategy_config(self):
        return self.toml.get("strategies", {})

    @property
    def risk_config(self):
        return self.toml.get("risk", {})

    @property
    def costs_config(self):
        return self.toml.get("costs", {})

    @property
    def account_config(self):
        return self.toml.get("account", {})

    def venue(self):
        return self.secrets.venue

    def key(self, *path, default=None):
        node = self.toml
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node


def _env_override(env_dict, key):
    val = env_dict.get(key)
    if val is None:
        val = os.environ.get(key)
    return (val or "").strip()


def load_config(toml_path=None, env_path=None, env_candidates=None):
    toml_path = toml_path or DEFAULT_TOML
    toml = load_toml(toml_path)

    env_dict = {}
    from .env_loader import load_env
    env_dict = load_env(env_path, env_candidates if env_candidates is not None else DEFAULT_ENV_CANDIDATES)

    env_name = _env_override(env_dict, "DELTA_ENV") or "india_prod"
    secrets = SecretConfig(
        api_key=_env_override(env_dict, "DELTA_API_KEY"),
        api_secret=_env_override(env_dict, "DELTA_API_SECRET"),
        env=resolve_env(env_name),
    )

    equity = _env_override(env_dict, "ATHENA_EQUITY")
    if equity:
        toml.setdefault("account", {}).setdefault("paper_equity", float(equity))

    return Config(secrets=secrets, toml=toml)


def require_secrets(cfg):
    if not cfg.secrets.has_keys:
        raise RuntimeError(
            "Delta API credentials missing. Set DELTA_API_KEY / DELTA_API_SECRET in "
            + " or ".join(DEFAULT_ENV_CANDIDATES)
        )
    return cfg

