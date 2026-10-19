from __future__ import annotations

from services.model_gateway import model_name, configuration_snapshot, controlled_evaluation

import hashlib
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

from evaluation.retrieval_evaluation import (
    DEFAULT_DATASET_DIR,
    _load_dataset,
    _read_json,
    _sha256,
    _write_json,
)


LABELS = (
    "SUPPORTED",
    "CONTRADICTED",
    "PARTIALLY_SUPPORTED",
    "UNKNOWN",
)

LEVELS = ("claim", "subclaim")
SCHEMA_VERSION = 1
APP_ROOT = Path(__file__).resolve().parents[1]


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _configuration(
    document_ids: list[str],
    manifest: dict,
) -> dict:
    from retrieval import embeddings, reranker

    files = {}

    required = {
        "document.json",
        "pages.json",
        "evidence.json",
        "chunks.json",
        "index.json",
    }

    for document_id in document_ids:
        if (
            not re.fullmatch(
                r"[A-Za-z0-9_.-]+",
                document_id,
            )
            or document_id in {".", ".."}
        ):
            raise ValueError(
                "Invalid document ID in the dataset."
            )

        folder = Path("data/processed") / document_id

        for name in (
            "document.json",
            "pages.json",
            "tables.json",
            "evidence.json",
            "chunks.json",
            "index.json",
            "ocr.json",
            "chart_analysis.json",
        ):
            path = folder / name

            if name in required and not path.is_file():
                raise ValueError(
                    f"Missing {path}. Process the document "
                    "before evaluation."
                )

            files[str(path)] = (
                _sha256(path)
                if path.is_file()
                else None
            )

        metadata = _read_json(
            folder / "document.json"
        )

        if metadata.get("status") != "ready":
            raise ValueError(
                f"{document_id} is not ready. "
                "Finish processing first."
            )

    code = {}

    for directory in (
        "pipelines",
        "verification",
        "reasoning",
        "retrieval",
        "services",
    ):
        for path in sorted(
            (APP_ROOT / directory).rglob("*.py")
        ):
            code[
                str(path.relative_to(APP_ROOT))
            ] = _sha256(path)

    code[
        "evaluation/verdict_evaluation.py"
    ] = _sha256(Path(__file__))

    dependencies = {}

    for package in (
        "google-genai",
        "pydantic",
        "chromadb",
        "sentence-transformers",
        "rank-bm25",
    ):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = None

    return {
        "reasoning_model": model_name(),
        "model_routing": configuration_snapshot(),
        "embedding_model": getattr(
            embeddings,
            "EMBED_MODEL",
            None,
        ),
        "embedding_dimensions": getattr(
            embeddings,
            "DIMENSIONS",
            None,
        ),
        "vector_collection": getattr(
            embeddings,
            "COLLECTION_NAME",
            None,
        ),
        "reranker_model": reranker.RERANK_MODEL,
        "reviewed_source_hashes": manifest.get(
            "source_hashes",
            {},
        ),
        "processed_file_hashes": files,
        "pipeline_code_hashes": code,
        "dependencies": dependencies,
        "cache_schema_version": SCHEMA_VERSION,
    }


def _build_queries(
    claims: list[dict],
    truths: dict[str, dict],
) -> list[dict]:
    queries = []

    for claim in claims:
        claim_id = str(claim["id"])
        truth = truths[claim_id]

        documents = (
            claim.get("document_ids") or []
        )

        if len(documents) != 1:
            raise ValueError(
                f"{claim_id}: expected one "
                "document per claim."
            )

        subclaims = truth.get("subclaims") or []

        if not subclaims:
            raise ValueError(
                f"{claim_id}: reviewed "
                "subclaims are missing."
            )

        def add(
            query_id: str,
            level: str,
            text: str,
            expected: str,
        ) -> None:
            if (
                expected not in LABELS
                or not isinstance(text, str)
                or not text.strip()
            ):
                raise ValueError(
                    f"{query_id}: invalid reviewed "
                    "claim or verdict."
                )

            queries.append(
                {
                    "query_id": query_id,
                    "claim_id": claim_id,
                    "level": level,
                    "claim_text": text,
                    "document_ids": [
                        str(documents[0])
                    ],
                    "expected": expected,
                    "predicted": None,
                    "correct": None,
                    "status": "not_run",
                    "prediction_source": None,
                    "error": None,
                    "results": [],
                    "case_tags": truth.get(
                        "case_tags",
                        [],
                    ),
                }
            )

        add(
            claim_id,
            "claim",
            claim["claim_text"],
            truth["verdict"],
        )

        if len(subclaims) > 1:
            for index, atom in enumerate(
                subclaims,
                start=1,
            ):
                add(
                    f"{claim_id}:subclaim:{index}",
                    "subclaim",
                    atom["claim_text"],
                    atom["verdict"],
                )

    return queries


