"""Integration smoke tests: optsell telegram mode toggle + worker warm-up."""
import datetime
import os
import tempfile
import unittest

from proxy import mode as _mode
from proxy.config import REPORT_DIR
from proxy.options_selling_config import options_selling_config

MODE_FILE = os.path.join(REPORT_DIR, "mode_optsell.json")


class TestTelegramModeToggle(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(MODE_FILE)
        except OSError:
            pass

    def tearDown(self):
        try:
            os.remove(MODE_FILE)
        except OSError:
            pass

    def test_paper_toggle_writes_variant_mode_file(self):
        from proxy.telegram_menu import TelegramMenu
        m = TelegramMenu()
        m._set_opt_mode("123", "paper")
        self.assertEqual(_mode.get_mode("optsell"), "paper")
        self.assertTrue(os.path.exists(MODE_FILE))
        m._set_opt_mode("123", "paper")   # idempotent

    def test_confirm_flow_arms_pending(self):
        from proxy.telegram_menu import TelegramMenu
        m = TelegramMenu()
        self.assertNotIn("42", m._pending_opt)
        m._opt_ask_live("42")
        self.assertIn("42", m._pending_opt)


class TestWorkerWarm(unittest.TestCase):
    def test_warm_history_populates_closes(self):
        import proxy.options_selling_worker as wkr
        from proxy.options_selling import OptionsSellingEngine
        cfg = options_selling_config()
        cfg.DB_PATH = tempfile.mktemp(suffix=".sqlite")
        cfg.CSV_PATH = os.path.join(REPORT_DIR, "..", "data", "NIFTY_5m.csv")
        eng = OptionsSellingEngine(cfg=cfg, db_path=cfg.DB_PATH,
                                   notify=lambda *a: None)
        fed = wkr.warm_history(eng, cfg, days_back=3, notify=lambda *a: None)
        self.assertGreater(fed, 0)
        self.assertGreater(len(eng.history), 30)


if __name__ == "__main__":
    unittest.main()
