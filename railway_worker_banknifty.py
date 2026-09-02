"""PrOxy Trading Terminal - BANKNIFTY second engine (dual worker).

Runs the same strategy on BANKNIFTY options with its own geometry
(lot 35, strike 100), its own tracker DB and Telegram-tagged messages:

    python railway_worker_banknifty.py            # paper/live per Telegram mode
    python railway_worker_banknifty.py --help

Deploy: a second systemd unit with PROXY_ALLOCATION_PCT (e.g. 0.5) so the
two engines share the Dhan balance.
"""

from railway_worker import main

if __name__ == "__main__":
    main("banknifty")