def _validate_prediction(
    results: object,
) -> list[dict]:
    if not isinstance(results, list) or not results:
        raise ValueError(
            "The verification pipeline "
            "returned no result list."
        )

    for result in results:
        if (
            not isinstance(result, dict)
            or result.get("verdict") not in LABELS
        ):
            raise ValueError(
                "The pipeline returned an "
                "invalid verdict result."
            )

        parsed = (
            result.get("parsed_claim") or {}
        )

        if (
            result.get("status") != "complete"
            or result.get("error")
            or parsed.get("parsing_error")
        ):
            raise RuntimeError(
                str(
                    result.get("error")
                    or parsed.get("parsing_error")
                    or result.get("reason")
                    or "Verification failed."
                )
            )

        # Provider failures must not become semantic UNKNOWN.
        # A valid deterministic verdict with an unavailable
        # explanation remains a valid prediction.
        if result["verdict"] == "UNKNOWN":
            detail = " ".join(
                str(result.get(key) or "")
                for key in (
                    "reason",
                    "calculation_unavailable_reason",
                )
            )

            technical = re.search(
                r"resource[_ ]exhausted|"
                r"(?:^|unavailable:\s*|\bhttp\s+|"
                r"\bcode[\s:=]+|\bstatus[\s:=]+)"
                r"[45]\d{2}\b|"
                r"quota (?:exceeded|exhausted|limit)|"
                r"rate limit|timeout|"
                r"api[_ ]?key|servererror|clienterror|"
                r"connectionerror|"
                r"grounded reasoning unavailable|"
                r"claim parsing unavailable|"
                r"index is incomplete|"
                r"has no vector index|"
                r"couldn't connect",
                detail,
                re.I,
            )

            if technical:
                raise RuntimeError(detail)

    return results


def _metrics(
    queries: list[dict],
    level: str,
) -> dict:
    selected = [
        query
        for query in queries
        if query["level"] == level
    ]

    scored = [
        query
        for query in selected
        if query["status"] == "ok"
    ]

    counts = Counter(
        query["status"]
        for query in selected
    )

    matrix = {
        actual: {
            predicted: 0
            for predicted in LABELS
        }
        for actual in LABELS
    }

    for query in scored:
        matrix[
            query["expected"]
        ][
            query["predicted"]
        ] += 1

    classes = []

    for label in LABELS:
        support = sum(
            matrix[label].values()
        )

        predicted_count = sum(
            matrix[actual][label]
            for actual in LABELS
        )

        tp = matrix[label][label]
        fp = predicted_count - tp
        fn = support - tp

        active = support + predicted_count > 0

        precision = (
            tp / predicted_count
            if predicted_count
            else 0.0
        )

        recall = (
            tp / support
            if support
            else 0.0
        )

        denominator = 2 * tp + fp + fn

        f1 = (
            2 * tp / denominator
            if denominator
            else 0.0
        )

        classes.append(
            {
                "verdict": label,
                "support": support,
                "predicted_count": predicted_count,
                "precision": (
                    precision if active else None
                ),
                "recall": (
                    recall if active else None
                ),
                "f1": (
                    f1 if active else None
                ),
                "tp": tp,
                "fp": fp,
                "fn": fn,
            }
        )

    active_classes = [
        row
        for row in classes
        if row["f1"] is not None
    ]

    summary = {
        "level": level,
        "total_queries": len(selected),
        "completed_queries": len(scored),
        "failed_queries": counts["error"],
        "not_run_queries": counts["not_run"],
        "cache_hits": sum(
            query["prediction_source"] == "cache"
            for query in scored
        ),
        "coverage": (
            len(scored) / len(selected)
            if selected
            else None
        ),
        "accuracy": (
            sum(query["correct"] for query in scored)
            / len(scored)
            if scored
            else None
        ),
        "macro_class_count": len(active_classes),
    }

    for metric in ("precision", "recall", "f1"):
        summary[f"macro_{metric}"] = (
            sum(
                row[metric]
                for row in active_classes
            ) / len(active_classes)
            if active_classes
            else None
        )

        summary[f"weighted_{metric}"] = (
            sum(
                (row[metric] or 0.0) * row["support"]
                for row in classes
            ) / len(scored)
            if scored
            else None
        )

    return {
        "summary": summary,
        "per_class": classes,
        "confusion_matrix": matrix,
    }


