from __future__ import annotations

import re
import shutil
from pathlib import Path

from retrieval.embeddings import collection
from retrieval.vector_store import _bm25_cache
from services.runtime import read, write


def persist_document_labels(documents: list[dict]):
    for document in documents:
        path = Path("data/processed") / document["document_id"] / "document.json"
        if not path.exists():
            continue
        saved = read(path)
        label = document.get("company_name") or ""
        if label != (saved.get("company_name") or ""):
            saved["company_name"] = label
            write(path, saved)


def delete_document(document_id: str):
    """Called only by the upload UI's confirmed permanent-delete action."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", document_id) or document_id in {".", ".."}:
        raise ValueError("Invalid document ID.")
    store = collection()
    ids = store.get(where={"document_id": document_id}, include=[])["ids"]
    if ids:
        store.delete(ids=ids)
    for directory in ("documents", "processed", "pages", "tables", "charts"):
        root = (Path("data") / directory).resolve()
        target = root / document_id
        if target.is_symlink():
            raise ValueError("Refusing to delete a symbolic-link document directory.")
        if target.exists():
            shutil.rmtree(target)
    _bm25_cache.pop(document_id, None)
