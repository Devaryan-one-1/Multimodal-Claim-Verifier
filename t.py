# # # # # # # # from app.retrieval.reranker import rerank
# # # # # # # # from app.retrieval.vector_store import search
# # # # # # # # from app.verification.claim_parser import parse_claims

# # # # # # # # claims = parse_claims("International revenue grew 40% in 2025")
# # # # # # # # c = claims[0]
# # # # # # # # hits = search(c["claim_text"], top_k=20, document_id="Apple_Financial_10_K")
# # # # # # # # top = rerank(c["claim_text"], hits, keep=5, parsed_claim=c)

# # # # # # # # for i, h in enumerate(top, 1):
# # # # # # # #     print(f"{i}. Page {h['page']} — {h['type']} (rerank {h['rerank_score']:.3f})")
# # # # # # # # debug_retrieve.py  (project root)
# # # # # # # import os, sys
# # # # # # # sys.path.insert(0, os.path.join("app", "retrieval"))
# # # # # # # sys.path.insert(0, os.path.join("app", "verification"))
# # # # # # # from vector_store import search
# # # # # # # from reranker import rerank

# # # # # # # q = "Revenue grew 25% in 2025"
# # # # # # # hits = search(q, top_k=20, document_id="Apple_Financial_10_K")
# # # # # # # print("=== raw retrieval (20) ===")
# # # # # # # for h in hits:
# # # # # # #     print(f"  {h['type']:10} p{h['page']}  {h.get('text','')[:60]!r}")

# # # # # # # top = rerank(q, hits, keep=5)
# # # # # # # print("\n=== after rerank (5) ===")
# # # # # # # for h in top:
# # # # # # #     print(f"  {h['type']:10} p{h['page']}  {h.get('text','')[:60]!r}")

# # # # # # # print("\ntable hits in top 5:", sum(1 for h in top if h['type'] == 'table'))
# # # # # # # cmp_tables.py  (project root)
# # # # # # import pdfplumber

# # # # # # pdf = "data/documents/Apple_Financial_10_K/original.pdf"

# # # # # # # page 4 had table_4_1 with the messy $ / number split
# # # # # # PAGE = 3  # 0-indexed -> page 4

# # # # # # with pdfplumber.open(pdf) as doc:
# # # # # #     page = doc.pages[PAGE]

# # # # # #     print("=== DEFAULT (line-based) ===")
# # # # # #     for tbl in page.extract_tables():
# # # # # #         for row in tbl[:4]:
# # # # # #             print(row)
# # # # # #         print("---")

# # # # # #     print("\n=== TEXT STRATEGY ===")
# # # # # #     settings = {"vertical_strategy": "text", "horizontal_strategy": "text"}
# # # # # #     for tbl in page.extract_tables(table_settings=settings):
# # # # # #         for row in tbl[:4]:
# # # # # #             print(row)
# # # # # #         print("---")
# # # # # # cmp_tables.py  (project root)
# # # # # import pdfplumber

# # # # # pdf = "data/documents/Apple_Financial_10_K/original.pdf"
# # # # # PAGE = 3  # 0-indexed -> page 4 (had the messy table_4_1)

# # # # # with pdfplumber.open(pdf) as doc:
# # # # #     page = doc.pages[PAGE]

# # # # #     print("=== DEFAULT (line-based) ===")
# # # # #     for tbl in page.extract_tables():
# # # # #         for row in tbl[:5]:
# # # # #             print(row)
# # # # #         print("---")

# # # # #     print("\n=== TEXT STRATEGY ===")
# # # # #     settings = {"vertical_strategy": "text", "horizontal_strategy": "text"}
# # # # #     for tbl in page.extract_tables(table_settings=settings):
# # # # #         for row in tbl[:5]:
# # # # #             print(row)
# # # # #         print("---")
# # # # # check_headers.py (project root)
# # # # import sys, os
# # # # sys.path.insert(0, os.path.join("app", "extraction"))
# # # # from table_extractor import extract_tables

