"""Remote helper: set LOCK_ARM_POINTS = 1.0 in /opt/proxy/proxy/config.py."""
import re

P = "/opt/proxy/proxy/config.py"
src = open(P, encoding="utf-8").read()
new = re.sub(r"(?m)^LOCK_ARM_POINTS\s*=.*$",
             "LOCK_ARM_POINTS = 1.0              # A/B 03-Sep (V4 policy): arm 2.0 -> 1.0 - train net +244.6k->+322.9k (PF 1.66), test PF 2.72, protects profit from +1pt",
             src, count=1)
assert new != src, "LOCK_ARM_POINTS line not found/replaced"
open(P, "w", encoding="utf-8").write(new)
print("LOCK_ARM_POINTS -> 1.0 OK")
