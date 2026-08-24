import sys
sys.path.insert(0, ".")
from proxy import config as cfg
from proxy.risk import base_capital, current_equity

s_paper = {}
s_live = {"capital": 113.18}
print("paper base:", base_capital(s_paper, cfg), "| equity:", current_equity(s_paper, cfg))
print("live base:", base_capital(s_live, cfg), "| equity:", current_equity(s_live, cfg))
print("paper risk/trade:", base_capital(s_paper, cfg) * cfg.RISK_PER_TRADE_PCT)
print("live risk/trade:", base_capital(s_live, cfg) * cfg.RISK_PER_TRADE_PCT)
