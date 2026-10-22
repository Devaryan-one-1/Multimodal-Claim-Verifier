"""Four-variant study with isolated caches and matched-claim metrics."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from evaluation.retrieval_evaluation import (
    DEFAULT_DATASET_DIR, _Checkpoints, _InputGuard, _configuration as _retrieval_configuration,
    _load_dataset, _read_json, _sha256, _write_json,
)
from pipelines.ablation_pipeline import verify_ablation_claim
from retrieval.ablation_search import AblationCorpus, VARIANTS
from services.model_gateway import capture_calls, configuration_snapshot, controlled_evaluation

LABELS = ("SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED", "UNKNOWN")
SCHEMA_VERSION = 1
APP_ROOT = Path(__file__).resolve().parents[1]
OVERRIDE_FILENAME = "ablation_modality_overrides.json"


class CacheMiss(RuntimeError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _documents(claims):
    output = []
    for claim in claims:
        documents = claim.get("document_ids") or []
        if len(documents) != 1:
            raise ValueError(f"{claim.get('id')}: each reviewed claim must name one source document")
        doc = str(documents[0])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", doc) or doc in {".", ".."}:
            raise ValueError("Invalid document ID")
        output.append(doc)
    return sorted(set(output))


def _overrides(dataset_dir):
    path = Path(dataset_dir) / OVERRIDE_FILENAME
    value = _read_json(path) if path.is_file() else {}
    if not isinstance(value, dict) or any(not isinstance(key, str) or item not in
                                         {"text", "table", "chart", "exclude"}
                                         for key, item in value.items()):
        raise ValueError("Content overrides must map document_id::source_id to text, table, chart or exclude")
    return value


def inspect_ablation_sources(dataset_dir=DEFAULT_DATASET_DIR):
    """Read source files only. No index/model/query calls."""
    claims, _, _ = _load_dataset(Path(dataset_dir))
    corpus = AblationCorpus(_documents(claims), _overrides(dataset_dir))
    unknown = set(corpus.overrides) - {item["override_key"] for item in corpus.inventory}
    if unknown:
        raise ValueError("Unknown override keys: " + ", ".join(sorted(unknown)[:5]))
    return {"coverage": corpus.coverage(), "inventory": corpus.inventory}


def save_modality_overrides(value, dataset_dir=DEFAULT_DATASET_DIR):
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    claims, _, _ = _load_dataset(Path(dataset_dir))
    corpus = AblationCorpus(_documents(claims), value)
    unknown = set(value) - {item["override_key"] for item in corpus.inventory}
    if unknown:
        raise ValueError("Unknown override keys: " + ", ".join(sorted(unknown)[:5]))
    _write_json(Path(dataset_dir) / OVERRIDE_FILENAME, value)


def _metrics(rows):
    scored = [row for row in rows if row["status"] == "ok"]
    counts = Counter(row["status"] for row in rows)
    matrix = {gold: {predicted: 0 for predicted in LABELS} for gold in LABELS}
    for row in scored:
        matrix[row["expected"]][row["predicted"]] += 1
    classes = []
    for label in LABELS:
        support = sum(matrix[label].values())
        predicted = sum(matrix[gold][label] for gold in LABELS)
        tp = matrix[label][label]
        fp, fn = predicted - tp, support - tp
        classes.append({"verdict": label, "support": support, "predicted_count": predicted,
                        "precision": tp / predicted if predicted else 0.0 if scored else None,
                        "recall": tp / support if support else 0.0 if scored else None,
                        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0 if scored else None})
    summary = {"total_claims": len(rows), "completed": len(scored), "errors": counts["error"],
               "not_run": counts["not_run"], "coverage": len(scored) / len(rows) if rows else None,
               "accuracy": sum(row["correct"] for row in scored) / len(scored) if scored else None}
    for metric in ("precision", "recall", "f1"):
        summary["macro_" + metric] = sum(row[metric] for row in classes) / len(LABELS) if scored else None
        summary["weighted_" + metric] = sum(row[metric] * row["support"] for row in classes) / len(scored) if scored else None
    return {"summary": summary, "per_class": classes, "confusion_matrix": matrix}


def _comparison(report):
    rows = report["queries"]
    maps = {variant: {row["claim_id"]: row for row in rows if row["variant"] == variant}
            for variant in VARIANTS}
    common = set.intersection(*[{cid for cid, row in maps[variant].items() if row["status"] == "ok"}
                                for variant in VARIANTS])
    report["metrics"] = {variant: _metrics(list(maps[variant].values())) for variant in VARIANTS}
    report["paired_metrics"] = {variant: _metrics([maps[variant][cid] for cid in sorted(common)])
                                for variant in VARIANTS}
    report["paired_claim_ids"] = sorted(common)
    report["summary"] = [dict(variant=variant, name=VARIANTS[variant]["name"],
                              **report["metrics"][variant]["summary"]) for variant in VARIANTS]
    report["paired_summary"] = [dict(variant=variant, name=VARIANTS[variant]["name"],
                                     **report["paired_metrics"][variant]["summary"]) for variant in VARIANTS]
    report["deltas"] = []
    for before, after in (("A", "B"), ("B", "C"), ("C", "D")):
        gained = sum(not maps[before][cid]["correct"] and maps[after][cid]["correct"] for cid in common)
        lost = sum(maps[before][cid]["correct"] and not maps[after][cid]["correct"] for cid in common)
        report["deltas"].append({"comparison": f"{before} → {after}", "matched_claims": len(common),
                                 "improved": gained, "worsened": lost,
                                 "accuracy_change_percentage_points": 100 * (gained - lost) / len(common) if common else None})


@controlled_evaluation
def evaluate_ablation(*, dataset_dir=DEFAULT_DATASET_DIR, variants=("A", "B", "C", "D"),
                      cache_mode="cache_only", stop_on_error=True, candidate_depth=20,
                      rerank_keep=10, progress=None):
    """Only explicit reuse/fresh runs may call embedding or reasoning APIs."""
    if cache_mode not in {"cache_only", "reuse", "fresh"}:
        raise ValueError("Invalid cache mode")
    if not variants or any(v not in VARIANTS for v in variants):
        raise ValueError("Choose at least one of A, B, C and D")
    if (isinstance(candidate_depth, bool) or not isinstance(candidate_depth, int) or candidate_depth < 1
            or isinstance(rerank_keep, bool) or not isinstance(rerank_keep, int)
            or not 1 <= rerank_keep <= candidate_depth):
        raise ValueError("Retrieval depths must be positive integers; keep cannot exceed candidate depth")
    dataset_dir = Path(dataset_dir)
    claims, truths, manifest = _load_dataset(dataset_dir)
    documents = _documents(claims)
    overrides = _overrides(dataset_dir)
    corpus = AblationCorpus(documents, overrides)
    unknown = set(overrides) - {item["override_key"] for item in corpus.inventory}
    if unknown:
        raise ValueError("Unknown content override keys; inspect the source inventory first")
    chunk_maps, chunk_hashes = {}, {}
    for doc in documents:
        path = Path("data/processed") / doc / "chunks.json"
        chunks = _read_json(path)["chunks"]
        chunk_maps[doc] = {item["id"]: item for item in chunks}
        chunk_hashes[str(path)] = _sha256(path)
    # Same local content-hash/index checks as retrieval evaluation. No source
    # re-extraction and no dashboard 'ready' metadata rewrite is required.
    initial, _, _, hashes = _retrieval_configuration(chunk_maps, chunk_hashes, manifest, candidate_depth, False)
    for doc in documents:
        for name in ("pages.json", "evidence.json"):
            path = Path("data/processed") / doc / name
            hashes[str(path)] = _sha256(path)
    for directory in ("pipelines", "verification", "reasoning", "retrieval", "services"):
        for path in sorted((APP_ROOT / directory).rglob("*.py")):
            hashes[str(path)] = _sha256(path)
    hashes[str(Path(__file__).resolve())] = _sha256(Path(__file__))
    source_hashes = dict(hashes)
    for name in ("claims.jsonl", "ground_truth.jsonl", "manifest.json", OVERRIDE_FILENAME):
        path = dataset_dir / name
        if path.is_file():
            hashes[str(path)] = _sha256(path)
    versions = {}
    for package in ("google-genai", "pydantic", "httpx", "jsonschema", "chromadb",
                    "sentence-transformers", "transformers", "torch", "rank-bm25"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    configuration = {"schema_version": SCHEMA_VERSION, "model_routing": configuration_snapshot(),
                     "retrieval": initial, "candidate_depth": candidate_depth, "rerank_keep": rerank_keep,
                     "source_and_code_hashes": source_hashes, "dependencies": versions,
                     "overrides": overrides, "inventory_sha256": _digest(corpus.inventory),
                     "variants": VARIANTS, "shared_parser": True, "arithmetic_policy": "LLM unverified for A-C; Python for D",
                     "context_policy": "Own evidence content only; whole-page context removed in every variant",
                     "ocr_policy": "Classify by content; unclassified OCR excluded and inventoried"}
    # Labels and gold citations are scoring inputs only; they are absent from
    # model requests and successful stage cache identities.
    signature = _digest(configuration)
    guard = _InputGuard(hashes)
    checkpoints = _Checkpoints(dataset_dir / "ablation_cache", cache_mode != "fresh", progress)
    queries = []
    for claim in claims:
        expected = truths[str(claim["id"])]["verdict"]
        if expected not in LABELS:
            raise ValueError("Invalid reviewed ground-truth label")
        for variant in VARIANTS:
            queries.append({"claim_id": str(claim["id"]), "variant": variant,
                            "claim_text": claim["claim_text"], "document_id": str(claim["document_ids"][0]),
                            "expected": expected, "predicted": None, "correct": None,
                            "status": "not_run", "error": None, "prediction_source": None,
                            "case_tags": truths[str(claim["id"])].get("case_tags", []), "results": []})
    timestamp = datetime.now(timezone.utc)
    report_id = timestamp.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    path = dataset_dir / "ablation_results" / f"{report_id}.json"
    report = {"schema_version": SCHEMA_VERSION, "report_id": report_id,
              "created_at": timestamp.isoformat(), "report_path": str(path),
              "dataset_id": manifest["dataset_id"], "dataset_revision": manifest.get("revision"),
              "dataset_checksums": {key: manifest[key] for key in ("claims_sha256", "ground_truth_sha256")},
              "configuration": configuration, "configuration_signature": signature,
              "cache_mode": cache_mode, "selected_variants": list(variants), "complete": False,
              "queries": queries, "source_coverage": corpus.coverage(), "source_inventory": corpus.inventory,
              "metric_policy": {"scope": "All exported complete claims, including compound claims; no separate subclaim experiment",
                                "errors": "API/configuration/processing failures have no label and are excluded; coverage always shown",
                                "macro": "Fixed four labels; undefined class denominators score zero; no completed predictions yields null metrics",
                                "comparison": "Compare only the identical claims completed by all four variants",
                                "interpretation": "D changes arithmetic and its verification safeguard; C-to-D measures that combined contribution",
                                "repeats": "One cached prediction per variant/input/configuration; not a multi-seed significance study"}}
    started = time.perf_counter()
    with capture_calls() as (calls, call_start):
        def save():
            _comparison(report)
            report["complete"] = all(row["status"] == "ok" for row in queries)
            report["updated_at"] = datetime.now(timezone.utc).isoformat()
            report["elapsed_seconds"] = time.perf_counter() - started
            report["checkpoint_counts"] = dict(checkpoints.counts)
            report["model_calls"] = copy.deepcopy(calls[call_start:])
            _write_json(path, report)

        def memo(stage, request, compute):
            guard.check()
            config = {"ablation_signature": signature}
            payload, source = checkpoints.get(stage, request, config)
            if payload is not None:
                return payload["value"]
            if cache_mode == "cache_only":
                raise CacheMiss(f"No matching {stage} checkpoint; no API request made")
            with capture_calls() as (stage_calls, stage_start):
                value = compute()
                metadata = copy.deepcopy(stage_calls[stage_start:])
            guard.check()
            checkpoints.put(stage, request, config, {"value": value, "model_calls": metadata})
            save()
            return value

        save()
        for index, row in enumerate(queries, 1):
            if row["variant"] not in variants:
                continue
            if progress:
                progress(f"{index}/{len(queries)} · {row['claim_id']} · Variant {row['variant']}")
            try:
                request = {"claim_text": row["claim_text"], "document_id": row["document_id"], "variant": row["variant"]}
                before = dict(checkpoints.counts)
                prediction = memo("prediction", request, lambda: verify_ablation_claim(
                    row["claim_text"], row["document_id"], row["variant"], corpus, memo,
                    candidate_depth=candidate_depth, rerank_keep=rerank_keep))
                if prediction["verdict"] not in LABELS or not prediction.get("results"):
                    raise ValueError("Invalid complete-claim prediction")
                computed = checkpoints.counts.get("prediction_computed", 0) > before.get("prediction_computed", 0)
                row.update(status="ok", predicted=prediction["verdict"],
                           correct=prediction["verdict"] == row["expected"], results=prediction["results"],
                           prediction_source="computed" if computed else "cache")
            except CacheMiss as exc:
                row["error"] = str(exc)
            except Exception as exc:
                row.update(status="error", error=f"{type(exc).__name__}: {exc}")
                save()
                if stop_on_error:
                    break
            save()
        try:
            guard.check(full=True)
            current_initial, _, _, _ = _retrieval_configuration(chunk_maps, chunk_hashes, manifest, candidate_depth, False)
            if _digest(current_initial) != _digest(initial):
                raise RuntimeError("Index configuration or vector metadata changed during the study")
        except Exception as exc:
            checkpoints.discard_new()
            report["abort_reason"] = str(exc)
            for row in queries:
                row.update(status="error", predicted=None, correct=None, results=[],
                           error="Scores invalidated: " + str(exc))
        save()
    return report
