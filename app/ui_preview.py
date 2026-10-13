from __future__ import annotations

import time
from pathlib import Path

import streamlit as st
from pypdf import PdfReader

from extraction.image_renderer import render_page
# from ui.claim_input import render_claim_input
from ui.claim_input import render_claim_input
from ui.document_viewer import go_to_page, render_document_viewer
from ui.evidence import render_evidence
from ui.sidebar import apply_theme, render_sidebar
from ui.upload import render_upload
from ui.verdict import render_verdicts
from ui.evidence_panel import render_evidence_panel
from ui.preview_evidence import actual_page_preview
from ui.evaluation import render_evaluation_panel
from ui.retrieval_evaluation import render_retrieval_evaluation

st.set_page_config(
    page_title="Multimodal Claim Verifier",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme()

st.session_state.setdefault("verification_history", [])
st.session_state.setdefault("latest_results", [])


# def preview_document_processor(
#     file_bytes: bytes,
#     filename: str,
#     document_id: str,
#     company_name: str,
# ) -> dict:
#     """
#     Temporary processor used only to test the UI.

#     It saves the PDF in the structure expected by your existing backend.
#     Replace this function with document_pipeline later.
#     """

#     document_directory = Path("data/documents") / document_id
#     document_directory.mkdir(parents=True, exist_ok=True)

#     pdf_path = document_directory / "original.pdf"
#     pdf_path.write_bytes(file_bytes)

#     reader = PdfReader(str(pdf_path))

#     return {
#         "status": "ready",
#         "document_id": document_id,
#         "filename": filename,
#         "company_name": company_name,
#         "page_count": len(reader.pages),
#         "table_count": "Pending",
#         "chart_count": "Pending",
#         "pdf_path": str(pdf_path),
#     }

def preview_document_processor(
    file_bytes: bytes,
    filename: str,
    document_id: str,
    company_name: str,
    reporter,
) -> dict:
    from pathlib import Path
    from pypdf import PdfReader

    document_directory = Path("data/documents") / document_id
    document_directory.mkdir(parents=True, exist_ok=True)

    reporter.update(
        "Saving document",
        completed=1,
        message=f"Saving {filename}",
    )

    pdf_path = document_directory / "original.pdf"
    pdf_path.write_bytes(file_bytes)

    reporter.update(
        "Extracting text",
        completed=2,
        message="Opening PDF and counting pages",
    )

    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)

    text_page_count = 0

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""

        if text.strip():
            text_page_count += 1

        reporter.log(
            f"Read page {page_number}/{page_count}"
        )

    reporter.update(
        "Analyzing layout",
        completed=3,
        message="Layout analysis is not connected in preview mode",
    )

    reporter.update(
        "Extracting tables",
        completed=4,
        message="Table extraction is pending document pipeline",
    )

    reporter.update(
        "Detecting charts",
        completed=5,
        message="Chart detection is pending document pipeline",
    )

    reporter.update(
        "Building evidence",
        completed=6,
        message="Evidence building is pending document pipeline",
    )

    reporter.update(
        "Creating search index",
        completed=7,
        message="Index creation is pending document pipeline",
    )

    return {
        "status": "ready",
        "document_id": document_id,
        "filename": filename,
        "company_name": company_name,
        "page_count": page_count,
        "text_extracted": text_page_count > 0,
        "text_page_count": text_page_count,
        "table_count": 0,
        "chart_count": 0,
        "evidence_count": 0,
        "pdf_path": str(pdf_path),
    }

def preview_verification_callback(
    claim_text: str,
    document_ids: list[str],
    reporter,
) -> list[dict]:
    """
    Temporary UI test callback.

    Replace this with verification_pipeline later.
    """

    reporter.update(
        "Resolving document scope",
        completed=2,
        message=(
            f"Searching {len(document_ids)} selected document(s)"
        ),
    )

    reporter.update(
        "Retrieving evidence",
        completed=3,
        message="Preview mode: no real retrieval performed",
    )

    reporter.update(
        "Verifying calculations",
        completed=4,
        message="Preview mode: no calculation performed",
    )

    reporter.update(
        "Reasoning over evidence",
        completed=5,
        message="Preview mode: reasoning pipeline not connected",
    )

    # return [
    #     {
    #         "claim_text": claim_text,
    #         "status": "complete",
    #         "verdict": "UNKNOWN",
    #         "confidence": 0.0,
    #         "reason": (
    #             "The claim-input interface is working, but the "
    #             "verification pipeline is not connected yet."
    #         ),
    #         "evidence": [],
    #         "document_ids": document_ids,
    #     }
    # ]
    preview_items = (
            actual_page_preview(
                document_ids[0],
                page_number=1,
            )
            if document_ids
            else []
        )
    return [
        {
            "claim_text": claim_text,
            "status": "complete",
            "verdict": "CONTRADICTED",
            "confidence": 0.98,
            "company_name": "Example Company",
            "reporting_period": "2024–2025",
            "unit": "percent",
            "calculation": {
                "raw_inputs": {
                    "old": "$10B",
                    "new": "$11B",
                },
                "inputs": {
                    "old": 10_000_000_000,
                    "new": 11_000_000_000,
                },
                "formula": "(new - old)/old*100",
                "value": 10.0,
                "currency": "USD",
            },
            "comparison": {
                "claimed": 40.0,
                "actual": 10.0,
                "difference": 30.0,
                "verdict": "CONTRADICTED",
            },
            "reason": (
                "Revenue increased by 10%, not the claimed 40%."
            ),
            # "evidence": [],
            "evidence": preview_items,
            "document_ids": document_ids,   
        }
    ]
