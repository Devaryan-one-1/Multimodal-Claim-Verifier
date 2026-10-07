import os
import json

PROCESSED_ROOT = "data/processed"

TEXT_TYPES = {"paragraph", "heading", "caption", "footnote"}

def build_evidence(document_id: str) -> dict:
    doc_dir = os.path.join(PROCESSED_ROOT, document_id)
    pages = _load(doc_dir, "pages.json")
    tables = _load(doc_dir, "tables.json")

    evidence = []
    counter = 1

    def next_id():
        nonlocal counter
        eid = f"evidence_{counter:03d}"
        counter += 1
        return eid

    # 1. Text + chart evidence, from layout regions
    for page in pages["pages"]:
        page_no = page["page_number"]
        for region in page["regions"]:
            rtype = region["type"]

            if rtype in TEXT_TYPES:
                text = (region.get("text") or "").strip()
                if not text:
                    continue
                evidence.append({
                    "id": next_id(),
                    "document_id": document_id,
                    "page": page_no,
                    "type": _map_text_type(rtype),
                    "content": text,
                    "bbox": list(region["bbox"]),
                })

            elif rtype == "figure":
                # no chart extractor yet — record location as visual evidence
                evidence.append({
                    "id": next_id(),
                    "document_id": document_id,
                    "page": page_no,
                    "type": "chart",
                    "content": "",  # filled once a chart/OCR extractor exists
                    "bbox": list(region["bbox"]),
                })

    # 2. Table evidence, from tables.json
    # for table in tables["tables"]:
    #     evidence.append({
    #         "id": next_id(),
    #         "document_id": document_id,
    #         "page": table["page"],
    #         "type": "table",
    #         "content": _flatten_table(table),   # searchable string
    #         "bbox": table.get("bbox"),           # may be None; tables.json has no bbox yet
    #         "table_id": table["table_id"],
    #         "headers": table["headers"],         # structured data kept alongside
    #         "rows": table["rows"],
    #     })
    for table in tables["tables"]:
        evidence.append({
            "id": next_id(),
            "document_id": document_id,
            "page": table["page"],
            "type": "table",
            "content": _flatten_table(table),   # searchable string
            "bbox": table.get("bbox"),           # now populated from find_tables()
            "table_id": table["table_id"],
            "headers": table["headers"],         # structured data kept alongside
            "rows": table["rows"],
            "column_map": table.get("column_map"),      # positional year/period map
            "column_headers": table.get("column_headers"),  # recovered years/periods
            "is_data": table.get("is_data", True),          # False for TOC tables
        })
    result = {"document_id": document_id, "evidence": evidence}

    # overwrite the placeholder evidence.json
    out_path = os.path.join(doc_dir, "evidence.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(evidence)} evidence records to {out_path}")

    return result


def _map_text_type(region_type):
    """Layout region type -> evidence type. Captions/footnotes keep their identity."""
    if region_type in ("paragraph", "heading"):
        return "text"
    return region_type  # "caption" or "footnote"


# def _flatten_table(table):
#     """Turn headers+rows into one searchable line, like your example."""
#     parts = []
#     headers = table["headers"]
#     for row in table["rows"]:
#         # pair each cell with its header: "Region North America 2024 $40B 2025 $50B"
#         label = row[0] if row else ""
#         pairs = []
#         for h, cell in zip(headers[1:], row[1:]):
#             pairs.append(f"{h} {cell}".strip())
#         line = f"{label} " + " ".join(pairs)
#         parts.append(line.strip())
#     return " | ".join(parts)

def _flatten_table(table):
    """
    Build a searchable string from row labels + values, labelling each value
    with its recovered period column (year + period type) when available.
    e.g. 'Products: 2026 three_months 78678, 2025 three_months 66613 | Services: ...'
    """
    col_map = table.get("column_map") or []
    parts = []
    for row in table.get("rows") or []:
        if not row:
            continue
        label = str(row[0])
        values = row[1:]
        pairs = []
        for i, cell in enumerate(values):
            if i < len(col_map):
                cm = col_map[i]
                tag = f"{cm['year']}"
                if cm.get("period"):
                    tag += f" {cm['period']}"
                pairs.append(f"{tag} {cell}")
            else:
                pairs.append(str(cell))
        parts.append(f"{label}: " + ", ".join(pairs))
    return " | ".join(parts)


def _load(doc_dir, filename):
    path = os.path.join(doc_dir, filename)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys
    from collections import Counter

    result = build_evidence(sys.argv[1])
    counts = Counter(e["type"] for e in result["evidence"])
    print("by type:", dict(counts))