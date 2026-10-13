from __future__ import annotations

import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from retrieval.embeddings import collection, embed_query
from services.runtime import read

PROCESSED_ROOT = "data/processed"
DEFAULT_K = 20
CANDIDATE_POOL = 50
RRF_K = 60
_bm25_cache = {}


def _tokenize(text):
    return re.findall(r"[\w]+", str(text).casefold())


def _load_bm25(document_id):
    path = Path(PROCESSED_ROOT) / document_id / "chunks.json"
    stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    cached = _bm25_cache.get(document_id)
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    chunks = read(path)["chunks"]
    if not chunks:
        return None, []
    corpus = [_tokenize(c.get("text", "")) or ["_empty_"] for c in chunks]
    bm25 = BM25Okapi(corpus)
    _bm25_cache[document_id] = (stamp, bm25, chunks)
    return bm25, chunks


def search(query: str, top_k: int = DEFAULT_K, document_id: str | None = None) -> list[dict]:
    if not document_id:
        raise ValueError("An explicit document ID is required for retrieval.")
    bm25, chunks = _load_bm25(document_id)
    if not chunks:
        return []
    store = collection()
    existing = store.get(where={"document_id": document_id}, include=[])["ids"]
    if not existing:
        raise RuntimeError("This document has no vector index. Reprocess it in the dashboard.")
    available = set(existing)
    missing = {c["id"] for c in chunks} - available
    if missing:
        raise RuntimeError("The document index is incomplete. Reprocess it before verification.")
    result = store.query(
        query_embeddings=[embed_query(query)], where={"document_id": document_id},
        n_results=min(CANDIDATE_POOL, len(existing)), include=["metadatas"],
    )
    vector_ids = result["ids"][0]
    scores = bm25.get_scores(_tokenize(query))
    keyword_ids = [c["id"] for c, _ in sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)[:CANDIDATE_POOL]]
    fused = {}
    for ranking in (vector_ids, keyword_ids):
        for rank, cid in enumerate(ranking, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1 / (RRF_K + rank)
    by_id = {c["id"]: c for c in chunks}
    hits = []
    for cid, score in sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:top_k]:
        if cid not in by_id:
            continue
        hit = dict(by_id[cid])
        hit.update(chunk_id=cid, score=score, injected_table=False)
        hits.append(hit)
    return hits

# import os
# import json

# from google import genai
# from google.genai import types
# import chromadb
# from rank_bm25 import BM25Okapi

# PROCESSED_ROOT = "data/processed"
# CHROMA_DIR = "data/chroma"
# COLLECTION_NAME = "evidence"
# EMBED_MODEL = "gemini-embedding-001"   # MUST match embeddings.py

# DEFAULT_K = 10
# CANDIDATE_POOL = 50          # how many to pull from each retriever before fusing
# RRF_K = 60                   # reciprocal-rank-fusion constant (standard default)
# TABLE_REL_THRESHOLD = 0.8    # inject a table if its score >= 0.8 * top score

# def _client():
#     key = os.environ.get("GOOGLE_API_KEY")
#     if not key:
#         raise RuntimeError("Google Api key not found")
#     return genai.Client(api_key=key)

# _bm25_cache = {}

# def _load_bm25(document_id):
#     if document_id in _bm25_cache:
#         return _bm25_cache[document_id]

#     path = os.path.join(PROCESSED_ROOT, document_id, "chunks.json")
#     with open(path, encoding="utf-8") as f:
#         chunks = json.load(f)["chunks"]

#     corpus = []
#     meta = []
#     for c in chunks:
#         # text = c.get("text") or ""
#         text = _chunk_search_text(c)
#         corpus.append(_tokenize(text))
#         # meta.append({
#         #     "chunk_id": c["id"],
#         #     "page": c["page"],
#         #     "type": c["type"],
#         #     "text": text,
#         #     "source_evidence_id": c.get("source_evidence_id"),
#         #     "table_id": c.get("table_id"),
#         # })
#         meta.append({
#             "chunk_id": c["id"],
#             "page": c["page"],
#             "type": c["type"],
#             "text": text,
#             "source_evidence_id": c.get("source_evidence_id"),
#             "table_id": c.get("table_id"),

#             # Preserve table structure
#             "headers": c.get("headers"),
#             "rows": c.get("rows"),
#             "column_map": c.get("column_map"),

#             # Preserve any additional table metadata
#             "metadata": c.get("metadata"),
#         })

#     bm25 = BM25Okapi(corpus)
#     _bm25_cache[document_id] = (bm25, meta)
#     return bm25, meta


# def _tokenize(text):
#     # simple lowercase word split; keeps tokens like "2025" and "13(a)" mostly intact
#     return text.lower().split()


