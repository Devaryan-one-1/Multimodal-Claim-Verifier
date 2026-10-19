from __future__ import annotations

from ui.model_settings import render_model_settings, render_model_usage

import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from extraction.image_renderer import render_page
from pipelines.document_pipeline import process_document, saved_documents
from pipelines.verification_pipeline import aggregate_verdict, verify_claims
from services.documents import delete_document, persist_document_labels
from services.runtime import digest, model_name
from ui.claim_input import initialize_claim_state, render_claim_input
from ui.dashboard import apply_dashboard_style, render_hero, render_nav, render_overall, render_result
from ui.document_viewer import go_to_page, render_document_viewer
from ui.evaluation import render_evaluation_panel
from ui.evidence import evidence_from_result, render_evidence
from ui.retrieval_evaluation import render_retrieval_evaluation
from ui.upload import initialize_upload_state, process_one_document, render_upload
from ui.verdict_evaluation import render_verdict_evaluation

def _open_upload():
    st.session_state.dashboard_manage = True


def _settings():
    configured = False
    with st.sidebar:
        st.title("Claim Verifier")
        st.caption("Financial document workspace")
        configured = render_model_settings()
        st.divider()
        st.markdown("**Document processing**")
        st.checkbox("OCR pages with little extractable text", value=True, key="dashboard_ocr")
        st.selectbox("Chart scan", ["candidate pages", "all pages", "off"], key="dashboard_charts")
        st.number_input("Candidate chart page limit", min_value=1, max_value=1000, value=20,
                        step=5, key="dashboard_visual_limit", disabled=st.session_state.dashboard_charts != "candidate pages")
        st.caption("Candidate detection can miss charts. All pages scans the complete report and sends more vision requests.")
        st.button("Manage documents", on_click=_open_upload, use_container_width=True)
        st.divider()
        st.caption("Page numbers are PDF page positions. Source extraction and model confidence require review.")
    return configured


def _processing_callback(file_bytes, filename, document_id, company_name="", reporter=None):
    return process_document(file_bytes, filename, document_id, company_name, reporter,
                            enable_ocr=st.session_state.dashboard_ocr,
                            chart_mode=st.session_state.dashboard_charts,
                            visual_limit=int(st.session_state.dashboard_visual_limit),
                            force=bool(st.session_state.get("dashboard_force_processing")))


def _documents_panel():
    before = [(d["document_id"], d.get("status")) for d in st.session_state.upload_documents]
    documents = render_upload(process_document=_processing_callback, permanently_delete_document=delete_document)
    if before != [(d["document_id"], d.get("status")) for d in documents]:
        persist_document_labels(documents)
        st.rerun()
    for document in list(documents):
        doc_id = document["document_id"]
        path = Path(document.get("pdf_path") or f"data/documents/{doc_id}/original.pdf")
        if path.exists():
            label = "Reprocess PDF / rebuild index" if document.get("status") == "ready" else "Process saved PDF"
            if st.button(label, key=f"reprocess_{doc_id}", use_container_width=True):
                st.session_state.dashboard_force_processing = True
                try:
                    process_one_document(document, path.read_bytes(), _processing_callback)
                finally:
                    st.session_state.dashboard_force_processing = False
                st.rerun()
        warnings = document.get("warnings") or []
        if warnings:
            with st.expander(f"Processing coverage: {document['filename']}"):
                for warning in warnings:
                    st.write(warning)
                st.caption(f"Visual pages scanned: {document.get('visual_pages_scanned', 0)} · OCR pages: {document.get('ocr_page_count', 0)}")
    persist_document_labels(documents)
    return documents


def _scope(documents):
    ready = {d["document_id"]: d for d in documents if d.get("status") == "ready"}
    ids = list(ready)
    known = set(st.session_state.get("dashboard_seen_ready", []))
    previous = st.session_state.get("selected_document_ids", [])
    selected = [doc for doc in previous if doc in ready]
    selected.extend(doc for doc in ids if doc not in known and doc not in selected)
    st.session_state.selected_document_ids = selected
    st.session_state.dashboard_seen_ready = ids
    return st.multiselect("Documents for verification and evaluation", options=ids,
                          format_func=lambda doc: ready[doc]["filename"], key="selected_document_ids",
                          help="For a claim without a company name, select one document to keep its scope clear.")


