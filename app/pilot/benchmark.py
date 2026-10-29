"""Score human-labelled held-out predictions; no model calls or invented labels."""
from __future__ import annotations
import argparse
import json
import math
import statistics
from pathlib import Path

LABELS = ("SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED", "UNKNOWN")


def score(rows):
    valid = []
    for row in rows:
        if row.get("status") != "complete":
            continue
        if row.get("gold") not in LABELS or row.get("predicted") not in LABELS:
            raise ValueError("Completed rows require valid gold and predicted labels.")
        valid.append(row)
    n = len(valid)
    matrix = {g: {p: 0 for p in LABELS} for g in LABELS}
    for r in valid:
        matrix[r["gold"]][r["predicted"]] += 1
    per_class = {}
    for label in LABELS:
        tp = matrix[label][label]
        actual = sum(matrix[label].values())
        predicted = sum(matrix[g][label] for g in LABELS)
        precision = tp/predicted if predicted else 0
        recall = tp/actual if actual else 0
        per_class[label] = {"precision": precision, "recall": recall, "f1": 2*precision*recall/(precision+recall) if precision+recall else 0, "support": actual}
    supported = [r for r in valid if r["predicted"] == "SUPPORTED"]
    non_supported_gold = [r for r in valid if r["gold"] != "SUPPORTED"]
    false_supported = sum(r["gold"] != "SUPPORTED" for r in supported)
    def measurements(field):
        values = [r[field] for r in rows if r.get(field) is not None]
        if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError(f"Invalid {field} measurement.")
        return sorted(values)
    latencies, costs = measurements("latency_seconds"), measurements("cost_usd")
    return {"total": len(rows), "completed": n, "failed_or_unattempted": len(rows)-n,
            "completion_rate": n/len(rows) if rows else None,
            "accuracy": sum(r["gold"]==r["predicted"] for r in valid)/n if n else None,
            "per_class": per_class, "macro_f1_all_four_classes": sum(v["f1"] for v in per_class.values())/4 if n else None,
            "false_supported_count": false_supported,
            "false_support_rate_among_non_supported_gold": false_supported/len(non_supported_gold) if non_supported_gold else None,
            "false_discovery_rate_among_supported_predictions": false_supported/len(supported) if supported else None,
            "abstention_rate": sum(r["predicted"]=="UNKNOWN" for r in valid)/n if n else None,
            "latency_measured_count": len(latencies), "median_latency_seconds": statistics.median(latencies) if latencies else None,
            "p95_latency_seconds_nearest_rank": latencies[max(0,math.ceil(.95*len(latencies))-1)] if latencies else None,
            "cost_measured_count": len(costs), "observed_cost_usd": sum(costs) if costs else None,
            "cost_complete": bool(rows) and len(costs)==len(rows), "confusion_matrix": matrix}


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("predictions_jsonl")
    args=parser.parse_args()
    rows=[json.loads(line) for line in Path(args.predictions_jsonl).read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps(score(rows),indent=2))
