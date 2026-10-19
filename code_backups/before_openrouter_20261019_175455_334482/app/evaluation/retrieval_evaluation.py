from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv


DEFAULT_DATASET_DIR = Path("data/evaluation")
PROCESSED_ROOT = Path("data/processed")

STAGES = ("initial", "reranked")
LEVELS = ("claim", "subclaim")
MRR_DEPTH = 20
METRIC_NAMES = ("Recall@5", "Recall@10", "Hit@5", "Hit@10", "MRR@20")
REPORT_SCHEMA_VERSION = 2


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    records = []

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path.name}, line {line_number}: invalid JSON."
            ) from exc

        if not isinstance(record, dict):
            raise ValueError(
                f"{path.name}, line {line_number}: "
                "expected an object."
            )

        records.append(record)

    return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )

    temporary = path.with_name(
        f".{path.name}.{uuid.uuid4().hex}.tmp"
    )

    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_dataset(
    dataset_dir: Path,
) -> tuple[list[dict], dict[str, dict], dict]:
    paths = {
        "claims": dataset_dir / "claims.jsonl",
        "ground_truth": dataset_dir / "ground_truth.jsonl",
        "manifest": dataset_dir / "manifest.json",
    }

    for path in paths.values():
        if not path.exists():
            raise ValueError(
                f"Missing {path}. Approve and export "
                "the evaluation dataset first."
            )

    manifest = _read_json(paths["manifest"])

    for name in ("claims", "ground_truth"):
        expected = manifest.get(f"{name}_sha256")

        if not expected:
            raise ValueError(
                f"Manifest has no checksum for {name}."
            )

        if _sha256(paths[name]) != expected:
            raise ValueError(
                f"{paths[name].name} differs from its manifest. "
                "Export the reviewed dataset again."
            )

    for filename, expected in manifest.get(
        "source_hashes", {}
    ).items():
        path = Path(filename)

        if not path.exists() or _sha256(path) != expected:
            raise ValueError(
                f"Dataset source changed or disappeared: {filename}. "
                "Use a dataset reviewed against the current sources."
            )

    claims = _read_jsonl(paths["claims"])
    truths = _read_jsonl(paths["ground_truth"])

    if not claims:
        raise ValueError("The exported dataset is empty.")

    claim_ids = [str(item["id"]) for item in claims]
    truth_ids = [str(item["id"]) for item in truths]

    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("Duplicate claim IDs.")

    if len(set(truth_ids)) != len(truth_ids):
        raise ValueError("Duplicate ground-truth IDs.")

    if set(claim_ids) != set(truth_ids):
        raise ValueError(
            "Claims and ground truth have different IDs."
        )

    if len(claims) != manifest.get("claim_count"):
        raise ValueError(
            "Claim count differs from the manifest."
        )

    for truth in truths:
        if truth.get("review_status") != "approved":
            raise ValueError(
                "Ground truth contains an unapproved record."
            )

    return (
        claims,
        {str(item["id"]): item for item in truths},
        manifest,
    )


def _build_queries(
    claims: list[dict],
    truths: dict[str, dict],
) -> list[dict]:
    queries = []

    for claim in claims:
        claim_id = str(claim["id"])
        truth = truths[claim_id]

        document_ids = claim.get("document_ids") or []

        if len(document_ids) != 1:
            raise ValueError(
                f"{claim_id}: this evaluator expects one "
                "document per claim, matching the dataset builder."
            )

        document_id = str(document_ids[0])
        subclaims = truth.get("subclaims") or []

        if not subclaims:
            raise ValueError(
                f"{claim_id}: ground-truth subclaims are missing."
            )

        citation_map = {
            item["citation_id"]: item
            for item in truth.get("citations", [])
        }

        def gold_for(subclaim: dict) -> set[str]:
            if subclaim["verdict"] == "UNKNOWN":
                # Context for an unresolved assertion is not
                # positive gold evidence for that assertion.
                return set()

            gold = set()

            for citation_id in subclaim.get("evidence_ids", []):
                citation = citation_map.get(citation_id)

                if citation is None:
                    raise ValueError(
                        f"{claim_id}: citation metadata is missing "
                        f"for {citation_id}."
                    )

                if citation["document_id"] != document_id:
                    raise ValueError(
                        f"{claim_id}: citation document mismatch."
                    )

                source_id = citation.get("evidence_id")

                if not source_id:
                    raise ValueError(
                        f"{claim_id}: citation has no original "
                        "evidence ID."
                    )

                gold.add(str(source_id))

            return gold

        atomic_gold = [
            gold_for(subclaim)
            for subclaim in subclaims
        ]

        complete_gold = set().union(*atomic_gold)

        def add_query(
            query_id: str,
            level: str,
            text: str,
            gold: set[str],
        ) -> None:
            status = "not_run" if gold else "excluded"

            queries.append(
                {
                    "query_id": query_id,
                    "claim_id": claim_id,
                    "level": level,
                    "query_text": text,
                    "document_id": document_id,
                    "gold_evidence_ids": sorted(gold),
                    "case_tags": truth.get("case_tags", []),
                    "indexed_gold_count": None,
                    "unindexed_gold_evidence_ids": [],
                    "stages": {
                        stage: {
                            "status": status,
                            "ranked_hits": [],
                            "metrics": None,
                            "error": None,
                        }
                        for stage in STAGES
                    },
                }
            )

        add_query(
            query_id=claim_id,
            level="claim",
            text=claim["claim_text"],
            gold=complete_gold,
        )

        # Evaluate atomic queries separately for compound claims.
        if len(subclaims) > 1:
            for index, (subclaim, gold) in enumerate(
                zip(subclaims, atomic_gold),
                start=1,
            ):
                add_query(
                    query_id=f"{claim_id}:subclaim:{index}",
                    level="subclaim",
                    text=subclaim["claim_text"],
                    gold=gold,
                )

    return queries


def _load_chunk_maps(
    queries: list[dict],
) -> tuple[dict[str, dict], dict[str, str]]:
    maps = {}
    hashes = {}

    document_ids = {
        query["document_id"]
        for query in queries
        if query["gold_evidence_ids"]
    }

    for document_id in sorted(document_ids):
        path = (
            PROCESSED_ROOT
            / document_id
            / "chunks.json"
        )

        if not path.exists():
            raise ValueError(
                f"{document_id} has no chunks.json. "
                "Create chunks and build its vector index "
                "before evaluating retrieval."
            )

        data = _read_json(path)
        chunks = data.get("chunks") or []

        if not chunks:
            raise ValueError(
                f"{document_id} has an empty chunks.json."
            )

        chunk_map = {}

        for chunk in chunks:
            chunk_id = str(chunk["id"])

            if chunk_id in chunk_map:
                raise ValueError(
                    f"{document_id} contains duplicate chunk IDs."
                )

            chunk_map[chunk_id] = chunk

        maps[document_id] = chunk_map
        hashes[str(path)] = _sha256(path)

    return maps, hashes


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    return number if math.isfinite(number) else None


