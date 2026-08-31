"""Render the ML Lab results into a readable text report."""
import json
import os

from .config import REPORT_DIR, HORIZONS

LABELS = {"h1": "5 min", "h3": "15 min", "h6": "30 min", "h12": "60 min"}
MODEL_NAMES = {"lgbm": "LightGBM", "xgb": "XGBoost", "mlp": "MLP (sklearn)",
               "gru": "GRU (TensorFlow)", "ensemble": "Ensemble (LGBM+XGB)"}


def _fmt_metrics(m):
    if not m:
        return "n/a"
    return (f"acc {m.get('accuracy')}% (majority {m.get('majority')}%) | "
            f"AUC {m.get('auc')} | bal_acc {m.get('balanced_acc')}% | "
            f"conf@{m.get('conf_acc')}% (rate {m.get('conf_signal_rate')}%)")


def build_report(report_path=None, out_path=None):
    report_path = report_path or os.path.join(REPORT_DIR, "ml_lab_report.json")
    out_path = out_path or os.path.join(REPORT_DIR, "ml_lab_report.txt")
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)

    lines = []
    lines.append("=" * 78)
    lines.append("PrOxy ML Lab - NIFTY 50 & BANKNIFTY movement prediction report")
    lines.append("=" * 78)
    lines.append("Method: 5-fold expanding-window walk-forward, strictly out-of-sample.")
    lines.append("Significance: permutation test against the majority-class null model.")
    lines.append("")

    best_by_symbol = {}
    for symbol in ("nifty", "banknifty"):
        if symbol not in report:
            continue
        lines.append(f"--- {symbol.upper()} ---")
        sym_best = None
        for hz in ("h1", "h3", "h6", "h12"):
            if hz not in report[symbol]:
                continue
            block = report[symbol][hz]
            lines.append(f"  {LABELS[hz]:7s} horizon (bars_ahead={HORIZONS[hz]['bars']}): "
                         f"best = {block.get('best_model')}")
            for mname, mres in block.get("results", {}).items():
                met = mres.get("metrics", {})
                perm = mres.get("permutation", {})
                strat = mres.get("strategy", {})
                lines.append(
                    f"      {MODEL_NAMES.get(mname, mname):18s} "
                    f"{_fmt_metrics(met)} | perm-p {perm.get('perm_p_value')} | "
                    f"conf-signal hit {strat.get('hit_rate')}% ({strat.get('n_signals')} trades)")
            best = block.get("best_model")
            score = block.get("results", {}).get(best, {}).get("metrics", {}).get("accuracy")
            if sym_best is None or (score or 0) > sym_best[1]:
                sym_best = (hz, score)
            lines.append("")
        if sym_best:
            best_by_symbol[symbol] = sym_best
            lines.append(f"  >>> {symbol.upper()} most accurate horizon: {LABELS[sym_best[0]]} "
                         f"({sym_best[1]}% OOS accuracy)")
        lines.append("")

    lines.append("=" * 78)
    lines.append("Deployed artifacts (models/ml_lab/):")
    for symbol in ("nifty", "banknifty"):
        if symbol not in report:
            continue
        for hz in ("h1", "h3", "h6", "h12"):
            block = report[symbol].get(hz)
            if not block:
                continue
            for s in block.get("saved", []):
                lines.append(f"  {s['artifact']}")
    lines.append("")

    text = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return out_path


if __name__ == "__main__":
    print(build_report())