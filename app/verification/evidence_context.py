"""Restore persisted source context without changing source addresses."""
from __future__ import annotations
import copy


def enrich_candidate(item, siblings=()):
    result = copy.deepcopy(item)
    context = {key: copy.deepcopy(item.get(key)) for key in (
        "title", "section", "heading", "caption", "context", "headers",
        "column_headers", "column_map", "row_units", "unit", "units",
        "currency_quote", "x_axis", "y_axis") if item.get(key)}
    parents, rows = [], []
    for index, row in enumerate(item.get("rows") or []):
        if not row:
            continue
        if str(row[0]).strip() and not any(str(cell or "").strip() for cell in row[1:]):
            parents = [str(row[0])]
        else:
            rows.append({"row_index": index, "label": str(row[0]),
                         "parent_row_labels": list(parents)})
    if rows:
        context["row_context"] = rows
    # Background never shifts indexes in the original text's number bank.
    nearby = []
    for other in siblings:
        if (other.get("id") != item.get("id") and other.get("page") == item.get("page")
                and other.get("type") in {"text", "ocr"} and other.get("content")):
            nearby.append({"source_id": other.get("id"), "text": str(other["content"])[:1600]})
            if len(nearby) == 3:
                break
    if nearby:
        context["same_page_background"] = nearby
    result["candidate_context"] = context
    return result
