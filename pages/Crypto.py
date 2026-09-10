"""ATHENA CRYPTO - dashboard page (Delta Exchange India).

Added as a Streamlit multipage entry so it can be deployed without modifying the
tracked streamlit_app.py. Shows the action view only: equity, open positions with
live marks, the latest decision per symbol, recent fills and the kill switch.
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(page_title="ATHENA CRYPTO", layout="wide")

try:
    from proxy.crypto_data import render_crypto_page
except Exception as exc:  # never take down the dashboard
    st.error("crypto module unavailable: %s" % exc)
else:
    render_crypto_page()
