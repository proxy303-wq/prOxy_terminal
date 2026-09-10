"""Venue definitions for Delta Exchange."""
from dataclasses import dataclass

VALID_ENVS = ("india_prod", "india_test", "global_prod", "global_test")


@dataclass(frozen=True)
class Venue:
    rest_base: str
    ws_url: str
    name: str


VENUES = {
    # Keys in the repo .env belong to Delta India production.
    "india_prod": Venue(
        rest_base="https://api.india.delta.exchange",
        ws_url="wss://socket.india.delta.exchange/v2",
        name="Delta India (production)",
    ),
    "india_test": Venue(
        rest_base="https://cdn-ind.testnet.deltaex.org",
        ws_url="wss://socket-ind.testnet.deltaex.org/",  # private socket (demo account)
        name="Delta India (testnet / demo account)",
    ),
    "global_prod": Venue(
        rest_base="https://api.delta.exchange",
        ws_url="wss://socket.delta.exchange/v2",
        name="Delta Global (production)",
    ),
    "global_test": Venue(
        rest_base="https://testnet-api.delta.exchange",
        ws_url="wss://socket-testnet.deltaex.org/",
        name="Delta Global (testnet)",
    ),
}


def resolve_env(name: str) -> str:
    if not name:
        name = "india_prod"
    name = name.strip().lower()
    if name not in VALID_ENVS:
        raise ValueError("Unknown venue %r; expected one of %s" % (name, ", ".join(VALID_ENVS)))
    return name

