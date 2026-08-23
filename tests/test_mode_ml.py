import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg


class TestMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        import proxy.mode as mode
        self._orig = mode.MODE_FILE
        mode.MODE_FILE = self.tmp.name

    def tearDown(self):
        import proxy.mode as mode
        mode.MODE_FILE = self._orig
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_default_paper(self):
        import proxy.mode as mode
        self.assertEqual(mode.get_mode(), "paper")

    def test_set_and_get(self):
        import proxy.mode as mode
        mode.set_mode("live")
        self.assertEqual(mode.get_mode(), "live")
        self.assertTrue(mode.is_live())
        mode.set_mode("paper")
        self.assertFalse(mode.is_live())


class TestDhanBroker(unittest.TestCase):
    def test_missing_creds_raises(self):
        from proxy.dhan_broker import DhanBroker
        import os as _os
        saved = (_os.environ.get("DHAN_CLIENT_ID"), _os.environ.get("DHAN_ACCESS_TOKEN"),
                 _os.environ.get("ATHENA_ENV_FILE"))
        for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"):
            _os.environ.pop(k, None)
        _os.environ["ATHENA_ENV_FILE"] = os.path.join(tempfile.gettempdir(), "no_such_env_xyz.env")
        try:
            with self.assertRaises(RuntimeError):
                DhanBroker()
        finally:
            if saved[0] is not None:
                _os.environ["DHAN_CLIENT_ID"] = saved[0]
            if saved[1] is not None:
                _os.environ["DHAN_ACCESS_TOKEN"] = saved[1]
            if saved[2] is not None:
                _os.environ["ATHENA_ENV_FILE"] = saved[2]
            else:
                _os.environ.pop("ATHENA_ENV_FILE", None)


class TestMLModule(unittest.TestCase):
    def test_success_probability_helpers_importable(self):
        # the ML feature pipeline shares indicators; ensure it imports cleanly
        import proxy.ml_model as ml
        self.assertTrue(callable(ml.train))
        self.assertTrue(callable(ml.load))
        self.assertTrue(callable(ml.build_features))

    def test_sequence_maker(self):
        import numpy as np
        import pandas as pd
        from proxy.ml_model import make_sequences
        feats = pd.DataFrame(np.zeros((40, 3)))
        labels = pd.Series([0, 1] * 20)
        X, y = make_sequences(feats, labels, seq_len=10)
        self.assertEqual(X.shape[0], 29)
        self.assertEqual(X.shape[1], 10)
        self.assertEqual(X.shape[2], 3)


if __name__ == "__main__":
    unittest.main()
