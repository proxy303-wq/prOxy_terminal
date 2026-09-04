"""Item-7 supplementary: per-trade datasets for the strike-once OFF variants
(needed for trades/day and consecutive-loss comparisons)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import REPORT_DIR, months, base_config, _data, run_pool, TRAIN, TEST
from proxy.backtest import Backtest, load_csv


def worker(task):
    idx, win_spec, label = task
    c = base_config(idx, dict(ONE_TRADE_PER_STRIKE_DAY=False))
    c.BT_TRADE_DATASET = True
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    df5p, df1p = _data(idx)
    df5 = load_csv(df5p)
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    df1 = load_csv(df1p)
    bt = Backtest(c, df=df5[keep], df1m=df1, verbose=False)
    r = bt.run()
    bt.save_report(r, name=os.path.join("v41", label))
    return label, r


def main():
    tasks = [
        ("NIFTY test strike-OFF", ("NIFTY", TEST, "dataset_NIFTY_test_strikeoff")),
        ("BN test strike-OFF", ("BN", TEST, "dataset_BN_test_strikeoff")),
        ("BN train strike-OFF", ("BN", TRAIN, "dataset_BN_train_strikeoff")),
    ]
    results = run_pool(tasks, fn=worker)
    for label, r in results.items():
        print(label, r["trades"], r["net_pnl"])


if __name__ == "__main__":
    main()
