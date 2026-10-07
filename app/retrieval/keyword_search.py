from vector_store import _load_bm25, _bm25_rank, _tokenize

def keyword_search(query: str, document_id: str = None, pool: int = 50):
    """Return BM25-ranked chunk_ids (best first) and an id->metadata map."""
    return _bm25_rank(query, document_id, pool)


__all__ = ["keyword_search", "_load_bm25", "_bm25_rank", "_tokenize"]