st.title("Multimodal Claim Verifier")
st.caption(
    "Evidence-grounded financial document analysis and claim verification"
)

# documents = render_upload(
#     process_document=preview_document_processor,
# )
documents = render_upload(
    process_document=preview_document_processor,
    permanently_delete_document=None,
)

sidebar_settings = render_sidebar(
    documents=documents,
    history=st.session_state.verification_history,
)

ready_documents = {
    document["document_id"]: document
    for document in documents
    if document.get("status") == "ready"
}

# submission = render_claim_input(
#     selected_document_ids=sidebar_settings["selected_document_ids"],
#     disabled=not ready_documents,
# )

# if submission:
#     start_time = time.perf_counter()

#     # Temporary demonstration result.
#     # Replace this block with verification_pipeline later.
#     mock_evidence = [
#         {
#             "id": "preview-evidence-1",
#             "type": "text",
#             "page": 1,
#             "content": (
#                 "This is preview evidence used to test the Day 8 interface. "
#                 "The real verification pipeline will provide retrieved text, "
#                 "table, chart, and OCR evidence."
#             ),
#             "bbox": None,
#             "score": 0.95,
#         }
#     ]

#     elapsed = time.perf_counter() - start_time

#     preview_result = {
#         "claim_text": submission.text,
#         "verdict": "UNKNOWN",
#         "confidence": 0.50,
#         "reason": (
#             "The interface is running correctly. The verification pipeline "
#             "has not been connected, so no real verdict was generated."
#         ),
#         "claimed_value": None,
#         "actual_value": None,
#         "formula": None,
#         "processing_time_seconds": elapsed,
#         "evidence": mock_evidence,
#     }

#     st.session_state.latest_results = [preview_result]

#     st.session_state.verification_history.append(
#         {
#             "claim": submission.text,
#             "verdict": preview_result["verdict"],
#             "created_at": time.time(),
#         }
#     )

# results = st.session_state.latest_results

# if results:
#     render_verdicts(results)

#     evidence_items = results[0].get("evidence", [])

# results = render_claim_input(
#     selected_document_ids=(
#         sidebar_settings["selected_document_ids"]
#     ),
#     documents=documents,
#     verify_callback=preview_verification_callback,
# )

# if results:
#     st.session_state.latest_results = results
#     render_verdicts(results)
#     evidence_items = results[0].get("evidence", [])

#     selected_ids = sidebar_settings["selected_document_ids"]

#     if selected_ids:
#         active_document_id = selected_ids[0]
#         active_document = ready_documents.get(active_document_id)

#         if active_document:
#             evidence_column, viewer_column = st.columns(
#                 [0.9, 1.4],
#                 gap="large",
#             )

#             with evidence_column:
#                 selected_page = render_evidence(evidence_items)

#                 if selected_page:
#                     go_to_page(
#                         active_document_id,
#                         selected_page,
#                     )

#             with viewer_column:
#                 render_document_viewer(
#                     document_id=active_document_id,
#                     page_count=active_document["page_count"],
#                     initial_page=1,
#                     evidence=(
#                         evidence_items[0]
#                         if evidence_items
#                         else None
#                     ),
#                     render_page_callback=render_page,
#                 )

selected_ids = sidebar_settings["selected_document_ids"]
current_scope = tuple(sorted(selected_ids))

new_results = render_claim_input(
    selected_document_ids=selected_ids,
    documents=documents,
    verify_callback=preview_verification_callback,
)

if new_results is not None:
    st.session_state.latest_results = new_results
    st.session_state.latest_results_scope = current_scope

results = (
    st.session_state.get("latest_results", [])
    if (
        st.session_state.get("latest_results_scope")
        == current_scope
    )
    else []
)

if results:
    st.caption(
        "UI preview mode. The sample verdict "
        "is not real claim verification."
    )

    render_verdicts(results)

    render_evidence_panel(
        results=results,
        documents=documents,
        selected_document_ids=selected_ids,
        render_page_callback=render_page,
    )
render_evaluation_panel(
    selected_document_ids=selected_ids,
    documents=documents,
    )
render_retrieval_evaluation()