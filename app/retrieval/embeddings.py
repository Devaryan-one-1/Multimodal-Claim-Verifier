from __future__ import annotations

import os

import chromadb
from google.genai import types

from services.runtime import client, digest, normalize, read, retry, write

CHROMA_DIR = "data/chroma"
PROCESSED_ROOT = "data/processed"
EMBED_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
DIMENSIONS = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "768"))
COLLECTION_NAME = "evidence_v2_" + digest(f"{EMBED_MODEL}:{DIMENSIONS}")[:12]
BATCH_SIZE = 16


def collection():
    return chromadb.PersistentClient(path=CHROMA_DIR).get_or_create_collection(name=COLLECTION_NAME)


def embed_query(query: str) -> list[float]:
    api = client()
    result = retry(lambda: api.models.embed_content(
        model=EMBED_MODEL, contents=query,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=DIMENSIONS),
    ))
    return normalize(result.embeddings[0].values)


def embed_document(document_id: str, reporter=None) -> int:
    chunks = read(f"{PROCESSED_ROOT}/{document_id}/chunks.json")["chunks"]
    if not chunks:
        raise ValueError("No searchable evidence chunks were extracted.")
    store = collection()
    existing = store.get(where={"document_id": document_id}, include=["metadatas"])
    hashes = {cid: (meta or {}).get("content_hash") for cid, meta in zip(existing["ids"], existing["metadatas"])}
    pending = [c for c in chunks if hashes.get(c["id"]) != digest(c["text"])]
    api = client() if pending else None
    completed = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        texts = [c["text"][:12000] for c in batch]
        response = retry(lambda: api.models.embed_content(
            model=EMBED_MODEL, contents=texts,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=DIMENSIONS),
        ))
        vectors = [normalize(e.values) for e in response.embeddings]
        if len(vectors) != len(batch):
            raise ValueError("Embedding provider returned the wrong batch size.")
        store.upsert(
            ids=[c["id"] for c in batch], embeddings=vectors, documents=texts,
            metadatas=[{
                "document_id": document_id, "page": int(c["page"]), "type": c["type"],
                "source_evidence_id": c["source_evidence_id"],
                "content_hash": digest(c["text"]),
            } for c in batch],
        )
        completed += len(batch)
        if reporter:
            reporter.log(f"Indexed {completed}/{len(pending)} new or changed chunks")
    wanted = {c["id"] for c in chunks}
    stale = [cid for cid in existing["ids"] if cid not in wanted]
    if stale:
        store.delete(ids=stale)
    write(f"{PROCESSED_ROOT}/{document_id}/index.json", {
        "document_id": document_id, "collection": COLLECTION_NAME,
        "embedding_model": EMBED_MODEL, "dimensions": DIMENSIONS,
        "chunk_count": len(chunks), "chunks_hash": digest(str([(c["id"], digest(c["text"])) for c in chunks])),
    })
    return len(chunks)

# import os
# import json
# import time

# from google import genai
# from google.genai import types
# import chromadb

# PROCESSED_ROOT = "data/processed"
# CHROMA_DIR = "data/chroma"                 # Chroma persists here
# COLLECTION_NAME = "evidence"

# EMBED_MODEL = "models/gemini-embedding-001"  # Gemini embedding model
# DESC_MODEL = "models/gemini-flash-latest"     # for table descriptions

# TEXT_TYPES = {"text", "heading", "caption", "footnote"}

# def _client():
#     key = os.environ.get("GOOGLE_API_KEY")
#     if not key:
#         raise RuntimeError(
#             "Google API key not set get key and set it up in .env"
#         )
#     return genai.Client(api_key=key)
# BATCH_SIZE = 20   # texts per embed request; keeps us under 100/min

# def embed_document(document_id: str) -> int:
#     client = _client()

#     doc_dir = os.path.join(PROCESSED_ROOT, document_id)
#     chunks = _load(doc_dir, "chunks.json")["chunks"]

#     chroma = chromadb.PersistentClient(path=CHROMA_DIR)
#     collection = chroma.get_or_create_collection(name=COLLECTION_NAME)

#     # find what's already embedded so we can resume without redoing work
#     existing = set()
#     try:
#         got = collection.get(include=[])   # returns ids only
#         existing = set(got["ids"])
#     except Exception:
#         pass
#     # 1. build the list of (chunk, text_to_embed) first
#     to_embed = []
#     # for chunk in chunks:
#     #     if chunk["type"] == "table":
#     #         text = _table_description(client, chunk)
#     #     elif chunk["type"] in TEXT_TYPES:
#     #         text = chunk["text"]
#     #     else:
#     #         continue
#     #     if text.strip():
#     #         to_embed.append((chunk, text))