def _ranked_hits(
    hits: list[dict],
    document_id: str,
    chunk_map: dict,
) -> list[dict]:
    ranked = []

    for rank, hit in enumerate(hits, start=1):
        chunk_id = str(
            hit.get("chunk_id")
            or hit.get("id")
            or ""
        )

        chunk = chunk_map.get(chunk_id, {})

        # Resolve identity from the current chunk file, not a guessed page
        # or an unchecked source ID returned by a retriever.
        source_id = chunk.get("source_evidence_id")
        reported_source = hit.get("source_evidence_id")
        resolved = bool(chunk and source_id)
        if reported_source is not None and str(reported_source) != str(source_id):
            resolved = False

        hit_document_id = str(
            hit.get("document_id") or document_id
        )

        page = hit.get("page") or chunk.get("page")

        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError, OverflowError):
            page = None

        ranked.append(
            {
                "rank": rank,
                "document_id": hit_document_id,
                "chunk_id": chunk_id,
                "source_evidence_id": (
                    str(source_id) if source_id else None
                ),
                "type": hit.get("type") or chunk.get("type"),
                "page": page,
                "score": _finite_number(hit.get("score")),
                "rerank_score": _finite_number(
                    hit.get("rerank_score")
                ),
                "injected_table": bool(
                    hit.get("injected_table")
                ),
                "source_identity_resolved": resolved and hit_document_id == document_id,
            }
        )

    return ranked


def _metrics(
    ranked: list[dict],
    gold: set[str],
    document_id: str,
) -> dict:
    """Query-level source recall, any-hit success and reciprocal rank at 20.

    Rank positions are chunk positions. Repeated chunks from one source
    count once in the recall numerator but still occupy rank positions.
    A query with no positive gold is excluded before reaching this function.
    """
    if not gold:
        raise ValueError("Recall and reciprocal rank require positive gold evidence.")
    window = ranked[:MRR_DEPTH]

    def matches(hit: dict) -> bool:
        return (
            hit["source_identity_resolved"]
            and hit["document_id"] == document_id
            and hit["source_evidence_id"] in gold
        )

    def found_at(k: int) -> set[str]:
        return {
            hit["source_evidence_id"]
            for hit in window[:k]
            if matches(hit)
        }

    found_5, found_10 = found_at(5), found_at(10)
    first_rank = next(
        (position for position, hit in enumerate(window, start=1) if matches(hit)),
        None,
    )
    return {
        "Recall@5": len(found_5) / len(gold),
        "Recall@10": len(found_10) / len(gold),
        "Hit@5": int(bool(found_5)),
        "Hit@10": int(bool(found_10)),
        "MRR@20": 1.0 / first_rank if first_rank is not None else 0.0,
        "first_correct_rank": first_rank,
        "gold_item_count": len(gold),
        "correct_sources_at_5": sorted(found_5),
        "correct_sources_at_10": sorted(found_10),
        "evaluated_hit_count": len(window),
        "unresolved_source_id_count": sum(
            not hit["source_identity_resolved"] for hit in window
        ),
    }


def _summaries(queries: list[dict]) -> list[dict]:
    summaries = []

    for level in LEVELS:
        level_queries = [
            query
            for query in queries
            if query["level"] == level
        ]

        for stage in STAGES:
            statuses = Counter(
                query["stages"][stage]["status"]
                for query in level_queries
            )

            completed = [
                query["stages"][stage]["metrics"]
                for query in level_queries
                if query["stages"][stage]["status"] == "ok"
            ]

            def mean(metric: str) -> float | None:
                if not completed:
                    return None

                return sum(
                    result[metric]
                    for result in completed
                ) / len(completed)

            summaries.append(
                {
                    "level": level,
                    "stage": stage,
                    "total_queries": len(level_queries),
                    "eligible_queries": (
                        len(level_queries)
                        - statuses["excluded"]
                    ),
                    "completed_queries": statuses["ok"],
                    "excluded_queries": statuses["excluded"],
                    "failed_queries": statuses["error"],
                    "blocked_queries": statuses["blocked"],
                    "not_run_queries": statuses["not_run"],
                    "Recall@5": mean("Recall@5"),
                    "Recall@10": mean("Recall@10"),
                    "Hit@5": mean("Hit@5"),
                    "Hit@10": mean("Hit@10"),
                    "MRR@20": mean("MRR@20"),
                }
            )

    return summaries