# # # # d = extract_tables("Apple_Financial_10_K")
# # # # for t in d["tables"][:10]:
# # # #     ch = t["column_headers"]
# # # #     print(f"{t['table_id']}  cols={t['data_columns']}  years={ch['years']}  "
# # # #           f"types={ch['period_types']}  llm={t['needs_llm_header']}")
# # # #     print(f"    raw header block: {ch['raw'][:80]!r}")
# # # # t2.py
# # # import os, sys
# # # sys.path.insert(0, os.path.join("app","retrieval"))
# # # from vector_store import search
# # # from reranker import rerank
# # # q = "total net sales revenue 2026 2025"
# # # hits = search(q, top_k=20, document_id="Apple_Financial_10_K")
# # # print("tables in raw 20:", sum(1 for h in hits if h["type"]=="table"))
# # # top = rerank(q, hits, keep=5)
# # # print("tables in top 5:", sum(1 for h in top if h["type"]=="table"))
# # # for h in top:
# # #     print(f"  {h['type']:8} p{h['page']} {h.get('text','')[:50]!r}")

# # # t3.py
# # import os, sys, json
# # sys.path.insert(0, os.path.join("app","retrieval"))
# # from vector_store import _vector_rank, _bm25_rank, _client
# # # does ANY table rank at all for a table-ish query?
# # q = "total net sales products services"
# # client = _client()
# # vids = _vector_rank(client, q, "Apple_Financial_10_K", 50)
# # bids, meta = _bm25_rank(q, "Apple_Financial_10_K", 50)
# # # how do table chunks rank?
# # tbl_ids = [cid for cid, m in meta.items() if m["type"]=="table"]
# # print("total table chunks:", len(tbl_ids))
# # print("tables in vector top50:", [i for i,cid in enumerate(vids) if meta.get(cid,{}).get('type')=='table'][:10])
# # print("tables in bm25 top50:", [i for i,cid in enumerate(bids) if meta[cid]['type']=='table'][:10])

# # test_match.py
# import os, sys
# sys.path.insert(0, os.path.join("app","retrieval"))
# sys.path.insert(0, os.path.join("app","verification"))
# from claim_verifier import _match_table_cells
# import json

# d = json.load(open("data/processed/Apple_Financial_10_K/tables.json", encoding="utf-8"))
# # find the income statement table (has 'Total net sales')
# tbl = None
# for t in d["tables"]:
#     if any("total net sales" in str(r[0]).lower() for r in t.get("rows", []) if r):
#         tbl = t; break

# # simulate a hit dict (what rerank would pass)
# hit = {"rows": tbl["rows"], "column_map": tbl["column_map"]}

# # claim: total net sales, 2025 -> 2026, quarterly
# pc = {"metric": "total net sales", "segment": None,
#       "start_period": "2025", "end_period": "2026",
#       "claim_text": "Total net sales grew from 2025 to 2026"}
# old, new, why = _match_table_cells(pc, hit)
# print("old:", old, "new:", new, "reason:", why)

# client = get_llm_client()

# prompt = """
# USER CLAIM:
# International revenue grew by 40%.

# DOCUMENT EVIDENCE:

# Evidence ID: evidence_52_1
# Page: 52

# International Revenue
# 2024: $10 billion
# 2025: $11 billion


# DETERMINISTIC CALCULATION:

# Old value: 10000000000
# New value: 11000000000
# Percentage growth: 10%


# DETERMINISTIC COMPARISON:

# Claimed: 40%
# Actual: 10%
# Verdict: CONTRADICTED


# VALID EVIDENCE IDS:

# evidence_52_1
# """

# result = client.generate_verification_result(
#     prompt=prompt,
#     system_instruction=GROUNDING_SYSTEM_PROMPT,
#     valid_evidence_ids=["evidence_52_1"],
#     deterministic_verdict="CONTRADICTED",
# )

# print("\nSTRUCTURED RESULT")
# print("-----------------")
# print(result)

# print("\nVerdict:", result["verdict"])
# print("Confidence:", result["confidence"])
# print("Reason:", result["reason"])
# print("Evidence IDs:", result["evidence_ids"])

# from app.reasoning.evidence_fusion import fuse_evidence


# print("\n")
# print("=" * 60)
# print("EVIDENCE FUSION TEST")
# print("=" * 60)


# # ---------------------------------------------------------
# # TEST 1
# # Table resolves disagreement from text
# # ---------------------------------------------------------

# hits = [
#     {
#         "chunk_id": "chunk_text_1",
#         "source_evidence_id": "evidence_text_1",
#         "page": 52,
#         "type": "text",
#         "rerank_score": 3.2,
#         "text": "Revenue was $90 billion.",

#         # Structured facts are optional.
#         # They make deterministic conflict detection possible.
#         "facts": [
#             {
#                 "metric": "revenue",
#                 "period": 2025,
#                 "value": 90,
#                 "unit": "USD billion",
#             }
#         ],
#     },