@controlled_evaluation
def evaluate_verdicts(
    *,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    cache_mode: str = "reuse",
    stop_on_error: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """
    cache_only: score matching caches without API calls.
    reuse: reuse caches and verify missing predictions.
    fresh: verify every query again.
    """
    if cache_mode not in {
        "cache_only",
        "reuse",
        "fresh",
    }:
        raise ValueError(
            "Invalid prediction cache mode."
        )

    dataset_dir = Path(dataset_dir)

    claims, truths, manifest = _load_dataset(
        dataset_dir
    )

    queries = _build_queries(claims, truths)

    documents = sorted(
        {
            document
            for query in queries
            for document in query["document_ids"]
        }
    )

    configuration = _configuration(
        documents,
        manifest,
    )

    signature = _digest(configuration)

    cache_dir = (
        dataset_dir / "verdict_prediction_cache"
    )

    from pipelines.verification_pipeline import (
        aggregate_verdict,
        verify_claims,
    )

    written_caches = []
    started = time.perf_counter()

    for index, query in enumerate(
        queries,
        start=1,
    ):
        if progress:
            progress(
                f"Query {index}/{len(queries)} · "
                f"{query['level']} · "
                f"{query['query_id']}"
            )

        # Ground-truth labels, calculations and citations
        # are never passed to the verification pipeline.
        request = {
            "claim_text": query["claim_text"],
            "document_ids": query["document_ids"],
        }

        key = _digest(
            {
                "request": request,
                "configuration_signature": signature,
            }
        )

        path = cache_dir / f"{key}.json"
        cached = None

        if cache_mode != "fresh" and path.is_file():
            try:
                value = _read_json(path)

                matching = (
                    value.get("schema_version")
                    == SCHEMA_VERSION
                    and value.get("cache_key") == key
                    and value.get("request") == request
                    and value.get(
                        "configuration_signature"
                    ) == signature
                    and value.get("results_hash")
                    == _digest(value.get("results"))
                )

                if matching:
                    cached = _validate_prediction(
                        value.get("results")
                    )

            except (
                OSError,
                ValueError,
                TypeError,
                RuntimeError,
                KeyError,
            ):
                cached = None

        if (
            cached is None
            and cache_mode == "cache_only"
        ):
            query["error"] = (
                "No matching successful cached prediction. "
                "No API request made."
            )
            continue

        try:
            if cached is not None:
                results = cached
                query["prediction_source"] = "cache"

            else:
                results = _validate_prediction(
                    verify_claims(**request)
                )

                query["prediction_source"] = "live"

                _write_json(
                    path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "cache_key": key,
                        "configuration_signature": signature,
                        "request": request,
                        "created_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "results": results,
                        "results_hash": _digest(results),
                    },
                )

                written_caches.append(path)

            predicted = aggregate_verdict(results)

            if predicted not in LABELS:
                raise ValueError(
                    "Invalid aggregate verdict."
                )

            query.update(
                status="ok",
                predicted=predicted,
                correct=(
                    predicted == query["expected"]
                ),
                results=results,
            )

        except Exception as exc:
            query.update(
                status="error",
                error=(
                    f"{type(exc).__name__}: {exc}"
                ),
            )

            if stop_on_error:
                break

    # Reject a run if its sources or configuration changed.
    try:
        _load_dataset(dataset_dir)

        current_signature = _digest(
            _configuration(documents, manifest)
        )

        if current_signature != signature:
            raise RuntimeError(
                "Document artifacts, model configuration "
                "or pipeline code changed during evaluation."
            )

    except Exception:
        for path in written_caches:
            path.unlink(missing_ok=True)
        raise

    timestamp = datetime.now(timezone.utc)

    report_id = (
        timestamp.strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    report_path = (
        dataset_dir
        / "verdict_results"
        / f"{report_id}.json"
    )

    metrics = {
        level: _metrics(queries, level)
        for level in LEVELS
    }

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "dataset_id": manifest["dataset_id"],
        "dataset_revision": manifest.get("revision"),
        "created_at": timestamp.isoformat(),
        "report_path": str(report_path),
        "complete": all(
            query["status"] == "ok"
            for query in queries
        ),
        "elapsed_seconds": (
            time.perf_counter() - started
        ),
        "labels": list(LABELS),
        "cache_mode": cache_mode,
        "configuration_signature": signature,
        "configuration": configuration,
        "dataset_checksums": {
            name: manifest[name]
            for name in (
                "claims_sha256",
                "ground_truth_sha256",
            )
        },
        "metric_policy": {
            "errors": (
                "Operational failures and unattempted "
                "queries have no predicted label and are "
                "excluded from scores; coverage is reported."
            ),
            "macro": (
                "Equal weight across labels present in "
                "gold or predictions. Labels absent in both "
                "have null class metrics and are excluded "
                "from macro averages."
            ),
            "zero_division": (
                "For an active class, an undefined precision/"
                "recall denominator is scored as zero."
            ),
            "weighted": (
                "Per-class values weighted by ground-truth "
                "support among completed queries."
            ),
            "subclaims": (
                "Independent verification of reviewed "
                "atomic text from compound claims, not "
                "positional matching of parser outputs."
            ),
            "cache": (
                "Matches exact input, reviewed sources, "
                "processed/index metadata, configured model "
                "identifiers, pipeline code and dependency "
                "versions. Errors are not cached."
            ),
            "model_aliases": (
                "Aliases can change upstream without "
                "changing their name. Use a pinned model "
                "or force fresh predictions after an "
                "upstream model change."
            ),
        },
        "metrics": metrics,
        "summary": [
            metrics[level]["summary"]
            for level in LEVELS
        ],
        "queries": queries,
    }

    _write_json(report_path, report)

    return report