def evaluate_retrieval(
    *,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    candidate_depth: int = 20,
    use_claim_parser: bool = True,
    stop_on_error: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    if isinstance(candidate_depth, bool) or not isinstance(candidate_depth, int) or candidate_depth < MRR_DEPTH:
        raise ValueError("Candidate depth must be an integer of at least 20 for MRR@20.")

    load_dotenv()
    dataset_dir = Path(dataset_dir)

    claims, truths, manifest = _load_dataset(dataset_dir)
    queries = _build_queries(claims, truths)

    chunk_maps, chunk_hashes = _load_chunk_maps(queries)

    for query in queries:
        if not query["gold_evidence_ids"]:
            continue

        indexed_sources = {
            str(chunk["source_evidence_id"])
            for chunk in chunk_maps[
                query["document_id"]
            ].values()
            if chunk.get("source_evidence_id")
        }

        gold = set(query["gold_evidence_ids"])

        query["indexed_gold_count"] = len(
            gold & indexed_sources
        )

        query["unindexed_gold_evidence_ids"] = sorted(
            gold - indexed_sources
        )

    # Import retrieval dependencies only when evaluation runs.
    from retrieval import embeddings, reranker, vector_store

    if use_claim_parser:
        from verification.claim_parser import parse_claims

    # Reload BM25 data from the current chunk files.
    bm25_cache = getattr(
        vector_store,
        "_bm25_cache",
        None,
    )

    if isinstance(bm25_cache, dict):
        bm25_cache.clear()

    # Identical text/document queries reuse their rankings.
    ranking_cache = {}

    for index, query in enumerate(queries, start=1):
        gold = set(query["gold_evidence_ids"])

        if not gold:
            continue

        document_id = query["document_id"]
        text = query["query_text"]
        cache_key = (document_id, text)

        if progress:
            progress(
                f"Query {index}/{len(queries)} · "
                f"{query['level']} · {document_id}"
            )

        if cache_key not in ranking_cache:
            entry = {
                "initial": {
                    "status": "not_run",
                    "hits": [],
                    "error": None,
                },
                "reranked": {
                    "status": "not_run",
                    "hits": [],
                    "error": None,
                },
                "parsed_claim_used": False,
            }

            try:
                initial_hits = list(
                    vector_store.search(
                        text,
                        top_k=candidate_depth,
                        document_id=document_id,
                    )
                )

                entry["initial"].update(
                    status="ok",
                    hits=initial_hits,
                )

            except Exception as exc:
                entry["initial"].update(
                    status="error",
                    error=str(exc),
                )

                entry["reranked"].update(
                    status="blocked",
                    error="Initial retrieval failed.",
                )

            if entry["initial"]["status"] == "ok":
                try:
                    initial_hits = entry["initial"]["hits"]
                    parsed_claim = None

                    if use_claim_parser and initial_hits:
                        parsed = parse_claims(text)

                        if not isinstance(parsed, list) or not parsed:
                            raise ValueError("The claim parser returned no usable structures.")
                        for atom in parsed:
                            if not isinstance(atom, dict):
                                raise ValueError("The claim parser returned an invalid structure.")
                            failure = atom.get("parsing_error") or atom.get("error")
                            failure = failure or next((str(note) for note in atom.get("notes", [])
                                                       if str(note).startswith("Claim parsing unavailable:")), None)
                            if failure:
                                raise RuntimeError(f"Reranker metadata parsing failed: {failure}")
                        # A compound query has multiple structures.
                        # Do not apply the first atom's metadata
                        # to the complete compound claim.
                        if (
                            isinstance(parsed, list)
                            and len(parsed) == 1
                            and isinstance(parsed[0], dict)
                        ):
                            parsed_claim = parsed[0]

                    entry["parsed_claim_used"] = (
                        parsed_claim is not None
                    )

                    reranked_hits = reranker.rerank(
                        text,
                        initial_hits,
                        keep=max(10, len(initial_hits)),
                        parsed_claim=parsed_claim,
                    )

                    entry["reranked"].update(
                        status="ok",
                        hits=list(reranked_hits),
                    )

                except Exception as exc:
                    entry["reranked"].update(
                        status="error",
                        error=str(exc),
                    )

            ranking_cache[cache_key] = entry

        entry = ranking_cache[cache_key]

        query["parsed_claim_used"] = (
            entry["parsed_claim_used"]
        )

        for stage in STAGES:
            result = entry[stage]

            query["stages"][stage]["status"] = result["status"]
            query["stages"][stage]["error"] = result["error"]

            if result["status"] != "ok":
                continue

            ranked = _ranked_hits(
                result["hits"],
                document_id,
                chunk_maps[document_id],
            )

            query["stages"][stage]["ranked_hits"] = ranked
            query["stages"][stage]["metrics"] = _metrics(
                ranked,
                gold,
                document_id,
            )

        has_error = any(
            entry[stage]["status"] == "error"
            for stage in STAGES
        )

        if has_error and stop_on_error:
            break

    summaries = _summaries(queries)

    incomplete = any(
        summary["failed_queries"]
        or summary["blocked_queries"]
        or summary["not_run_queries"]
        for summary in summaries
    )

    timestamp = datetime.now(timezone.utc)
    report_id = (
        timestamp.strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    result_dir = dataset_dir / "retrieval_results"
    report_path = result_dir / f"{report_id}.json"

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "dataset_id": manifest["dataset_id"],
        "dataset_revision": manifest.get("revision"),
        "created_at": timestamp.isoformat(),
        "complete": not incomplete,
        "report_path": str(report_path),
        "configuration": {
            "candidate_depth": candidate_depth,
            "metric_depth": MRR_DEPTH,
            "mrr_cutoff": MRR_DEPTH,
            "metric_names": list(METRIC_NAMES),
            "rerank_keep": "all returned candidates",
            "use_claim_parser": use_claim_parser,
            "stop_on_error": stop_on_error,
            "reranker_model": reranker.RERANK_MODEL,
            "embedding_model": getattr(embeddings, "EMBED_MODEL", None),
            "embedding_dimensions": getattr(embeddings, "DIMENSIONS", None),
            "vector_collection": getattr(embeddings, "COLLECTION_NAME", None),
            "hybrid_candidate_pool": getattr(vector_store, "CANDIDATE_POOL", None),
            "matching": (
                "document_id plus original source_evidence_id"
            ),
            "ranking_unit": "chunk",
            "gold_unit": "source evidence item",
            "averaging": (
                "Macro average over successfully completed "
                "eligible queries, separately for each stage/level."
            ),
            "mrr_definition": (
                f"Reciprocal first-gold rank within top "
                f"{MRR_DEPTH}; zero when absent."
            ),
            "recall_definition": "Distinct gold source IDs in the first k chunk positions divided by all gold source IDs.",
            "hit_definition": "One if any correct source occurs in the first k positions; zero otherwise.",
            "unindexed_gold_policy": "Gold items without indexed chunks remain in the recall denominator.",
            "error_policy": "Failed, blocked and unattempted stages have null metrics; they are not retrieval misses.",
            "subclaim_scope": (
                "Individual atoms of compound claims."
            ),
            "unknown_policy": (
                "UNKNOWN context citations are not gold. "
                "Queries without resolved gold evidence are excluded."
            ),
        },
        "dataset_checksums": {
            "claims_sha256": manifest["claims_sha256"],
            "ground_truth_sha256": (
                manifest["ground_truth_sha256"]
            ),
        },
        "chunk_file_checksums": chunk_hashes,
        "summary": summaries,
        "paired_summary": _summaries([
            query for query in queries
            if all(query["stages"][stage]["status"] == "ok" for stage in STAGES)
        ]),
        "queries": queries,
    }

    _write_json(report_path, report)
    return report
# from __future__ import annotation

# import hashlib
# import json
# import math
# import uuid
# from collections import Counter
# from datetime import datetime, timezone
# from pathlib import Path
# from typing import Callable

# from dotenv import load_dotenv


# DEFAULT_DATASET_DIR = Path("data/evaluation")
# PROCESSED_ROOT = Path("data/processed")

# STAGES = ("initial", "reranked")
# LEVELS = ("claim", "subclaim")


# def _read_json(path: Path) -> dict:
#     return json.loads(path.read_text(encoding="utf-8"))


# def _read_jsonl(path: Path) -> list[dict]:
#     records = []

#     for line_number, line in enumerate(
#         path.read_text(encoding="utf-8").splitlines(),
#         start=1,
#     ):
#         if not line.strip():
#             continue

#         try:
#             record = json.loads(line)
#         except json.JSONDecodeError as exc:
#             raise ValueError(
#                 f"{path.name}, line {line_number}: invalid JSON."
#             ) from exc

#         if not isinstance(record, dict):
#             raise ValueError(
#                 f"{path.name}, line {line_number}: "
#                 "expected an object."
#             )

#         records.append(record)

#     return records


# def _sha256(path: Path) -> str:
#     digest = hashlib.sha256()

#     with path.open("rb") as file:
#         for block in iter(lambda: file.read(1024 * 1024), b""):
#             digest.update(block)

#     return digest.hexdigest()


# def _write_json(path: Path, value: dict) -> None:
#     path.parent.mkdir(parents=True, exist_ok=True)

#     content = json.dumps(
#         value,
#         indent=2,
#         ensure_ascii=False,
#         allow_nan=False,
#     )

#     temporary = path.with_name(
#         f".{path.name}.{uuid.uuid4().hex}.tmp"
#     )

#     try:
#         temporary.write_text(content, encoding="utf-8")
#         temporary.replace(path)
#     finally:
#         temporary.unlink(missing_ok=True)


# def _load_dataset(
#     dataset_dir: Path,
# ) -> tuple[list[dict], dict[str, dict], dict]:
#     paths = {
#         "claims": dataset_dir / "claims.jsonl",
#         "ground_truth": dataset_dir / "ground_truth.jsonl",
#         "manifest": dataset_dir / "manifest.json",
#     }

#     for path in paths.values():
#         if not path.exists():
#             raise ValueError(
#                 f"Missing {path}. Approve and export "
#                 "the evaluation dataset first."
#             )

#     manifest = _read_json(paths["manifest"])

#     for name in ("claims", "ground_truth"):
#         expected = manifest.get(f"{name}_sha256")

#         if not expected:
#             raise ValueError(
#                 f"Manifest has no checksum for {name}."
#             )

#         if _sha256(paths[name]) != expected:
#             raise ValueError(
#                 f"{paths[name].name} differs from its manifest. "
#                 "Export the reviewed dataset again."
#             )

#     for filename, expected in manifest.get(
#         "source_hashes", {}
#     ).items():
#         path = Path(filename)

#         if not path.exists() or _sha256(path) != expected:
#             raise ValueError(
#                 f"Dataset source changed or disappeared: {filename}. "
#                 "Use a dataset reviewed against the current sources."
#             )

#     claims = _read_jsonl(paths["claims"])
#     truths = _read_jsonl(paths["ground_truth"])

#     if not claims:
#         raise ValueError("The exported dataset is empty.")

#     claim_ids = [str(item["id"]) for item in claims]
#     truth_ids = [str(item["id"]) for item in truths]

#     if len(set(claim_ids)) != len(claim_ids):
#         raise ValueError("Duplicate claim IDs.")

#     if len(set(truth_ids)) != len(truth_ids):
#         raise ValueError("Duplicate ground-truth IDs.")

#     if set(claim_ids) != set(truth_ids):
#         raise ValueError(
#             "Claims and ground truth have different IDs."
#         )

#     if len(claims) != manifest.get("claim_count"):
#         raise ValueError(
#             "Claim count differs from the manifest."
#         )

#     for truth in truths:
#         if truth.get("review_status") != "approved":
#             raise ValueError(
#                 "Ground truth contains an unapproved record."
#             )

#     return (
#         claims,
#         {str(item["id"]): item for item in truths},
#         manifest,
#     )


# def _build_queries(
#     claims: list[dict],
#     truths: dict[str, dict],
# ) -> list[dict]:
#     queries = []

#     for claim in claims:
#         claim_id = str(claim["id"])
#         truth = truths[claim_id]

#         document_ids = claim.get("document_ids") or []

#         if len(document_ids) != 1:
#             raise ValueError(
#                 f"{claim_id}: this evaluator expects one "
#                 "document per claim, matching the dataset builder."
#             )

#         document_id = str(document_ids[0])
#         subclaims = truth.get("subclaims") or []

#         if not subclaims:
#             raise ValueError(
#                 f"{claim_id}: ground-truth subclaims are missing."
#             )

#         citation_map = {
#             item["citation_id"]: item
#             for item in truth.get("citations", [])
#         }

#         def gold_for(subclaim: dict) -> set[str]:
#             if subclaim["verdict"] == "UNKNOWN":
#                 # Context for an unresolved assertion is not
#                 # positive gold evidence for that assertion.
#                 return set()

#             gold = set()

#             for citation_id in subclaim.get("evidence_ids", []):
#                 citation = citation_map.get(citation_id)

#                 if citation is None:
#                     raise ValueError(
#                         f"{claim_id}: citation metadata is missing "
#                         f"for {citation_id}."
#                     )

#                 if citation["document_id"] != document_id:
#                     raise ValueError(
#                         f"{claim_id}: citation document mismatch."
#                     )

#                 source_id = citation.get("evidence_id")

#                 if not source_id:
#                     raise ValueError(
#                         f"{claim_id}: citation has no original "
#                         "evidence ID."
#                     )

#                 gold.add(str(source_id))

#             return gold

#         atomic_gold = [
#             gold_for(subclaim)
#             for subclaim in subclaims
#         ]

#         complete_gold = set().union(*atomic_gold)

#         def add_query(
#             query_id: str,
#             level: str,
#             text: str,
#             gold: set[str],
#         ) -> None:
#             status = "not_run" if gold else "excluded"

#             queries.append(
#                 {
#                     "query_id": query_id,
#                     "claim_id": claim_id,
#                     "level": level,
#                     "query_text": text,
#                     "document_id": document_id,
#                     "gold_evidence_ids": sorted(gold),
#                     "case_tags": truth.get("case_tags", []),
#                     "indexed_gold_count": None,
#                     "unindexed_gold_evidence_ids": [],
#                     "stages": {
#                         stage: {
#                             "status": status,
#                             "ranked_hits": [],
#                             "metrics": None,
#                             "error": None,
#                         }
#                         for stage in STAGES
#                     },
#                 }
#             )

#         add_query(
#             query_id=claim_id,
#             level="claim",
#             text=claim["claim_text"],
#             gold=complete_gold,
#         )

#         # Evaluate atomic queries separately for compound claims.
#         if len(subclaims) > 1:
#             for index, (subclaim, gold) in enumerate(
#                 zip(subclaims, atomic_gold),
#                 start=1,
#             ):
#                 add_query(
#                     query_id=f"{claim_id}:subclaim:{index}",
#                     level="subclaim",
#                     text=subclaim["claim_text"],
#                     gold=gold,
#                 )

#     return queries


# def _load_chunk_maps(
#     queries: list[dict],
# ) -> tuple[dict[str, dict], dict[str, str]]:
#     maps = {}
#     hashes = {}

#     document_ids = {
#         query["document_id"]
#         for query in queries
#         if query["gold_evidence_ids"]
#     }

#     for document_id in sorted(document_ids):
#         path = (
#             PROCESSED_ROOT
#             / document_id
#             / "chunks.json"
#         )

#         if not path.exists():
#             raise ValueError(
#                 f"{document_id} has no chunks.json. "
#                 "Create chunks and build its vector index "
#                 "before evaluating retrieval."
#             )

#         data = _read_json(path)
#         chunks = data.get("chunks") or []

#         if not chunks:
#             raise ValueError(
#                 f"{document_id} has an empty chunks.json."
#             )

#         chunk_map = {}

#         for chunk in chunks:
#             chunk_id = str(chunk["id"])

#             if chunk_id in chunk_map:
#                 raise ValueError(
#                     f"{document_id} contains duplicate chunk IDs."
#                 )

#             chunk_map[chunk_id] = chunk

#         maps[document_id] = chunk_map
#         hashes[str(path)] = _sha256(path)

#     return maps, hashes


# def _finite_number(value: object) -> float | None:
#     try:
#         number = float(value)
#     except (TypeError, ValueError, OverflowError):
#         return None

#     return number if math.isfinite(number) else None


# def _ranked_hits(
#     hits: list[dict],
#     document_id: str,
#     chunk_map: dict,
# ) -> list[dict]:
#     ranked = []

#     for rank, hit in enumerate(hits, start=1):
#         chunk_id = str(
#             hit.get("chunk_id")
#             or hit.get("id")
#             or ""
#         )

#         chunk = chunk_map.get(chunk_id, {})

#         source_id = (
#             hit.get("source_evidence_id")
#             or chunk.get("source_evidence_id")
#         )

#         hit_document_id = str(
#             hit.get("document_id") or document_id
#         )

#         page = hit.get("page") or chunk.get("page")

#         try:
#             page = int(page) if page is not None else None
#         except (TypeError, ValueError, OverflowError):
#             page = None

#         ranked.append(
#             {
#                 "rank": rank,
#                 "document_id": hit_document_id,
#                 "chunk_id": chunk_id,
#                 "source_evidence_id": (
#                     str(source_id) if source_id else None
#                 ),
#                 "type": hit.get("type") or chunk.get("type"),
#                 "page": page,
#                 "score": _finite_number(hit.get("score")),
#                 "rerank_score": _finite_number(
#                     hit.get("rerank_score")
#                 ),
#                 "injected_table": bool(
#                     hit.get("injected_table")
#                 ),
#                 "source_identity_resolved": bool(source_id),
#             }
#         )

#     return ranked


# def _metrics(
#     ranked: list[dict],
#     gold: set[str],
#     document_id: str,
#     depth: int,
# ) -> dict:
#     window = ranked[:depth]

#     def matches(hit: dict) -> bool:
#         return (
#             hit["document_id"] == document_id
#             and hit["source_evidence_id"] in gold
#         )

#     def recall_at(k: int) -> float:
#         found = {
#             hit["source_evidence_id"]
#             for hit in window[:k]
#             if matches(hit)
#         }

#         # Several chunks from one source give credit once.
#         return len(found) / len(gold)

#     first_rank = next(
#         (
#             hit["rank"]
#             for hit in window
#             if matches(hit)
#         ),
#         None,
#     )

#     return {
#         "Recall@5": recall_at(5),
#         "Recall@10": recall_at(10),
#         "MRR": (
#             1.0 / first_rank
#             if first_rank is not None
#             else 0.0
#         ),
#         "first_correct_rank": first_rank,
#         "evaluated_hit_count": len(window),
#         "unresolved_source_id_count": sum(
#             not hit["source_identity_resolved"]
#             for hit in window
#         ),
#     }


# def _summaries(queries: list[dict]) -> list[dict]:
#     summaries = []

#     for level in LEVELS:
#         level_queries = [
#             query
#             for query in queries
#             if query["level"] == level
#         ]

#         for stage in STAGES:
#             statuses = Counter(
#                 query["stages"][stage]["status"]
#                 for query in level_queries
#             )

#             completed = [
#                 query["stages"][stage]["metrics"]
#                 for query in level_queries
#                 if query["stages"][stage]["status"] == "ok"
#             ]

#             def mean(metric: str) -> float | None:
#                 if not completed:
#                     return None

#                 return sum(
#                     result[metric]
#                     for result in completed
#                 ) / len(completed)

#             summaries.append(
#                 {
#                     "level": level,
#                     "stage": stage,
#                     "total_queries": len(level_queries),
#                     "eligible_queries": (
#                         len(level_queries)
#                         - statuses["excluded"]
#                     ),
#                     "completed_queries": statuses["ok"],
#                     "excluded_queries": statuses["excluded"],
#                     "failed_queries": statuses["error"],
#                     "blocked_queries": statuses["blocked"],
#                     "not_run_queries": statuses["not_run"],
#                     "Recall@5": mean("Recall@5"),
#                     "Recall@10": mean("Recall@10"),
#                     "MRR": mean("MRR"),
#                 }
#             )

#     return summaries


# def evaluate_retrieval
#     *,
#     dataset_dir: Path = DEFAULT_DATASET_DIR,
#     candidate_depth: int = 20,
#     use_claim_parser: bool = True,
#     stop_on_error: bool = True,
#     progress: Callable[[str], None] | None = None,
# ) -> dict:
#     if candidate_depth < 10:
#         raise ValueError(
#             "Candidate depth must be at least 10."
#         )

#     load_dotenv()
#     dataset_dir = Path(dataset_dir)

#     claims, truths, manifest = _load_dataset(dataset_dir)
#     queries = _build_queries(claims, truths)

#     chunk_maps, chunk_hashes = _load_chunk_maps(queries)

#     for query in queries:
#         if not query["gold_evidence_ids"]:
#             continue

#         indexed_sources = {
#             str(chunk["source_evidence_id"])
#             for chunk in chunk_maps[
#                 query["document_id"]
#             ].values()
#             if chunk.get("source_evidence_id")
#         }

#         gold = set(query["gold_evidence_ids"])

#         query["indexed_gold_count"] = len(
#             gold & indexed_sources
#         )

#         query["unindexed_gold_evidence_ids"] = sorted(
#             gold - indexed_sources
#         )

#     # Import retrieval dependencies only when evaluation runs.
#     from retrieval import reranker, vector_store

#     if use_claim_parser:
#         from verification.claim_parser import parse_claims

#     # Reload BM25 data from the current chunk files.
#     bm25_cache = getattr(
#         vector_store,
#         "_bm25_cache",
#         None,
#     )

#     if isinstance(bm25_cache, dict):
#         bm25_cache.clear()

#     # Identical text/document queries reuse their rankings.
#     ranking_cache = {}

#     for index, query in enumerate(queries, start=1):
#         gold = set(query["gold_evidence_ids"])

#         if not gold:
#             continue

#         document_id = query["document_id"]
#         text = query["query_text"]
#         cache_key = (document_id, text)

#         if progress:
#             progress(
#                 f"Query {index}/{len(queries)} · "
#                 f"{query['level']} · {document_id}"
#             )

#         if cache_key not in ranking_cache:
#             entry = {
#                 "initial": {
#                     "status": "not_run",
#                     "hits": [],
#                     "error": None,
#                 },
#                 "reranked": {
#                     "status": "not_run",
#                     "hits": [],
#                     "error": None,
#                 },
#                 "parsed_claim_used": False,
#             }

#             try:
#                 initial_hits = list(
#                     vector_store.search(
#                         text,
#                         top_k=candidate_depth,
#                         document_id=document_id,
#                     )
#                 )

#                 entry["initial"].update(
#                     status="ok",
#                     hits=initial_hits,
#                 )

#             except Exception as exc:
#                 entry["initial"].update(
#                     status="error",
#                     error=str(exc),
#                 )

#                 entry["reranked"].update(
#                     status="blocked",
#                     error="Initial retrieval failed.",
#                 )

#             if entry["initial"]["status"] == "ok":
#                 try:
#                     initial_hits = entry["initial"]["hits"]
#                     parsed_claim = None

#                     if use_claim_parser and initial_hits:
#                         parsed = parse_claims(text)

#                         # A compound query has multiple structures.
#                         # Do not apply the first atom's metadata
#                         # to the complete compound claim.
#                         if (
#                             isinstance(parsed, list)
#                             and len(parsed) == 1
#                             and isinstance(parsed[0], dict)
#                         ):
#                             parsed_claim = parsed[0]

#                     entry["parsed_claim_used"] = (
#                         parsed_claim is not None
#                     )

#                     reranked_hits = reranker.rerank(
#                         text,
#                         initial_hits,
#                         keep=max(10, len(initial_hits)),
#                         parsed_claim=parsed_claim,
#                     )

#                     entry["reranked"].update(
#                         status="ok",
#                         hits=list(reranked_hits),
#                     )

#                 except Exception as exc:
#                     entry["reranked"].update(
#                         status="error",
#                         error=str(exc),
#                     )

#             ranking_cache[cache_key] = entry

#         entry = ranking_cache[cache_key]

#         query["parsed_claim_used"] = (
#             entry["parsed_claim_used"]
#         )

#         for stage in STAGES:
#             result = entry[stage]

#             query["stages"][stage]["status"] = result["status"]
#             query["stages"][stage]["error"] = result["error"]

#             if result["status"] != "ok":
#                 continue

#             ranked = _ranked_hits(
#                 result["hits"],
#                 document_id,
#                 chunk_maps[document_id],
#             )

#             query["stages"][stage]["ranked_hits"] = ranked
#             query["stages"][stage]["metrics"] = _metrics(
#                 ranked,
#                 gold,
#                 document_id,
#                 candidate_depth,
#             )

#         has_error = any(
#             entry[stage]["status"] == "error"
#             for stage in STAGES
#         )

#         if has_error and stop_on_error:
#             break

#     summaries = _summaries(queries)

#     incomplete = any(
#         summary["failed_queries"]
#         or summary["blocked_queries"]
#         or summary["not_run_queries"]
#         for summary in summaries
#     )

#     timestamp = datetime.now(timezone.utc)
#     report_id = (
#         timestamp.strftime("%Y%m%d_%H%M%S")
#         + "_"
#         + uuid.uuid4().hex[:8]
#     )

#     result_dir = dataset_dir / "retrieval_results"
#     report_path = result_dir / f"{report_id}.json"

#     report = {
#         "report_id": report_id,
#         "dataset_id": manifest["dataset_id"],
#         "dataset_revision": manifest.get("revision"),
#         "created_at": timestamp.isoformat(),
#         "complete": not incomplete,
#         "report_path": str(report_path),
#         "configuration": {
#             "candidate_depth": candidate_depth,
#             "metric_depth": candidate_depth,
#             "rerank_keep": "all returned candidates",
#             "use_claim_parser": use_claim_parse,
#             "stop_on_error": stop_on_error,
#             "reranker_model": reranker.RERANK_MODEL,
#             "matching": (
#                 "document_id plus original source_evidence_id"
#             ),
#             "ranking_unit": "chunk",
#             "gold_unit": "source evidence item",
#             "averaging": (
#                 "Macro average over successfully completed "
#                 "eligible queries, separately for each stage/level."
#             ),
#             "mrr_definition": (
#                 f"Reciprocal first-gold rank within top "
#                 f"{candidate_depth}; zero when absent."
#             ),
#             "subclaim_scope": (
#                 "Individual atoms of compound claims."
#             ),
#             "unknown_policy": (
#                 "UNKNOWN context citations are not gold. "
#                 "Queries without resolved gold evidence are excluded."
#             ),
#         },
#         "dataset_checksums": {
#             "claims_sha256": manifest["claims_sha256"],
#             "ground_truth_sha256": (
#                 manifest["ground_truth_sha256"]
#             ),
#         },
#         "chunk_file_checksums": chunk_hashes,
#         "summary": summaries,
#         "queries": queries,
#     }

#     _write_json(report_path, report)
#     return report
# # from __future__ import annotation

# # import hashlib
# # import json
# # import math
# # import uuid
# # from collections import Counter
# # from datetime import datetime, timezone
# # from pathlib import Path
# # from typing import Callable

# # from dotenv import load_dotenv


# # DEFAULT_DATASET_DIR = Path("data/evaluation")
# # PROCESSED_ROOT = Path("data/processed")

# # STAGES = ("initial", "reranked")
# # LEVELS = ("claim", "subclaim")


# # def _read_json(path: Path) -> dict:
# #     return json.loads(path.read_text(encoding="utf-8"))


# # def _read_jsonl(path: Path) -> list[dict]:
# #     records = []

# #     for line_number, line in enumerate(
# #         path.read_text(encoding="utf-8").splitlines(),
# #         start=1,
# #     ):
# #         if not line.strip():
# #             continue

# #         try:
# #             record = json.loads(line)
# #         except json.JSONDecodeError as exc:
# #             raise ValueError(
# #                 f"{path.name}, line {line_number}: invalid JSON."
# #             ) from exc

# #         if not isinstance(record, dict):
# #             raise ValueError(
# #                 f"{path.name}, line {line_number}: "
# #                 "expected an object."
# #             )

# #         records.append(record)

# #     return records


# # def _sha256(path: Path) -> str:
# #     digest = hashlib.sha256()

# #     with path.open("rb") as file:
# #         for block in iter(lambda: file.read(1024 * 1024), b""):
# #             digest.update(block)

# #     return digest.hexdigest()


# # def _write_json(path: Path, value: dict) -> None:
# #     path.parent.mkdir(parents=True, exist_ok=True)

# #     content = json.dumps(
# #         value,
# #         indent=2,
# #         ensure_ascii=False,
# #         allow_nan=False,
# #     )

# #     temporary = path.with_name(
# #         f".{path.name}.{uuid.uuid4().hex}.tmp"
# #     )

# #     try:
# #         temporary.write_text(content, encoding="utf-8")
# #         temporary.replace(path)
# #     finally:
# #         temporary.unlink(missing_ok=True)


# # def _load_dataset(
# #     dataset_dir: Path,
# # ) -> tuple[list[dict], dict[str, dict], dict]:
# #     paths = {
# #         "claims": dataset_dir / "claims.jsonl",
# #         "ground_truth": dataset_dir / "ground_truth.jsonl",
# #         "manifest": dataset_dir / "manifest.json",
# #     }

# #     for path in paths.values():
# #         if not path.exists():
# #             raise ValueError(
# #                 f"Missing {path}. Approve and export "
# #                 "the evaluation dataset first."
# #             )

# #     manifest = _read_json(paths["manifest"])

# #     for name in ("claims", "ground_truth"):
# #         expected = manifest.get(f"{name}_sha256")

# #         if not expected:
# #             raise ValueError(
# #                 f"Manifest has no checksum for {name}."
# #             )

# #         if _sha256(paths[name]) != expected:
# #             raise ValueError(
# #                 f"{paths[name].name} differs from its manifest. "
# #                 "Export the reviewed dataset again."
# #             )

# #     for filename, expected in manifest.get(
# #         "source_hashes", {}
# #     ).items():
# #         path = Path(filename)

# #         if not path.exists() or _sha256(path) != expected:
# #             raise ValueError(
# #                 f"Dataset source changed or disappeared: {filename}. "
# #                 "Use a dataset reviewed against the current sources."
# #             )

# #     claims = _read_jsonl(paths["claims"])
# #     truths = _read_jsonl(paths["ground_truth"])

# #     if not claims:
# #         raise ValueError("The exported dataset is empty.")

# #     claim_ids = [str(item["id"]) for item in claims]
# #     truth_ids = [str(item["id"]) for item in truths]

# #     if len(set(claim_ids)) != len(claim_ids):
# #         raise ValueError("Duplicate claim IDs.")

# #     if len(set(truth_ids)) != len(truth_ids):
# #         raise ValueError("Duplicate ground-truth IDs.")

# #     if set(claim_ids) != set(truth_ids):
# #         raise ValueError(
# #             "Claims and ground truth have different IDs."
# #         )

# #     if len(claims) != manifest.get("claim_count"):
# #         raise ValueError(
# #             "Claim count differs from the manifest."
# #         )

# #     for truth in truths:
# #         if truth.get("review_status") != "approved":
# #             raise ValueError(
# #                 "Ground truth contains an unapproved record."
# #             )

# #     return (
# #         claims,
# #         {str(item["id"]): item for item in truths},
# #         manifest,
# #     )


# # def _build_queries(
# #     claims: list[dict],
# #     truths: dict[str, dict],
# # ) -> list[dict]:
# #     queries = []

# #     for claim in claims:
# #         claim_id = str(claim["id"])
# #         truth = truths[claim_id]

# #         document_ids = claim.get("document_ids") or []

# #         if len(document_ids) != 1:
# #             raise ValueError(
# #                 f"{claim_id}: this evaluator expects one "
# #                 "document per claim, matching the dataset builder."
# #             )

# #         document_id = str(document_ids[0])
# #         subclaims = truth.get("subclaims") or []

# #         if not subclaims:
# #             raise ValueError(
# #                 f"{claim_id}: ground-truth subclaims are missing."
# #             )

# #         citation_map = {
# #             item["citation_id"]: item
# #             for item in truth.get("citations", [])
# #         }

# #         def gold_for(subclaim: dict) -> set[str]:
# #             if subclaim["verdict"] == "UNKNOWN":
# #                 # Context for an unresolved assertion is not
# #                 # positive gold evidence for that assertion.
# #                 return set()

# #             gold = set()

# #             for citation_id in subclaim.get("evidence_ids", []):
# #                 citation = citation_map.get(citation_id)

# #                 if citation is None:
# #                     raise ValueError(
# #                         f"{claim_id}: citation metadata is missing "
# #                         f"for {citation_id}."
# #                     )

# #                 if citation["document_id"] != document_id:
# #                     raise ValueError(
# #                         f"{claim_id}: citation document mismatch."
# #                     )

# #                 source_id = citation.get("evidence_id")

# #                 if not source_id:
# #                     raise ValueError(
# #                         f"{claim_id}: citation has no original "
# #                         "evidence ID."
# #                     )

# #                 gold.add(str(source_id))

# #             return gold

# #         atomic_gold = [
# #             gold_for(subclaim)
# #             for subclaim in subclaims
# #         ]

# #         complete_gold = set().union(*atomic_gold)

# #         def add_query(
# #             query_id: str,
# #             level: str,
# #             text: str,
# #             gold: set[str],
# #         ) -> None:
# #             status = "not_run" if gold else "excluded"

# #             queries.append(
# #                 {
# #                     "query_id": query_id,
# #                     "claim_id": claim_id,
# #                     "level": level,
# #                     "query_text": text,
# #                     "document_id": document_id,
# #                     "gold_evidence_ids": sorted(gold),
# #                     "case_tags": truth.get("case_tags", []),
# #                     "indexed_gold_count": None,
# #                     "unindexed_gold_evidence_ids": [],
# #                     "stages": {
# #                         stage: {
# #                             "status": status,
# #                             "ranked_hits": [],
# #                             "metrics": None,
# #                             "error": None,
# #                         }
# #                         for stage in STAGES
# #                     },
# #                 }
# #             )

# #         add_query(
# #             query_id=claim_id,
# #             level="claim",
# #             text=claim["claim_text"],
# #             gold=complete_gold,
# #         )

# #         # Evaluate atomic queries separately for compound claims.
# #         if len(subclaims) > 1:
# #             for index, (subclaim, gold) in enumerate(
# #                 zip(subclaims, atomic_gold),
# #                 start=1,
# #             ):
# #                 add_query(
# #                     query_id=f"{claim_id}:subclaim:{index}",
# #                     level="subclaim",
# #                     text=subclaim["claim_text"],
# #                     gold=gold,
# #                 )

# #     return queries


# # def _load_chunk_maps(
# #     queries: list[dict],
# # ) -> tuple[dict[str, dict], dict[str, str]]:
# #     maps = {}
# #     hashes = {}

# #     document_ids = {
# #         query["document_id"]
# #         for query in queries
# #         if query["gold_evidence_ids"]
# #     }

# #     for document_id in sorted(document_ids):
# #         path = (
# #             PROCESSED_ROOT
# #             / document_id
# #             / "chunks.json"
# #         )

# #         if not path.exists():
# #             raise ValueError(
# #                 f"{document_id} has no chunks.json. "
# #                 "Create chunks and build its vector index "
# #                 "before evaluating retrieval."
# #             )

# #         data = _read_json(path)
# #         chunks = data.get("chunks") or []

# #         if not chunks:
# #             raise ValueError(
# #                 f"{document_id} has an empty chunks.json."
# #             )

# #         chunk_map = {}

# #         for chunk in chunks:
# #             chunk_id = str(chunk["id"])

# #             if chunk_id in chunk_map:
# #                 raise ValueError(
# #                     f"{document_id} contains duplicate chunk IDs."
# #                 )

# #             chunk_map[chunk_id] = chunk

# #         maps[document_id] = chunk_map
# #         hashes[str(path)] = _sha256(path)

# #     return maps, hashes


# # def _finite_number(value: object) -> float | None:
# #     try:
# #         number = float(value)
# #     except (TypeError, ValueError, OverflowError):
# #         return None

# #     return number if math.isfinite(number) else None


# # def _ranked_hits(
# #     hits: list[dict],
# #     document_id: str,
# #     chunk_map: dict,
# # ) -> list[dict]:
# #     ranked = []

# #     for rank, hit in enumerate(hits, start=1):
# #         chunk_id = str(
# #             hit.get("chunk_id")
# #             or hit.get("id")
# #             or ""
# #         )

# #         chunk = chunk_map.get(chunk_id, {})

# #         source_id = (
# #             hit.get("source_evidence_id")
# #             or chunk.get("source_evidence_id")
# #         )

# #         hit_document_id = str(
# #             hit.get("document_id") or document_id
# #         )

# #         page = hit.get("page") or chunk.get("page")

# #         try:
# #             page = int(page) if page is not None else None
# #         except (TypeError, ValueError, OverflowError):
# #             page = None

# #         ranked.append(
# #             {
# #                 "rank": rank,
# #                 "document_id": hit_document_id,
# #                 "chunk_id": chunk_id,
# #                 "source_evidence_id": (
# #                     str(source_id) if source_id else None
# #                 ),
# #                 "type": hit.get("type") or chunk.get("type"),
# #                 "page": page,
# #                 "score": _finite_number(hit.get("score")),
# #                 "rerank_score": _finite_number(
# #                     hit.get("rerank_score")
# #                 ),
# #                 "injected_table": bool(
# #                     hit.get("injected_table")
# #                 ),
# #                 "source_identity_resolved": bool(source_id),
# #             }
# #         )

# #     return ranked


# # def _metrics(
# #     ranked: list[dict],
# #     gold: set[str],
# #     document_id: str,
# #     depth: int,
# # ) -> dict:
# #     window = ranked[:depth]

# #     def matches(hit: dict) -> bool:
# #         return (
# #             hit["document_id"] == document_id
# #             and hit["source_evidence_id"] in gold
# #         )

# #     def recall_at(k: int) -> float:
# #         found = {
# #             hit["source_evidence_id"]
# #             for hit in window[:k]
# #             if matches(hit)
# #         }

# #         # Several chunks from one source give credit once.
# #         return len(found) / len(gold)

# #     first_rank = next(
# #         (
# #             hit["rank"]
# #             for hit in window
# #             if matches(hit)
# #         ),
# #         None,
# #     )

# #     return {
# #         "Recall@5": recall_at(5),
# #         "Recall@10": recall_at(10),
# #         "MRR": (
# #             1.0 / first_rank
# #             if first_rank is not None
# #             else 0.0
# #         ),
# #         "first_correct_rank": first_rank,
# #         "evaluated_hit_count": len(window),
# #         "unresolved_source_id_count": sum(
# #             not hit["source_identity_resolved"]
# #             for hit in window
# #         ),
# #     }


# # def _summaries(queries: list[dict]) -> list[dict]:
# #     summaries = []

# #     for level in LEVELS:
# #         level_queries = [
# #             query
# #             for query in queries
# #             if query["level"] == level
# #         ]

# #         for stage in STAGES:
# #             statuses = Counter(
# #                 query["stages"][stage]["status"]
# #                 for query in level_queries
# #             )

# #             completed = [
# #                 query["stages"][stage]["metrics"]
# #                 for query in level_queries
# #                 if query["stages"][stage]["status"] == "ok"
# #             ]

# #             def mean(metric: str) -> float | None:
# #                 if not completed:
# #                     return None

# #                 return sum(
# #                     result[metric]
# #                     for result in completed
# #                 ) / len(completed)

# #             summaries.append(
# #                 {
# #                     "level": level,
# #                     "stage": stage,
# #                     "total_queries": len(level_queries),
# #                     "eligible_queries": (
# #                         len(level_queries)
# #                         - statuses["excluded"]
# #                     ),
# #                     "completed_queries": statuses["ok"],
# #                     "excluded_queries": statuses["excluded"],
# #                     "failed_queries": statuses["error"],
# #                     "blocked_queries": statuses["blocked"],
# #                     "not_run_queries": statuses["not_run"],
# #                     "Recall@5": mean("Recall@5"),
# #                     "Recall@10": mean("Recall@10"),
# #                     "MRR": mean("MRR"),
# #                 }
# #             )

# #     return summaries


# # def evaluate_retrieval
# #     *,
# #     dataset_dir: Path = DEFAULT_DATASET_DIR,
# #     candidate_depth: int = 20,
# #     use_claim_parser: bool = True,
# #     stop_on_error: bool = True,
# #     progress: Callable[[str], None] | None = None,
# # ) -> dict:
# #     if candidate_depth < 10:
# #         raise ValueError(
# #             "Candidate depth must be at least 10."
# #         )

# #     load_dotenv()
# #     dataset_dir = Path(dataset_dir)

# #     claims, truths, manifest = _load_dataset(dataset_dir)
# #     queries = _build_queries(claims, truths)

# #     chunk_maps, chunk_hashes = _load_chunk_maps(queries)

# #     for query in queries:
# #         if not query["gold_evidence_ids"]:
# #             continue

# #         indexed_sources = {
# #             str(chunk["source_evidence_id"])
# #             for chunk in chunk_maps[
# #                 query["document_id"]
# #             ].values()
# #             if chunk.get("source_evidence_id")
# #         }

# #         gold = set(query["gold_evidence_ids"])

# #         query["indexed_gold_count"] = len(
# #             gold & indexed_sources
# #         )

# #         query["unindexed_gold_evidence_ids"] = sorted(
# #             gold - indexed_sources
# #         )

# #     # Import retrieval dependencies only when evaluation runs.
# #     from retrieval import reranker, vector_store

# #     if use_claim_parser:
# #         from verification.claim_parser import parse_claims

# #     # Reload BM25 data from the current chunk files.
# #     bm25_cache = getattr(
# #         vector_store,
# #         "_bm25_cache",
# #         None,
# #     )

# #     if isinstance(bm25_cache, dict):
# #         bm25_cache.clear()

# #     # Identical text/document queries reuse their rankings.
# #     ranking_cache = {}

# #     for index, query in enumerate(queries, start=1):
# #         gold = set(query["gold_evidence_ids"])

# #         if not gold:
# #             continue

# #         document_id = query["document_id"]
# #         text = query["query_text"]
# #         cache_key = (document_id, text)

# #         if progress:
# #             progress(
# #                 f"Query {index}/{len(queries)} · "
# #                 f"{query['level']} · {document_id}"
# #             )

# #         if cache_key not in ranking_cache:
# #             entry = {
# #                 "initial": {
# #                     "status": "not_run",
# #                     "hits": [],
# #                     "error": None,
# #                 },
# #                 "reranked": {
# #                     "status": "not_run",
# #                     "hits": [],
# #                     "error": None,
# #                 },
# #                 "parsed_claim_used": False,
# #             }

# #             try:
# #                 initial_hits = list(
# #                     vector_store.search(
# #                         text,
# #                         top_k=candidate_depth,
# #                         document_id=document_id,
# #                     )
# #                 )

# #                 entry["initial"].update(
# #                     status="ok",
# #                     hits=initial_hits,
# #                 )

# #             except Exception as exc:
# #                 entry["initial"].update(
# #                     status="error",
# #                     error=str(exc),
# #                 )

# #                 entry["reranked"].update(
# #                     status="blocked",
# #                     error="Initial retrieval failed.",
# #                 )

# #             if entry["initial"]["status"] == "ok":
# #                 try:
# #                     initial_hits = entry["initial"]["hits"]
# #                     parsed_claim = None

# #                     if use_claim_parser and initial_hits:
# #                         parsed = parse_claims(text)

# #                         # A compound query has multiple structures.
# #                         # Do not apply the first atom's metadata
# #                         # to the complete compound claim.
# #                         if (
# #                             isinstance(parsed, list)
# #                             and len(parsed) == 1
# #                             and isinstance(parsed[0], dict)
# #                         ):
# #                             parsed_claim = parsed[0]

# #                     entry["parsed_claim_used"] = (
# #                         parsed_claim is not None
# #                     )

# #                     reranked_hits = reranker.rerank(
# #                         text,
# #                         initial_hits,
# #                         keep=max(10, len(initial_hits)),
# #                         parsed_claim=parsed_claim,
# #                     )

# #                     entry["reranked"].update(
# #                         status="ok",
# #                         hits=list(reranked_hits),
# #                     )

# #                 except Exception as exc:
# #                     entry["reranked"].update(
# #                         status="error",
# #                         error=str(exc),
# #                     )

# #             ranking_cache[cache_key] = entry

# #         entry = ranking_cache[cache_key]

# #         query["parsed_claim_used"] = (
# #             entry["parsed_claim_used"]
# #         )

# #         for stage in STAGES:
# #             result = entry[stage]

# #             query["stages"][stage]["status"] = result["status"]
# #             query["stages"][stage]["error"] = result["error"]

# #             if result["status"] != "ok":
# #                 continue

# #             ranked = _ranked_hits(
# #                 result["hits"],
# #                 document_id,
# #                 chunk_maps[document_id],
# #             )

# #             query["stages"][stage]["ranked_hits"] = ranked
# #             query["stages"][stage]["metrics"] = _metrics(
# #                 ranked,
# #                 gold,
# #                 document_id,
# #                 candidate_depth,
# #             )

# #         has_error = any(
# #             entry[stage]["status"] == "error"
# #             for stage in STAGES
# #         )

# #         if has_error and stop_on_error:
# #             break

# #     summaries = _summaries(queries)

# #     incomplete = any(
# #         summary["failed_queries"]
# #         or summary["blocked_queries"]
# #         or summary["not_run_queries"]
# #         for summary in summaries
# #     )

# #     timestamp = datetime.now(timezone.utc)
# #     report_id = (
# #         timestamp.strftime("%Y%m%d_%H%M%S")
# #         + "_"
# #         + uuid.uuid4().hex[:8]
# #     )

# #     result_dir = dataset_dir / "retrieval_results"
# #     report_path = result_dir / f"{report_id}.json"

# #     report = {
# #         "report_id": report_id,
# #         "dataset_id": manifest["dataset_id"],
# #         "dataset_revision": manifest.get("revision"),
# #         "created_at": timestamp.isoformat(),
# #         "complete": not incomplete,
# #         "report_path": str(report_path),
# #         "configuration": {
# #             "candidate_depth": candidate_depth,
# #             "metric_depth": candidate_depth,
# #             "rerank_keep": "all returned candidates",
# #             "use_claim_parser": use_claim_parse,
# #             "stop_on_error": stop_on_error,
# #             "reranker_model": reranker.RERANK_MODEL,
# #             "matching": (
# #                 "document_id plus original source_evidence_id"
# #             ),
# #             "ranking_unit": "chunk",
# #             "gold_unit": "source evidence item",
# #             "averaging": (
# #                 "Macro average over successfully completed "
# #                 "eligible queries, separately for each stage/level."
# #             ),
# #             "mrr_definition": (
# #                 f"Reciprocal first-gold rank within top "
# #                 f"{candidate_depth}; zero when absent."
# #             ),
# #             "subclaim_scope": (
# #                 "Individual atoms of compound claims."
# #             ),
# #             "unknown_policy": (
# #                 "UNKNOWN context citations are not gold. "
# #                 "Queries without resolved gold evidence are excluded."
# #             ),
# #         },
# #         "dataset_checksums": {
# #             "claims_sha256": manifest["claims_sha256"],
# #             "ground_truth_sha256": (
# #                 manifest["ground_truth_sha256"]
# #             ),
# #         },
# #         "chunk_file_checksums": chunk_hashes,
# #         "summary": summaries,
# #         "queries": queries,
# #     }

# #     _write_json(report_path, report)
# #     return report