#     {
#         "chunk_id": "chunk_table_1",
#         "source_evidence_id": "evidence_table_1",
#         "page": 52,
#         "type": "table",
#         "table_id": "table_52_1",
#         "rerank_score": 2.1,
#         "text": "Revenue 2025: $100 billion",

#         "facts": [
#             {
#                 "metric": "revenue",
#                 "period": 2025,
#                 "value": 100,
#                 "unit": "USD billion",
#             }
#         ],
#     },
# ]


# charts = [
#     {
#         "evidence_id": "evidence_chart_1",
#         "page": 53,
#         "type": "chart",
#         "chart_id": "chart_53_1",
#         "content": "Revenue 2025: $100 billion",

#         "facts": [
#             {
#                 "metric": "revenue",
#                 "period": 2025,
#                 "value": 100,
#                 "unit": "USD billion",
#             }
#         ],
#     }
# ]


# calculation = {
#     "value": 25.0,
#     "formula": "(new - old)/old*100",
#     "inputs": {
#         "old": 80,
#         "new": 100,
#     },
# }


# comparison = {
#     "claimed": 25,
#     "actual": 25,
#     "difference": 0,
#     "verdict": "SUPPORTED",
# }


# result = fuse_evidence(
#     hits,
#     calculation=calculation,
#     comparison=comparison,
#     chart_evidence=charts,
# )


# print("\nTEST 1")
# print("Primary source:")
# print(result["primary_numerical_source"])

# print("\nEvidence IDs:")
# print(result["evidence_ids"])

# print("\nPages:")
# print(result["pages"])

# print("\nHas conflict:")
# print(result["has_conflict"])

# print("\nUnresolved conflict:")
# print(result["unresolved_conflict"])

# print("\nRequires UNKNOWN:")
# print(result["requires_unknown"])

# print("\nConflicts:")
# print(result["conflicts"])

# print("\n")
# print("=" * 60)
# print("UNRESOLVED CONFLICT TEST")
# print("=" * 60)


# conflicting_tables = [
#     {
#         "source_evidence_id": "table_evidence_A",
#         "page": 50,
#         "type": "table",
#         "table_id": "table_A",
#         "text": "Revenue 2025 = 100",

#         "facts": [
#             {
#                 "metric": "revenue",
#                 "period": 2025,
#                 "value": 100,
#                 "unit": "USD billion",
#             }
#         ],
#     },

#     {
#         "source_evidence_id": "table_evidence_B",
#         "page": 51,
#         "type": "table",
#         "table_id": "table_B",
#         "text": "Revenue 2025 = 110",

#         "facts": [
#             {
#                 "metric": "revenue",
#                 "period": 2025,
#                 "value": 110,
#                 "unit": "USD billion",
#             }
#         ],
#     },
# ]


# conflict_result = fuse_evidence(
#     conflicting_tables
# )


# print(
#     "Has conflict:",
#     conflict_result[
#         "has_conflict"
#     ],
# )

# print(
#     "Unresolved conflict:",
#     conflict_result[
#         "unresolved_conflict"
#     ],
# )

# print(
#     "Requires UNKNOWN:",
#     conflict_result[
#         "requires_unknown"
#     ],
# )

# print(
#     "Conflict details:",
#     conflict_result[
#         "conflicts"
#     ],
# )



# DOCUMENT_ID = "Apple_Financial_10_K"


# print("\n")
# print("=" * 60)
# print("OCR BACKENDS")
# print("=" * 60)

# print(
#     check_ocr_backends()
# )
# print("\n")
# print("=" * 60)
# print("OCR AUTOMATIC DECISION")
# print("=" * 60)


# weak_pages = find_weak_text_pages(
#     DOCUMENT_ID
# )

# print(
#     "Weak pages:",
#     weak_pages,
# )


# automatic = ocr_page(
#     DOCUMENT_ID,
#     1,
# )

# print(
#     "\nAutomatic result:"
# )

# print(
#     "Status:",
#     automatic["status"],
# )

# print(
#     "Provider:",
#     automatic["provider"],
# )

# print(
#     "Reason:",
#     automatic.get("reason"),
# )

# print("\n")
# print("=" * 60)
# print("FORCED GEMINI OCR")
# print("=" * 60)


# gemini_result = ocr_page(
#     DOCUMENT_ID,
#     1,

#     force=True,
#     overwrite=True,

#     providers=(
#         "gemini",
#     ),
# )


# print(
#     "Success:",
#     gemini_result["success"],
# )

