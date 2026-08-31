"""Rebuild the aggregated ML Lab report from saved model metadata.

Each models/ml_lab/<symbol>_<horizon>_<model>_meta.json carries the full
walk-forward OOS results, so the report can be reconstructed even if the
original run's summary JSON was overwritten.
"""
import glob
import json
import os

from .config import MODELS_DIR, REPORT_DIR


def rebuild(out_path=None):
    out_path = out_path or os.path.join(REPORT_DIR, "ml_lab_report.json")
    report = {}
    for meta_path in sorted(glob.glob(os.path.join(MODELS_DIR, "*_meta.json"))):
        base = os.path.basename(meta_path)
        parts = base.split("_")
        # <symbol>_<horizon>_<model>_meta.json  (symbols/horizons have no _)
        if len(parts) < 4:
            continue
        symbol, horizon, model = parts[0], parts[1], "_".join(parts[2:-1])
        if symbol not in ("nifty", "banknifty"):
            continue
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        block = report.setdefault(symbol, {}).setdefault(horizon, {
            "results": {}, "saved": [], "majority_baseline": meta.get("majority_baseline")})
        oos = meta.get("oos", {})
        block["results"][model] = {
            "metrics": oos.get("metrics", {}),
            "permutation": oos.get("permutation", {}),
            "strategy": oos.get("strategy", {}),
            "per_fold": oos.get("per_fold", {}),
            "folds_used": oos.get("folds_used"),
            "oov_n": oos.get("oov_n"),
        }
        block["saved"].append({"model": model, "artifact": meta.get("artifact")})
    # pick best per horizon by OOS accuracy
    for symbol, blocks in report.items():
        for horizon, block in blocks.items():
            res = block.get("results", {})
            if res:
                best = max(res.items(), key=lambda kv: (kv[1].get("metrics", {}).get("accuracy") or 0))
                block["best_model"] = best[0]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return out_path


if __name__ == "__main__":
    print(rebuild())
