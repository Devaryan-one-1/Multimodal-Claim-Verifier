from vector_store import search as hybrid_search

__all__ = ["hybrid_search"]


if __name__ == "__main__":
    import sys, json
    query = sys.argv[1] if len(sys.argv) > 1 else "revenue growth in 2025"
    doc = sys.argv[2] if len(sys.argv) > 2 else "Apple_Financial_10_K"
    for i, h in enumerate(hybrid_search(query, document_id=doc), 1):
        tag = " [injected table]" if h.get("injected_table") else ""
        print(f"{i}. Page {h['page']} — {h['type'].capitalize()} "
              f"(score {h['score']}){tag}")