def _workspace(documents, selected):
    ready = {d["document_id"]: d for d in documents if d.get("status") == "ready"}
    claim_column, page_column = st.columns([0.95, 1.25], gap="large")
    with claim_column:
        returned = render_claim_input(selected, documents, verify_callback=verify_claims)
        results = returned if returned is not None else st.session_state.get("latest_verification_results")
        results = results or []
        # Result scopes travel with the saved result; changing the selector never
        # relabels an old citation as belonging to a different document.
        signature = digest(json.dumps(results, sort_keys=True, default=str))
        saved = st.session_state.get("dashboard_citation") or {}
        if saved.get("signature") != signature:
            st.session_state.dashboard_citation = {"signature": signature, "evidence": None}
        def show_page(page, evidence):
            doc_id = evidence.get("document_id")
            if doc_id not in ready:
                st.warning("This citation's document is unavailable. Reprocess or reload its original PDF.")
                return
            if not 1 <= page <= int(ready[doc_id].get("page_count", 0)):
                st.warning("The citation points outside the source PDF.")
                return
            st.session_state.dashboard_citation = {"signature": signature, "evidence": dict(evidence)}
            st.session_state.dashboard_viewer_document = doc_id
            go_to_page(f"workspace::{doc_id}", page)
        if not results:
            st.info("Upload a report, enter an assertion, and select VERIFY. Your result, calculation and citations appear here.")
        else:
            render_overall(results)
            render_model_usage(results)
            for index, result in enumerate(results):
                render_result(result, index, len(results) > 1)
                st.markdown("#### Evidence")
                items = evidence_from_result(result, result.get("document_id"))
                render_evidence(items, on_view_page=show_page, claim=result.get("parsed_claim"),
                                key_prefix=f"workspace_{signature}_{index}")
                if index < len(results) - 1:
                    st.divider()
            with st.expander("Result JSON", expanded=False):
                st.download_button("Download verification result", data=json.dumps(results, ensure_ascii=False, indent=2),
                                   file_name="verification_result.json", mime="application/json", key="result_download")
                st.json(results)
    with page_column:
        with st.container(key="source_panel"):
            if not ready:
                st.subheader("Source document")
                st.info("Your actual PDF page will appear here after processing.")
                return
            evidence = (st.session_state.get("dashboard_citation") or {}).get("evidence")
            options = list(ready)
            prior = st.session_state.get("dashboard_viewer_document")
            if prior not in ready:
                st.session_state.dashboard_viewer_document = next((doc for doc in selected if doc in ready), options[0])
            if evidence and evidence.get("document_id") in ready:
                st.session_state.dashboard_viewer_document = evidence["document_id"]
            viewed = st.selectbox("Source PDF", options, format_func=lambda doc: ready[doc]["filename"],
                                  key="dashboard_viewer_document", on_change=_clear_citation)
            document = ready[viewed]
            if evidence and evidence.get("document_id") != viewed:
                evidence = None
            render_document_viewer(document_id=viewed, page_count=int(document.get("page_count", 0)),
                                   evidence=evidence, render_page_callback=render_page, key_prefix="workspace")
            path = Path(document.get("pdf_path") or f"data/documents/{viewed}/original.pdf")
            if path.exists():
                st.download_button("Download original PDF", path.read_bytes(), file_name=document["filename"],
                                   mime="application/pdf", key="source_pdf_download", use_container_width=True)
            warnings = document.get("warnings") or []
            if warnings:
                with st.expander("Source processing coverage", expanded=False):
                    st.write("\n\n".join(warnings))


def _clear_citation():
    saved = st.session_state.get("dashboard_citation") or {}
    st.session_state.dashboard_citation = {"signature": saved.get("signature"), "evidence": None}


def _history():
    history = st.session_state.get("claim_history") or []
    st.subheader("Claim history")
    st.caption("Current session · Up to 50 recent verifications. Open saved results from Recent claims in Workspace.")
    if not history:
        st.info("No claims have been verified in this session.")
        return
    rows = [{"Time": datetime.fromtimestamp(entry["created_at"]).strftime("%Y-%m-%d %H:%M"),
             "Claim": entry["claim_text"], "Verdict": aggregate_verdict(entry["results"]),
             "Documents": ", ".join(entry["document_ids"])} for entry in reversed(history)]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.download_button("Download claim history", json.dumps(history, ensure_ascii=False, indent=2),
                       file_name="claim_history.json", mime="application/json", key="history_download")


def _how_it_works():
    st.subheader("How it works")
    st.write("Process a financial PDF once, then verify assertions against its text, tables, readable chart labels and OCR evidence. Retrieval combines semantic and keyword search, followed by reranking.")
    st.write("For numerical claims, the model selects source cell or label addresses. Python reads those values, checks their units and periods, performs the calculation, and compares the result with the claim. Missing or ambiguous values produce UNKNOWN with a reason.")
    st.write("Compound assertions are verified independently. A mixture that includes a supported clause is PARTIALLY SUPPORTED. View Page opens the original PDF beside the evidence and highlights its cited region when usable coordinates are available.")
    st.write("Evaluation starts only when you select Build evaluation dataset. AI drafts remain separate until human approval. Export requires 100 approved claims, 25 per verdict, with the agreed straightforward and difficult-case coverage. Retrieval evaluation measures Recall@5, Recall@10 and MRR for complete claims and compound subclaims before and after reranking.")
    st.caption("Model confidence is uncalibrated. Chart/OCR extraction and ambiguous layouts can introduce errors; inspect the cited page. Evaluation results appear only after you run evaluation.")


def run_dashboard():
    initialize_upload_state()
    initialize_claim_state()
    if not st.session_state.get("dashboard_loaded"):
        st.session_state.upload_documents = saved_documents()
        st.session_state.dashboard_loaded = True
    st.session_state.setdefault("dashboard_manage", not st.session_state.upload_documents)
    apply_dashboard_style()
    configured = _settings()
    documents = st.session_state.upload_documents
    ready = [d for d in documents if d.get("status") == "ready"]
    render_nav(len(ready), configured)
    preferred = st.session_state.get("selected_document_ids") or []
    hero_doc = next((d for d in ready if d["document_id"] in preferred), ready[0] if ready else None)
    render_hero(hero_doc, _open_upload)
    if st.toggle("Manage uploaded documents", key="dashboard_manage"):
        documents = _documents_panel()
    selected = _scope(documents)
    workspace, evaluation, history, about = st.tabs(["Workspace", "Evaluation", "History", "How it works"])
    with workspace:
        _workspace(documents, selected)
    # with evaluation:
    #     render_evaluation_panel(selected_document_ids=selected, documents=documents)
    #     render_retrieval_evaluation()
    with evaluation:
        render_evaluation_panel(
            selected_document_ids=selected,
            documents=documents,
        )
        render_retrieval_evaluation()
        render_verdict_evaluation()
    with history:
        _history()
    with about:
        _how_it_works()
