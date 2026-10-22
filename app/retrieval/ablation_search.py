"""Read-only, modality-filtered retrieval over the existing embedding index.

No extraction, document embedding or source-file mutation happens here.
"""
from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from rank_bm25 import BM25Okapi

from retrieval.embeddings import collection, embed_query

VARIANTS = {
    "A": {"name": "Text only", "modalities": ("text",), "numerical": False},
    "B": {"name": "Text + tables", "modalities": ("text", "table"), "numerical": False},
    "C": {"name": "Text + tables + charts", "modalities": ("text", "table", "chart"), "numerical": False},
    "D": {"name": "Text + tables + charts + numerical verification", "modalities": ("text", "table", "chart"), "numerical": True},
}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tokens(text):
    return re.findall(r"\w+", str(text).casefold())


def _norm(text):
    return " ".join(_tokens(text))


def _box(item, pages):
    box = item.get("bbox") or item.get("location")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = map(float, box)
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
            return None
        page = pages.get(item.get("page"), {})
        space = item.get("bbox_space", "pdf_points")
        if space in {"image_pixels", "pixels"}:
            width, height = item.get("width_px"), item.get("height_px")
        elif space in {"normalized_1000", "0-1000"}:
            width, height = 1000, 1000
        elif space in {"normalized", "0-1"}:
            width, height = 1, 1
        elif space in {"pdf_points", "points"}:
            width = item.get("page_width_pt") or page.get("width")
            height = item.get("page_height_pt") or page.get("height")
        else:
            return None
        if not width or not height or float(width) <= 0 or float(height) <= 0:
            return None
        return x0 / float(width), y0 / float(height), x1 / float(width), y1 / float(height)
    except (TypeError, ValueError):
        return None


def _overlap(a, b):
    if not a or not b:
        return False
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return intersection / area >= 0.15


def _kind(item):
    original = str(item.get("type") or "").lower()
    hint = str(item.get("content_type") or item.get("layout_type") or item.get("region_type") or "").lower()
    if original == "table" or hint == "table" or item.get("rows"):
        return "table", "Structured table or explicit table classification"
    if original in {"chart", "figure", "graph"} or hint in {"chart", "figure", "graph"} or item.get("facts"):
        return "chart", "Chart evidence or explicit chart classification"
    if original == "ocr":
        if hint in {"text", "prose", "paragraph", "heading", "footnote", "caption"}:
            return "text", "OCR explicitly classified as prose"
        return "exclude", "OCR content is not classified; review text/table/chart in the overrides"
    if original in {"text", "paragraph", "heading", "caption", "footnote"}:
        if original == "caption" and re.search(r"\b(chart|graph|figure)\b", str(item.get("content") or ""), re.I):
            return "chart", "Caption explicitly identifies a chart or figure"
        return "text", "Text passage, heading, caption or footnote"
    return "exclude", "Unrecognized evidence type; review in the overrides"


def _safe_evidence(item, document_id, kind):
    # Whitelist fields: page_context, whole-page OCR, arbitrary metadata, and
    # cross-modal cached summaries never reach the verifier through hydration.
    kept = {key: copy.deepcopy(item[key]) for key in (
        "page", "bbox", "bbox_space", "page_width_pt", "page_height_pt",
        "page_rotation", "width_px", "height_px", "title", "content",
    ) if key in item}
    source_id = str(item["id"])
    kept.update(id=f"{document_id}::{source_id}", evidence_id=f"{document_id}::{source_id}",
                source_evidence_id=source_id, document_id=document_id, type=kind,
                original_type=item.get("type"), extraction_method="ocr" if item.get("type") == "ocr" else "native_or_vision")
    if kind == "table":
        for key in ("headers", "rows", "column_map", "table_id"):
            if key in item:
                kept[key] = copy.deepcopy(item[key])
        # Recovered table headings belong to the table, not the entire page.
        headings = item.get("column_headers") or {}
        kept["context"] = str(headings.get("raw") or "") if isinstance(headings, dict) else ""
    elif kind == "chart":
        kept["facts"] = [copy.deepcopy(fact) for fact in item.get("facts", [])
                         if isinstance(fact, dict) and fact.get("value_source") == "explicit_label"]
    kept["content"] = str(kept.get("content") or "")
    kept["text"] = kept["content"]
    return kept


