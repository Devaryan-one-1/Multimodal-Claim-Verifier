"""Restore dashboard readiness from a validated existing document/index.

Put this file beside app/ and data/, stop Streamlit, then run:
    python repair_document_readiness.py

No extraction, embedding generation, or LLM requests are performed.
Uses the resumable retrieval evaluator already installed in this project.
Only document.json and a backup of that file are written by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DOCUMENT = "0000066740-26-000014-56e1ef4130"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", default=DEFAULT_DOCUMENT)
    parser.add_argument("--company", default=None)
    args = parser.parse_args()
    document_id = args.document_id
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", document_id) or document_id in {".", ".."}:
        raise ValueError("Invalid document ID.")
    if not (ROOT / "app").is_dir() or not (ROOT / "data").is_dir():
        raise ValueError("Put this script in the project root, beside app/ and data/.")

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "app"))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import chromadb
    import pymupdf
    from evaluation.retrieval_evaluation import (
        _build_queries,
        _configuration,
        _load_chunk_maps,
        _load_dataset,
        _write_json,
    )
    from retrieval import embeddings

    folder = ROOT / "data" / "processed" / document_id
    metadata_path = folder / "document.json"
    pdf_path = ROOT / "data" / "documents" / document_id / "original.pdf"
    dataset_dir = ROOT / "data" / "evaluation"
    required = [
        metadata_path, pdf_path, folder / "pages.json", folder / "evidence.json",
        folder / "chunks.json", folder / "index.json",
        dataset_dir / "manifest.json", dataset_dir / "claims.jsonl",
        dataset_dir / "ground_truth.jsonl",
    ]
    for path in required:
        if not path.is_file():
            raise ValueError(f"Required existing file is missing: {path}")
    starting_hashes = {path: sha256(path) for path in required}
    original = metadata_path.read_bytes()
    metadata = read_json(metadata_path)
    if metadata.get("document_id") != document_id:
        raise ValueError("document.json belongs to a different document.")

    # Reuse the same reviewed-source checks as retrieval evaluation.
    # These helper calls do not invoke a parser, reranker, or embedding API.
    claims, truths, manifest = _load_dataset(dataset_dir)
    if not manifest.get("source_hashes"):
        raise ValueError("The dataset manifest has no reviewed source checksums.")
    protected = {
        (ROOT / Path(name)).resolve() if not Path(name).is_absolute()
        else Path(name).resolve()
        for name in manifest["source_hashes"]
    }
    if metadata_path.resolve() in protected:
        raise ValueError(
            "document.json is itself a reviewed source in this dataset. "
            "Its checksum must not be changed by this repair."
        )
    for source in (pdf_path, folder / "evidence.json"):
        if source.resolve() not in protected:
            raise ValueError(f"The reviewed manifest does not identify this source: {source}")

    queries = [q for q in _build_queries(claims, truths)
               if q["document_id"] == document_id]
    if not queries:
        raise ValueError("This document is not part of the exported evaluation dataset.")
    chunk_maps, chunk_file_hashes = _load_chunk_maps(queries)
    if document_id not in chunk_maps:
        raise ValueError("No reviewed gold-evidence queries exist for this document.")
    chunks = list(chunk_maps[document_id].values())
    index = read_json(folder / "index.json")
    if index.get("document_id") != document_id:
        raise ValueError("index.json belongs to a different document.")
    expected_hash = text_hash(str([
        (chunk["id"], text_hash(chunk["text"])) for chunk in chunks
    ]))
    if index.get("chunk_count") != len(chunks):
        raise ValueError("index.json chunk_count does not match the existing chunks.json.")
    if index.get("chunks_hash") != expected_hash:
        raise ValueError("index.json chunks_hash does not match the existing chunks.json.")

    # Require an existing collection. Do not create a replacement collection.
    client = chromadb.PersistentClient(path=embeddings.CHROMA_DIR)
    store = client.get_collection(
        name=embeddings.COLLECTION_NAME, embedding_function=None,
    )
    stored = store.get(where={"document_id": document_id}, include=["metadatas"])
    if set(stored["ids"]) != set(chunk_maps[document_id]):
        raise ValueError(
            "The existing collection has missing or stale chunk IDs. "
            "Resume indexing from existing chunks before repairing readiness."
        )
    _, _, _, guarded_hashes = _configuration(
        chunk_maps, chunk_file_hashes, manifest, 20, False,
    )

    pages = read_json(folder / "pages.json").get("pages")
    evidence = read_json(folder / "evidence.json").get("evidence")
    if not isinstance(pages, list) or not pages:
        raise ValueError("pages.json must contain a nonempty pages list.")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence.json must contain a nonempty evidence list.")
    source_ids = [item["id"] for item in evidence]
    source_id_set = set(source_ids)
    if len(source_id_set) != len(source_ids):
        raise ValueError("evidence.json contains duplicate evidence IDs.")
    if any(chunk.get("source_evidence_id") not in source_id_set for chunk in chunks):
        raise ValueError("A chunk refers to missing original evidence.")
    with pymupdf.open(str(pdf_path)) as pdf:
        if pdf.needs_pass:
            raise ValueError("The PDF requires a password.")
        page_count = pdf.page_count
    if len(pages) != page_count or metadata.get("page_count", page_count) != page_count:
        raise ValueError("PDF, pages.json, and document.json page counts disagree.")
    if sorted(int(page["page_number"]) for page in pages) != list(range(1, page_count + 1)):
        raise ValueError("pages.json does not cover every PDF page exactly once.")

    repaired = dict(metadata)
    repaired.update(
        status="ready",
        profile="dashboard-v1",
        filename=metadata.get("filename") or f"{document_id}.pdf",
        company_name=(
            args.company if args.company is not None
            else metadata.get("company_name") or
            ("3M Company" if document_id == DEFAULT_DOCUMENT else "")
        ),
        pdf_path=str(pdf_path.relative_to(ROOT)),
        pdf_sha256=starting_hashes[pdf_path],
        fingerprint=starting_hashes[pdf_path],
        size_bytes=pdf_path.stat().st_size,
        page_count=page_count,
        text_extracted=any(str(page.get("text") or page.get("Text") or "").strip()
                           for page in pages),
        evidence_count=len(evidence),
        chunk_count=len(chunks),
        table_count=sum(item.get("type") == "table" for item in evidence),
        chart_count=sum(item.get("type") == "chart" for item in evidence),
        ocr_page_count=len({item.get("page") for item in evidence
                            if item.get("type") == "ocr"}),
    )
    repaired.setdefault("warnings", [])
    repaired.pop("error", None)
    repaired["readiness_repair"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "method": "adopt_validated_existing_index",
        "collection": index["collection"],
        "embedding_model": index["embedding_model"],
        "dimensions": index["dimensions"],
        "chunks_hash": expected_hash,
        "note": "Restored readiness from existing reviewed sources and index; "
                "no extraction, OCR, chart generation, or embedding requests performed.",
    }

    # Refuse to write if inputs changed while this command was validating them.
    for path, expected in starting_hashes.items():
        if sha256(path) != expected:
            raise ValueError(f"An input changed during validation: {path}")
    for filename, expected in guarded_hashes.items():
        if sha256(Path(filename)) != expected:
            raise ValueError(f"An evaluated source or configuration changed: {filename}")
    backup = metadata_path.with_name(
        "document.before-readiness-"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "-" + uuid.uuid4().hex[:8] + ".json"
    )
    backup.write_bytes(original)
    _write_json(metadata_path, repaired)
    print(f"Ready: {document_id}")
    print(f"Validated {len(chunks)} existing indexed chunks and {page_count} pages.")
    print(f"Updated: {metadata_path}")
    print(f"Backup: {backup}")
    print("Restart Streamlit, then rerun final-verdict evaluation using cache reuse.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Readiness repair failed: {exc}", file=sys.stderr)
        print("The readiness metadata was not promoted by a failed validation.", file=sys.stderr)
        raise SystemExit(1)

