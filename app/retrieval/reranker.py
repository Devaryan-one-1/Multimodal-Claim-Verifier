from sentence_transformers import CrossEncoder

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
METADATA_BOOST = 0.1          # added per matching metadata field
RELEVANCE_BAR = 0.0          # cross-encoder logit above this = "relevant"
DEFAULT_KEEP = 5

_model = None

def _get_model():
    global _model
    if _model is None:
        _model = CrossEncoder(RERANK_MODEL)
    return _model

def rerank(claim, hits, keep = DEFAULT_KEEP, parsed_claim = None):
    if not hits:
        return []
    model = _get_model()
    # pairs = [(claim, h.get("text") or "") for h in hits]
    pairs = [(claim, _evidence_text(h)) for h in hits]
    scores = model.predict(pairs)
    scored = []

    for h, base in zip(hits, scores):
        boost = _metadata_boost(h, parsed_claim)
        h = dict(h)
        h["rerank_score"] = float(base) + boost
        h["_relevant"] = float(base) > RELEVANCE_BAR
        scored.append(h)

    scored.sort(key = lambda x : x["rerank_score"], reverse = True)
    if scored and scored[0]["_relevant"]:
        return _diverse_top(scored, keep)
    else:
        return scored[:keep]

def _metadata_boost(hit, parsed_claim):
    if not parsed_claim:
        return 0.0
    boost = 0.0
    text = (hit.get("text") or "").lower()

    for field in ("start_period", "end_period"):
        val = parsed_claim.get(field)
        if val and str(val) in text:
            boost += METADATA_BOOST
    return boost

def _diverse_top(scored, keep):
    chosen = []
    seen_types = set()

    for h in scored:
        if len(chosen) >= keep:
            break
        t = h.get("type")
        if t not in seen_types:
            chosen.append(h)
            seen_types.add(t)

    if len(chosen) < keep:
        for h in scored:
            if len(chosen) >= keep:
                break
            if h not in chosen:
                chosen.append(h)

    chosen.sort(key= lambda x : x["rerank_score"], reverse=True)
    return chosen[:keep]

def _evidence_text(h):
    parts = []

    if h.get("text"):
        parts.append(str(h["text"]))

    headers = h.get("headers") or []
    if headers:
        parts.append(
            " | ".join(str(x) for x in headers)
        )

    rows = h.get("rows") or []
    for row in rows:
        parts.append(
            " | ".join(str(x) for x in row)
        )

    return "\n".join(parts)

if __name__ == "__main__":
    import sys
    from vector_store import search

    claim = sys.argv[1] if len(sys.argv) > 1 else "revenue growth in 2025"
    doc = sys.argv[2] if len(sys.argv) > 2 else "Apple_Financial_10_K"

    hits = search(claim, top_k=20, document_id=doc)
    top = rerank(claim, hits, keep=5)

    print(f'claim: "{claim}" \nreranked top {len(top)}:\n')
    for i, h in enumerate(top, 1):
        print(f"{i}. Page {h['page']} - {h['type'].capitalize()}" 
              f"(rerank {h['rerank_score']:.3f})")
        
