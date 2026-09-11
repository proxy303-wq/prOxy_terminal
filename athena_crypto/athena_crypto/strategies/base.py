"""Strategy base types and the signal contract.

Strategies are deterministic rules over MarketState snapshots. They output a
candidate Signal (entry/stop/target logic). They never size positions - the
risk engine owns sizing and can approve, modify or reject every signal.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    symbol: str
    direction: str            # 'long' | 'short'
    setup_type: str           # strategy family name, e.g. 'trend_pullback'
    entry_type: str = "market"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    confidence: float = 0.0   # 0..1 internal strategy conviction, NOT a risk override
    reason: str = ""
    meta: dict = field(default_factory=dict)
    params_version: str = "v1"
    bar_time: Optional[int] = None


class Strategy:
    name = "base"

    def __init__(self, cfg: Optional[dict] = None, enabled: bool = True):
        self.cfg = cfg or {}
        self.enabled = bool(enabled)

    def allows(self, symbol: str) -> bool:
        """Per-symbol scope from config.

        `symbols` is an allow-list (empty/absent = every symbol);
        `exclude_symbols` is a deny-list applied after it. This is how two
        strategies coexist on one venue without fighting over the same symbol.
        """
        allow = self.cfg.get("symbols")
        deny = self.cfg.get("exclude_symbols")
        if allow and symbol not in allow:
            return False
        if deny and symbol in deny:
            return False
        return True

    def evaluate(self, mstate: dict, regime: dict) -> Optional[Signal]:
        raise NotImplementedError
