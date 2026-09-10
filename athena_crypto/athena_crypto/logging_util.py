"""Logging helpers - single file logger plus console."""
import logging
import os
import sys

_CONFIGURED = False


def get_logger(name="athena", level=None, logfile=None):
    global _CONFIGURED
    if _CONFIGURED:
        return logging.getLogger(name)
    _CONFIGURED = True
    logger = logging.getLogger(name)
    logger.setLevel(level or os.environ.get("ATHENA_LOG_LEVEL", "INFO").upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if logfile:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(logfile)), exist_ok=True)
            fh = logging.FileHandler(logfile, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            pass
    return logger

