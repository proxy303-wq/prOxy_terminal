"""Strategy registry."""
from .breakout_retest import BreakoutRetest
from .range_mean_reversion import RangeMeanReversion
from .sweep_reversal import SweepReversal
from .trend_pullback import TrendPullback

_STRATEGIES = {
    "trend_pullback": TrendPullback,
    "breakout_retest": BreakoutRetest,
    "sweep_reversal": SweepReversal,
    "range_mean_reversion": RangeMeanReversion,
}


def get_enabled_strategies(config):
    """Instantiate enabled strategies from the config's strategies section."""
    out = []
    section = config.get("strategies", {}) if isinstance(config, dict) else {}
    for name, cls in _STRATEGIES.items():
        scfg = section.get(name, {}) if isinstance(section, dict) else {}
        enabled = scfg.get("enabled", True) if isinstance(scfg, dict) else True
        if enabled:
            out.append(cls(cfg=scfg))
    return out


def strategy_names():
    return list(_STRATEGIES.keys())