# print(
#     "Status:",
#     gemini_result["status"],
# )

# print(
#     "Provider:",
#     gemini_result["provider"],
# )

# print(
#     "Confidence:",
#     gemini_result["confidence"],
# )

# print(
#     "Blocks:",
#     len(
#         gemini_result["blocks"]
#     ),
# )

# print(
#     "\nOCR text preview:"
# )

# print(
#     (
#         gemini_result.get("text")
#         or ""
#     )[:1200]
# )

# print(
#     "\nProvider attempts:"
# )

# print(
#     gemini_result[
#         "provider_attempts"
#     ]
# )
# cached = ocr_page(
#     DOCUMENT_ID,
#     1,
#     force=True,
# )


# print(
#     "\nCached status:",
#     cached["status"],
# )

# print(
#     "Provider:",
#     cached["provider"],
# )
# from app.extraction.ocr import (
#     check_tesseract,
#     find_weak_text_pages,
#     ocr_page,
# )


# print("\n")
# print("=" * 60)
# print("OCR TEST")
# print("=" * 60)


# DOCUMENT_ID = "Apple_Financial_10_K"


# # ---------------------------------------------------------
# # Tesseract installation check
# # ---------------------------------------------------------

# status = check_tesseract()

# print("\nTesseract available:")
# print(status["available"])

# print("\nTesseract version:")
# print(status.get("version"))

# print("\nTesseract command:")
# print(status.get("command"))

# if not status["available"]:
#     print("\nERROR:")
#     print(status.get("error"))
# weak_pages = find_weak_text_pages(
#     DOCUMENT_ID
# )

# print("\nPages requiring OCR automatically:")
# print(weak_pages)
# automatic = ocr_page(
#     DOCUMENT_ID,
#     1,
# )

# print("\nAutomatic OCR result:")
# print("Status:", automatic["status"])
# print("Success:", automatic["success"])
# print("Reason:", automatic.get("reason"))
# automatic = ocr_page(
#     DOCUMENT_ID,
#     1,
# )

# print("\nAutomatic OCR result:")
# print("Status:", automatic["status"])
# print("Success:", automatic["success"])
# print("Reason:", automatic.get("reason"))
# # print("\n")
# # print("=" * 60)
# # print("PAGE RENDERING TEST")
# # print("=" * 60)


# # result = render_pages(
# #     document_id="Apple_Financial_10_K",
# #     page_numbers=[
# #         1,
# #         5,
# #         10,
# #     ],
# # )


# # print("\nDocument:")
# # print(result["document_id"])

# # print("\nPage count:")
# # print(result["page_count"])

# # print("\nRendered:")
# # for page in result["rendered"]:
# #     print(
# #         f"Page {page['page_number']} "
# #         f"-> {page['image_path']} "
# #         f"({page['width_px']}x"
# #         f"{page['height_px']})"
# #     )


# # print("\nReused:")
# # for page in result["reused"]:
# #     print(
# #         f"Page {page['page_number']} "
# #         f"-> {page['image_path']}"
# #     )


# # print("\nErrors:")
# # for error in result["errors"]:
# #     print(error)


# # print("\nManifest:")
# # print(result["manifest_path"])


# DOCUMENT_ID = "Apple_Financial_10_K"


# print("\n")
# print("=" * 60)
# print("CHART DETECTION TEST")
# print("=" * 60)


# result = detect_charts(
#     document_id=DOCUMENT_ID,

#     page_numbers=[
#         1,
#         5,
#         10,
#     ],

#     overwrite=True,
# )


# print("\nTotal charts:")
# print(result["chart_count"])

# print("\nPages containing charts:")
# print(result["detected_pages"])

# print("\nPages with no charts:")
# print(result["no_chart_pages"])

# print("\nFailed pages:")
# print(result["failed_pages"])


# for page_result in result["results"]:

#     print("\n" + "-" * 50)

#     print(
#         "Page:",
#         page_result["page_number"],
#     )

#     print(
#         "Status:",
#         page_result["status"],
#     )

#     print(
#         "Charts:",
#         len(
#             page_result.get(
#                 "charts",
#                 [],
#             )
#         ),
#     )

#     for chart in page_result.get(
#         "charts",
#         [],
#     ):

#         print(
#             "\nChart ID:",
#             chart["chart_id"],
#         )

#         print(
#             "Type:",
#             chart["chart_type"],
#         )

#         print(
#             "Title:",
#             chart["title"],
#         )

