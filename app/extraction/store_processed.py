import os
import json

from pdf_parser import parsepdf
from layout_analyzer import analyze_layout
from table_extractor import extract_tables

PROCESSED_ROOT = "data/processed"

def store_processed(document_id:str) -> str:
    parsed = parsepdf(document_id)
    layout = analyze_layout(document_id)
    tables = extract_tables(document_id)

    out_dir = os.path.join(PROCESSED_ROOT, document_id)
    os.makedirs(out_dir, exist_ok = True)

    document = {
        "document_id" : document_id,
        "page_count" : parsed["page_count"],
        "metadata" : parsed["metadata"]   
    }
    _write(out_dir, "document.json", document)

    regions_by_page = {p["page_number"]: p["regions"] for p in layout["pages"]}
    pages = []
    for p in parsed["pages"]:
        n = p["page_number"]
        pages.append({
            "page_number": n,
            "text": p["Text"],
            "regions": regions_by_page.get(n, []),
        })
    _write(out_dir, "pages.json", {"document_id": document_id, "pages": pages})

    _write(out_dir, "tables.json", tables)
    evidence = {"document_id" : document_id, "evidence" : []}
    _write(out_dir, "evidence.json", evidence)

    return out_dir

def _write(out_dir, filename, data):
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"wrote {path}")

if __name__ == "__main__":
    import sys

    out_dir = store_processed(sys.argv[1])
    print(f"All processed data for {sys.argv[1]} is in {out_dir}")