# # ---------- the two retrievers ----------
# def _vector_rank(client, query, document_id, pool):
#     """Return an ordered list of chunk_ids from vector search, best first."""
#     resp = client.models.embed_content(
#         model=EMBED_MODEL,
#         contents=query,
#         config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
#     )
#     qvec = resp.embeddings[0].values

#     chroma = chromadb.PersistentClient(path=CHROMA_DIR)
#     collection = chroma.get_collection(name=COLLECTION_NAME)
#     where = {"document_id": document_id} if document_id else None

#     res = collection.query(
#         query_embeddings=[qvec],
#         n_results=pool,
#         where=where,
#         include=["metadatas"],
#     )
#     return res["ids"][0]


# def _bm25_rank(query, document_id, pool):
#     """Return an ordered list of chunk_ids from BM25, best first."""
#     bm25, meta = _load_bm25(document_id)
#     scores = bm25.get_scores(_tokenize(query))
#     # pair each chunk with its score, sort high to low, take pool
#     ranked = sorted(zip(meta, scores), key=lambda x: x[1], reverse=True)
#     return [m["chunk_id"] for m, s in ranked[:pool]], {m["chunk_id"]: m for m in meta}


# # ---------- fusion ----------
# def _rrf_fuse(vector_ids, bm25_ids):
#     """
#     Reciprocal Rank Fusion: combine two rankings by summing 1/(k+rank).
#     Robust because it uses *rank position*, not raw scores — so vector
#     distances and BM25 scores (different scales) never need to be comparable.
#     """
#     scores = {}
#     for rank, cid in enumerate(vector_ids):
#         scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank + 1)
#     for rank, cid in enumerate(bm25_ids):
#         scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank + 1)
#     # sorted best first
#     return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# # ---------- public API ----------
# def search(query: str, top_k: int = DEFAULT_K, document_id: str = None) -> list:
#     client = _client()

#     vector_ids = _vector_rank(client, query, document_id, CANDIDATE_POOL)
#     bm25_ids, meta_by_id = _bm25_rank(query, document_id, CANDIDATE_POOL)

#     fused = _rrf_fuse(vector_ids, bm25_ids)  # list of (chunk_id, score)

#     # build the top-K
#     hits = []
#     for cid, score in fused[:top_k]:
#         m = meta_by_id.get(cid)
#         if not m:
#             continue
#         hits.append(_hit(m, score))

#     # --- table guarantee (option B: relative threshold, injected as K+1) ---
#     if fused:
#         top_score = fused[0][1]
#         already = {h["chunk_id"] for h in hits}
#         for cid, score in fused:
#             m = meta_by_id.get(cid)
#             if not m or m["type"] != "table":
#                 continue
#             if cid in already:
#                 break  # a table is already in the visible set; nothing to inject
#             if score >= TABLE_REL_THRESHOLD * top_score:
#                 hits.append(_hit(m, score, injected=True))
#                 break  # inject the single best qualifying table
#             else:
#                 break  # best table doesn't clear the bar; stop
#     return hits


# # def _hit(m, score, injected=False):
# #     return {
# #         "chunk_id": m["chunk_id"],
# #         "page": m["page"],
# #         "type": m["type"],
# #         "score": round(score, 4),
# #         "source_evidence_id": m.get("source_evidence_id"),
# #         "table_id": m.get("table_id"),
# #         "text": m["text"],
# #         "injected_table": injected,
# #     }
# def _chunk_search_text(c):
#     parts = []

#     text = c.get("text")
#     if text:
#         parts.append(str(text))

#     headers = c.get("headers") or []
#     if headers:
#         parts.append(" | ".join(str(x) for x in headers))

#     rows = c.get("rows") or []
#     for row in rows:
#         parts.append(" | ".join(str(x) for x in row))

#     return "\n".join(parts)

# def _hit(m, score, injected=False):
#     return {
#         "chunk_id": m["chunk_id"],
#         "page": m["page"],
#         "type": m["type"],
#         "score": round(score, 4),
#         "source_evidence_id": m.get("source_evidence_id"),
#         "table_id": m.get("table_id"),
#         "text": m.get("text", ""),
#         "injected_table": injected,

#         # IMPORTANT: pass table structure through
#         "headers": m.get("headers") or [],
#         "rows": m.get("rows") or [],
#         "column_map": m.get("column_map") or [],
#         "metadata": m.get("metadata"),
#     }

# if __name__ == "__main__":
#     import sys

#     query = sys.argv[1] if len(sys.argv) > 1 else "revenue growth in 2025"
#     doc = sys.argv[2] if len(sys.argv) > 2 else "Apple_Financial_10_K"
#     hits = search(query, document_id=doc)

#     print(f'query: "{query}"\n')
#     for i, h in enumerate(hits, 1):
#         tag = " [injected table]" if h["injected_table"] else ""
#         label = h["type"].capitalize()
#         print(f"{i}. Page {h['page']} — {label} (score {h['score']}){tag}")