#     for chunk in chunks:
#         if chunk["id"] in existing:
#             continue                      # already embedded, skip
#         if chunk["type"] == "table":
#             text = _table_description(client, chunk)
#         elif chunk["type"] in TEXT_TYPES:
#             text = chunk["text"]
#         else:
#             continue
#         if text.strip():
#             to_embed.append((chunk, text))
#     if not to_embed:
#         print("nothing to embed")
#         return 0

#     # 2. embed in batches
#     ids, embeddings, documents, metadatas = [], [], [], []
#     for start in range(0, len(to_embed), BATCH_SIZE):
#         batch = to_embed[start:start + BATCH_SIZE]
#         texts = [t for _, t in batch]
#         vectors = _embed_batch_with_retry(client, texts)

#         for (chunk, text), vector in zip(batch, vectors):
#             ids.append(chunk["id"])
#             embeddings.append(vector)
#             documents.append(text)
#             metadatas.append({
#                 "document_id": chunk["document_id"],
#                 "page": chunk["page"],
#                 "type": chunk["type"],
#                 "source_evidence_id": chunk.get("source_evidence_id", ""),
#                 "table_id": chunk.get("table_id", "") or "",
#             })
#         print(f"  embedded {len(ids)}/{len(to_embed)}")

#     collection.upsert(
#         ids=ids, embeddings=embeddings,
#         documents=documents, metadatas=metadatas,
#     )
#     print(f"embedded and stored {len(ids)} chunks in {CHROMA_DIR}")
#     return len(ids)


# def _embed_batch_with_retry(client, texts, retries=6):
#     for attempt in range(retries):
#         try:
#             resp = client.models.embed_content(
#                 model=EMBED_MODEL,
#                 contents=texts,   # a list -> batch embed
#                 config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
#             )
#             return [e.values for e in resp.embeddings]
#         except Exception as e:
#             if attempt == retries - 1:
#                 raise
#             wait = _retry_delay(e)
#             print(f"  batch retry {attempt + 1}, waiting {wait}s: rate limited")
#             time.sleep(wait)
#     return []


# def _retry_delay(error, default=30):
#     """Pull the server's suggested wait out of the 429 message, else default."""
#     text = str(error)
#     marker = "retryDelay"
#     if marker in text:
#         # find the number of seconds after retryDelay
#         after = text.split(marker, 1)[1]
#         digits = ""
#         for ch in after:
#             if ch.isdigit():
#                 digits += ch
#             elif digits:
#                 break
#         if digits:
#             return int(digits) + 2   # small cushion
#     return default

# # def _table_description(client, chunk):
# #     headers = chunk.get("headers") or []
# #     rows = chunk.get("rows") or []
# #     row_labels = [r[0] for r in rows if r]

# #     prompt = (
# #         "Write one short sentence describing what this financial table contains, "
# #         "for search indexing. Describe the subject and columns; do NOT list all the numbers.\n\n"
# #         f"Columns: {headers}\n"
# #         f"Row labels: {row_labels}\n"
# #     )
# #     try:
# #         resp = client.models.generate_content(model=DESC_MODEL, contents=prompt)
# #         desc = (resp.text or "").strip()
# #         if desc:
# #             return f"{desc} Columns: {', '.join(map(str, headers))}."
# #     except Exception as e:
# #         print(f"  table description failed, using fallback: {e}")

# #     return (f"Table with columns {', '.join(map(str, headers))}. "
# #             f"Rows: {', '.join(map(str, row_labels))}.")

# # def _table_description(client, chunk):
# #     headers = chunk.get("headers") or []
# #     rows = chunk.get("rows") or []
# #     row_labels = [r[0] for r in rows if r]
# #     return (f"Table with columns {', '.join(map(str, headers))}. "
# #             f"Rows: {', '.join(map(str, row_labels))}.")

# def _table_description(client,chunk):
#     """
#     Build a retrievable description from row labels + recovered period columns.
#     Uses the clean recovered years/periods, not the messy extracted header row.
#     """
#     rows = chunk.get("rows") or []
#     row_labels = [r[0] for r in rows if r][:12]     # what the table is about
#     ch = chunk.get("column_headers") or {}
#     years = ch.get("years") or []
#     ptypes = ch.get("period_types") or []

#     period_desc = ""
#     if years:
#         yrs = ", ".join(str(y) for y in sorted(set(years)))
#         period_desc = f" Periods: {yrs}"
#         if ptypes:
#             period_desc += " (" + ", ".join(p.replace("_", " ") for p in ptypes) + ")"

#     return (f"Financial table. Line items: {', '.join(map(str, row_labels))}."
#             f"{period_desc}")

# def _load(doc_dir, filename):
#     path = os.path.join(doc_dir, filename)
#     with open(path, encoding="utf-8") as f:
#         return json.load(f)


# if __name__ == "__main__":
#     import sys
#     count = embed_document(sys.argv[1])
#     print(f"done: {count} vectors")