class AblationCorpus:
    def __init__(self, document_ids, overrides=None):
        self.overrides = overrides or {}
        self.inventory = []
        self.chunks = {}
        self.evidence = {}
        self._views = {}
        for document_id in document_ids:
            self._load(document_id)

    def _load(self, document_id):
        folder = Path("data/processed") / document_id
        items = _read(folder / "evidence.json")["evidence"]
        pages = {p["page_number"]: p for p in _read(folder / "pages.json")["pages"]}
        chunks = _read(folder / "chunks.json")["chunks"]
        by_id = {str(item["id"]): item for item in items}
        if len(by_id) != len(items):
            raise ValueError(f"{document_id}: duplicate evidence IDs")
        structured = defaultdict(list)
        kinds = {}
        for sid, item in by_id.items():
            key = f"{document_id}::{sid}"
            kind, reason = _kind(item)
            if key in self.overrides:
                kind = self.overrides[key]
                if kind not in {"text", "table", "chart", "exclude"}:
                    raise ValueError(f"Invalid content classification for {key}")
                reason = "Explicit ablation content classification override"
            kinds[sid] = (kind, reason)
            if kind in {"table", "chart"}:
                structured[item.get("page")].append(item)
        for page_number, page in pages.items():
            for region in page.get("regions", []):
                if region.get("type") in {"table", "chart", "figure", "graph"}:
                    structured[page_number].append(dict(region, page=page_number, bbox_space="pdf_points"))
        excluded = set()
        for sid, item in by_id.items():
            kind, reason = kinds[sid]
            key = f"{document_id}::{sid}"
            if kind == "text" and key not in self.overrides:
                text = _norm(item.get("content") or "")
                for other in structured[item.get("page")]:
                    same_region = _overlap(_box(item, pages), _box(other, pages))
                    other_text = _norm(other.get("content") or "")
                    copied = len(text) >= 40 and text in other_text
                    uncertain_position = _box(other, pages) is None
                    if same_region or copied or uncertain_position:
                        kind, reason = "exclude", "Text overlaps or duplicates a table/chart region"
                        break
                # Without positions, a text/OCR record on a structured page
                # cannot safely be assumed to contain prose only.
                if kind == "text" and structured[item.get("page")] and _box(item, pages) is None:
                    kind, reason = "exclude", "Unpositioned text on a structured page requires content review"
            if kind == "exclude":
                excluded.add(sid)
            else:
                self.evidence[(document_id, sid)] = _safe_evidence(item, document_id, kind)
            self.inventory.append({"document_id": document_id, "source_evidence_id": sid,
                                   "override_key": key, "page": item.get("page"),
                                   "original_type": item.get("type"), "ablation_modality": kind,
                                   "reason": reason, "preview": str(item.get("content") or "")[:220]})
        selected = []
        seen = set()
        for chunk in chunks:
            cid, sid = str(chunk["id"]), str(chunk.get("source_evidence_id") or "")
            if cid in seen:
                raise ValueError(f"{document_id}: duplicate chunk ID")
            seen.add(cid)
            if sid not in by_id:
                raise ValueError(f"{document_id}: chunk source {sid} does not exist")
            if sid in excluded:
                continue
            item = self.evidence[(document_id, sid)]
            text = str(chunk.get("text") or "")
            # Never change text and silently reuse an embedding for another text.
            if text and text not in str(by_id[sid].get("content") or ""):
                raise ValueError(f"{cid}: chunk text is not present in its original source")
            if not text.strip():
                continue
            selected.append({"id": cid, "chunk_id": cid, "source_evidence_id": sid,
                             "document_id": document_id, "page": item.get("page"),
                             "type": item["type"], "text": text})
        self.chunks[document_id] = selected

    def coverage(self):
        rows = []
        for doc in self.chunks:
            counts = Counter(row["ablation_modality"] for row in self.inventory if row["document_id"] == doc)
            ocr = Counter(row["ablation_modality"] for row in self.inventory
                          if row["document_id"] == doc and row["original_type"] == "ocr")
            for modality in ("text", "table", "chart", "exclude"):
                rows.append({"document_id": doc, "modality": modality,
                             "evidence_items": counts[modality], "ocr_items": ocr[modality],
                             "indexed_chunks": sum(c["type"] == modality for c in self.chunks[doc])})
        return rows

    def search(self, query, document_id, modalities, *, top_k=20):
        key = (document_id, tuple(modalities))
        if key not in self._views:
            candidates = [c for c in self.chunks[document_id] if c["type"] in modalities]
            bm25 = BM25Okapi([_tokens(c["text"]) or ["_empty_"] for c in candidates]) if candidates else None
            self._views[key] = (candidates, bm25)
        candidates, bm25 = self._views[key]
        if not candidates:
            return []
        allowed = {c["id"] for c in candidates}
        # Chroma restricts the vector search to these IDs before finding top-k.
        # BM25 is also rebuilt over only the allowed corpus.
        result = collection().query(query_embeddings=[embed_query(query)],
                                    ids=sorted(allowed), where={"document_id": document_id},
                                    n_results=min(50, len(candidates)), include=["metadatas"])
        vector_ids = result["ids"][0]
        if not set(vector_ids).issubset(allowed):
            raise RuntimeError("Vector search returned an excluded modality; refusing this ablation run")
        scores = bm25.get_scores(_tokens(query))
        keyword_ids = [c["id"] for c, _ in sorted(zip(candidates, scores), key=lambda pair: (-pair[1], pair[0]["id"]))[:50]]
        fused = {}
        for ranking in (vector_ids, keyword_ids):
            for rank, cid in enumerate(ranking, 1):
                fused[cid] = fused.get(cid, 0.0) + 1 / (60 + rank)
        by_id = {c["id"]: c for c in candidates}
        return [dict(by_id[cid], score=score, injected_table=False)
                for cid, score in sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))[:top_k]]

    def hydrate(self, document_id, hits, modalities):
        output, seen = [], set()
        for hit in hits:
            sid = str(hit["source_evidence_id"])
            item = self.evidence.get((document_id, sid))
            if item is None or item["type"] not in modalities:
                raise ValueError("Reranking referenced an excluded or unknown source")
            if sid not in seen:
                output.append(copy.deepcopy(item))
                seen.add(sid)
        return output
