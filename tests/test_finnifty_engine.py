"""FINNIFTY third-engine profile tests (HANDOVER 17 scout PASS -> engine wiring).

The scout proved the pre-spread edge (test PF 2.77); this profile must be a
COMPLETE self-contained live profile (like banknifty_config) - never inherit
the box's config.py which can carry paper data-mode knobs (NO_STOP_LOSS=True
etc.) - and carry the REAL Dhan contract facts from the 06-Sep scrip master
(lot 60, monthly listings, index id 27, FNO underlying 26037).
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy.config as base
from proxy.dual import finnifty_config, banknifty_config, variant_config


def main():
    ok = True
    c = finnifty_config()

    # 1) self-contained LIVE discipline regardless of base config state
    print("base config (repo HEAD) carries data-mode knobs:",
          "NO_STOP_LOSS", base.NO_STOP_LOSS, "conf", base.MIN_CONFIDENCE_PCT)
    assert c.NO_STOP_LOSS is False, "FINNIFTY must never inherit NO_STOP_LOSS=True"
    ok &= c.NO_STOP_LOSS is False
    assert c.MIN_CONFIDENCE_PCT >= 60.0, "conf gate must be the LIVE 65"
    assert c.MAX_UNARMED_BARS >= 4
    assert c.RSI_ENTRY_GATE_BULL == 50.0 and c.RSI_ENTRY_GATE_BEAR == 50.0
    assert c.ML_LAB_ENABLED is False and c.ML_ENABLED is False and c.META_ENABLED is False
    assert c.SL_MODE == "points"
    assert c.REVERSE_EXIT_DELAY_BARS == 1          # V4 policy
    print("PASS live discipline (stops on, conf 65, unarmed 4, RSI 50/50, V4)")

    # 2) REAL Dhan contract facts (scrip master 06-Sep)
    assert c.LOT_SIZE == 60, f"real Dhan FINNIFTY lot is 60, got {c.LOT_SIZE}"
    assert c.OPTION_SYMBOL == "FINNIFTY"
    assert c.INDEX_ID == 27
    assert getattr(c, "FNO_UNDERLYING_ID", None) == 26037
    assert c.OPTION_STRIKE_STEP == 50.0
    print("PASS real contract facts (lot 60, idx 27, FNO 26037, step 50)")

    # 3) own DB + dashboard (never writes into the NIFTY DB)
    assert "finnifty" in os.path.basename(c.DB_PATH)
    assert "finnifty" in os.path.basename(c.DASHBOARD_HTML)
    assert c.DB_PATH != base.DB_PATH
    print("PASS own DB + dashboard:", os.path.basename(c.DB_PATH))

    # 4) variant dispatch resolves it
    vc = variant_config("finnifty")
    assert vc.OPTION_SYMBOL == "FINNIFTY"
    print("PASS variant_config(finnifty) resolves")

    # 5) mode file semantics: absent -> paper (never live by accident)
    from proxy.mode import mode_file_for, get_mode
    mf = mode_file_for("finnifty")
    assert "mode_finnifty.json" in mf, mf
    print("mode file:", os.path.basename(mf), "-> current:", get_mode("finnifty"))

    print("ALL FINNIFTY PROFILE TESTS PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