#         print(
#             "BBox:",
#             chart["bbox"],
#         )

#         print(
#             "Area ratio:",
#             chart["area_ratio"],
#         )

#         print(
#             "Crop:",
#             chart["image_path"],
#         )
        

        

import sys

import os

sys.path.insert(0, os.path.join("app","reasoning"))
sys.path.insert(0, os.path.join("app","extraction"))
from app.reasoning.llm_client import get_llm_client
from app.reasoning.prompts import GROUNDING_SYSTEM_PROMPT

from app.extraction.image_renderer import render_pages
from app.extraction.ocr import (
    check_ocr_backends,
    find_weak_text_pages,
    ocr_page,
)
from app.extraction.chart_detector import detect_charts
import json
from pathlib import Path
import pymupdf

from app.reasoning.vision_client import (
    analyze_chart,
)


print("\n")
print("=" * 60)
print("VISION CLIENT TEST")
print("=" * 60)


# ---------------------------------------------------------
# Create synthetic financial chart
# ---------------------------------------------------------

test_dir = Path(
    "data/test_assets"
)

test_dir.mkdir(
    parents=True,
    exist_ok=True,
)

chart_path = (
    test_dir
    / "synthetic_revenue_chart.png"
)


doc = pymupdf.open()

page = doc.new_page(
    width=800,
    height=600,
)


# Title
page.insert_text(
    (180, 50),
    "International Revenue",
    fontsize=22,
)


page.insert_text(
    (260, 80),
    "USD billions",
    fontsize=14,
)


# Y axis
page.draw_line(
    (120, 480),
    (120, 130),
)


# X axis
page.draw_line(
    (120, 480),
    (680, 480),
)


# Y labels
for value, y in [
    (0, 480),
    (5, 330),
    (10, 180),
]:

    page.insert_text(
        (80, y + 5),
        str(value),
        fontsize=12,
    )

    page.draw_line(
        (115, y),
        (680, y),
        color=(
            0.8,
            0.8,
            0.8,
        ),
    )


# 2024 bar = 10
page.draw_rect(
    pymupdf.Rect(
        220,
        180,
        330,
        480,
    ),

    fill=(
        0.4,
        0.4,
        0.4,
    ),
)


# 2025 bar = 11
page.draw_rect(
    pymupdf.Rect(
        440,
        150,
        550,
        480,
    ),

    fill=(
        0.6,
        0.6,
        0.6,
    ),
)


# Explicit values
page.insert_text(
    (255, 165),
    "10",
    fontsize=14,
)


page.insert_text(
    (475, 135),
    "11",
    fontsize=14,
)


# Years
page.insert_text(
    (250, 510),
    "2024",
    fontsize=14,
)


page.insert_text(
    (470, 510),
    "2025",
    fontsize=14,
)


# Render
pixmap = page.get_pixmap(
    dpi=200,
    alpha=False,
)

pixmap.save(
    str(chart_path)
)

doc.close()


# ---------------------------------------------------------
# Pretend chart_detector.py found this chart
# ---------------------------------------------------------

chart = {
    "chart_id": (
        "synthetic_chart_001"
    ),

    "document_id": (
        "synthetic_test"
    ),

    "page": 1,

    "chart_type": "bar",

    "title": (
        "International Revenue"
    ),

    "bbox": [
        0,
        0,
        pixmap.width,
        pixmap.height,
    ],

    "image_path": str(
        chart_path
    ),

    "evidence": {
        "evidence_id": (
            "synthetic_chart_evidence_001"
        )
    },
}


# ---------------------------------------------------------
# Analyze
# ---------------------------------------------------------

result = analyze_chart(
    chart,
    overwrite=True,
)


print(
    "\nSuccess:",
    result["success"],
)

print(
    "Status:",
    result["status"],
)

print(
    "Chart ID:",
    result["chart_id"],
)

print(
    "\nAnalysis:"
)

print(
    json.dumps(
        result.get(
            "analysis"
        ),
        indent=2,
    )
)

print(
    "\nFacts:"
)

print(
    json.dumps(
        result.get(
            "facts"
        ),
        indent=2,
    )
)
print("\nAttempts:")

print(
    json.dumps(
        result.get(
            "attempts",
            []
        ),
        indent=2,
    )
)

print(
    "\nFailure reason:",
    result.get("reason")
)
print(
    "Usable for numerical verification:",
    result.get(
        "usable_for_numeric_verification"
    )
)

print(
    "Readable values:",
    result.get(
        "readable_value_count"
    )
)