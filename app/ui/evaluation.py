from __future__ import annotations

import json
from collections import Counter

import streamlit as st

from evaluation.dataset import (
    ROOT,
    VERDICTS,
    add_drafts,
    create_run,
    export_reviewed,
    list_runs,
    load_run,
    update_record,
    verdict_counts,
)
from extraction.image_renderer import render_page
from ui.evidence_panel import render_evidence_panel
from evaluation.coverage import export_coverage_ready
from ui.evaluation_coverage import render_coverage_summary

def render_evaluation_panel(
    selected_document_ids: list[str],
    documents: list[dict],
) -> None:
    st.divider()
    st.subheader("Evaluation dataset")

    st.caption(
        "Generate draft claims from selected documents, "
        "review their labels and source evidence, then export "
        "100 approved claims: 25 per verdict."
    )

    ready_ids = {
        document["document_id"]
        for document in documents
        if document.get("status") == "ready"
    }

    saved_runs = list_runs()

    if saved_runs:
        saved_id = st.selectbox(
            "Saved datasets",
            options=saved_runs,
            key="evaluation_saved_dataset",
        )

        if st.button(
            "Open saved dataset",
            key="evaluation_open_saved",
        ):
            st.session_state.evaluation_dataset_id = saved_id
            st.rerun()

    if st.button(
        "Build evaluation dataset",
        type="primary",
        disabled=not selected_document_ids,
        key="evaluation_build",
    ):
        try:
            missing = (
                set(selected_document_ids) - ready_ids
            )

            if missing:
                raise ValueError(
                    "These selected documents are not ready: "
                    + ", ".join(sorted(missing))
                )

            run = create_run(selected_document_ids)

            st.session_state.evaluation_dataset_id = (
                run["dataset_id"]
            )

            with st.status(
                "Generating evaluation drafts",
                expanded=True,
            ) as status:
                try:
                    add_drafts(
                        run["dataset_id"],
                        batches=10,
                        progress=status.write,
                    )

                    status.update(
                        label="Draft generation finished",
                        state="complete",
                    )

                except Exception as exc:
                    status.update(
                        label="Generation stopped; drafts saved",
                        state="error",
                    )
                    st.error(str(exc))

        except Exception as exc:
            st.error(str(exc))

    dataset_id = st.session_state.get(
        "evaluation_dataset_id"
    )

    if not dataset_id:
        st.info(
            "Select processed documents and build a dataset, "
            "or open a saved dataset."
        )
        return

    try:
        run = load_run(dataset_id)
    except Exception as exc:
        st.error(f"Could not load dataset: {exc}")
        return

    st.caption(
        f"Dataset: {dataset_id} · "
        f"Documents: {', '.join(run['document_ids'])}"
    )

    available = Counter(
        source["type"]
        for source in run["evidence"].values()
    )

    st.write(
        "Available source evidence:",
        dict(available),
    )

    missing_modalities = [
        label
        for label, kinds in (
            ("text", {"text", "caption", "footnote"}),
            ("tables", {"table"}),
            ("readable charts", {"chart"}),
            ("OCR", {"ocr"}),
        )
        if not any(available[kind] for kind in kinds)
    ]

    if missing_modalities:
        st.warning(
            "This snapshot has no usable evidence for: "
            + ", ".join(missing_modalities)
            + ". These modalities will not be invented."
        )

    approved = verdict_counts(run)
    drafts = verdict_counts(run, statuses=("draft",))

    st.dataframe(
        [
            {
                "Verdict": verdict,
                "Approved": approved[verdict],
                "Draft": drafts[verdict],
                "Target": 25,
                "Still needed": max(
                    0, 25 - approved[verdict]
                ),
            }
            for verdict in VERDICTS
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.progress(
        sum(
            min(25, approved[verdict])
            for verdict in VERDICTS
        ) / 100
    )

    if st.button(
        "Generate missing / replacement drafts",
        key=f"evaluation_more_{dataset_id}",
    ):
        with st.status(
            "Generating additional drafts",
            expanded=True,
        ) as status:
            try:
                add_drafts(
                    dataset_id,
                    batches=5,
                    progress=status.write,
                )

                status.update(
                    label="Additional drafts saved",
                    state="complete",
                )

                st.rerun()

            except Exception as exc:
                status.update(
                    label="Generation stopped; drafts saved",
                    state="error",
                )
                st.error(str(exc))

        run = load_run(dataset_id)
    render_coverage_summary(run)
    _render_review(run)

    st.divider()

    # Reload because a review action may have changed counts.
    run = load_run(dataset_id)
    approved = verdict_counts(run)

    # can_export = all(
    #     approved[verdict] == 25
    #     for verdict in VERDICTS
    # )

    can_export = export_coverage_ready(run) 

    if st.button(
        "Export reviewed dataset",
        disabled=not can_export,
        key=f"evaluation_export_{dataset_id}",
    ):
        try:
            manifest = export_reviewed(dataset_id)

            st.success(
                f"Exported {manifest['claim_count']} "
                "reviewed claims to data/evaluation/."
            )

        except Exception as exc:
            st.error(str(exc))

    _render_downloads(run)

    if run["generation_log"]:
        with st.expander("Generation log"):
            st.json(run["generation_log"][-10:])


def _render_review(run: dict) -> None:
    dataset_id = run["dataset_id"]

    st.subheader("Review claims")

    status_filter = st.selectbox(
        "Review status",
        options=["draft", "approved", "rejected"],
        key=f"evaluation_filter_{dataset_id}",
    )

    records = [
        record
        for record in run["records"]
        if record["status"] == status_filter
    ]

    if not records:
        st.info(f"No {status_filter} claims.")
        return

    by_id = {
        record["id"]: record
        for record in records
    }

    def label(claim_id: str) -> str:
        candidate = by_id[claim_id]["candidate"]

        return (
            f"{candidate['verdict']} · "
            f"{candidate['claim_text'][:100]}"
        )

    claim_id = st.selectbox(
        "Claim to review",
        options=list(by_id),
        format_func=label,
        key=(
            f"evaluation_claim_{dataset_id}_"
            f"{status_filter}"
        ),
    )

    record = by_id[claim_id]
    candidate = record["candidate"]

    widget_prefix = (
        f"{dataset_id}_{claim_id}_{record['version']}"
    )

    st.write(candidate["claim_text"])

    st.caption(
        f"Document scope: {candidate['document_id']} · "
        f"Status: {record['status']}"
    )

    for index, subclaim in enumerate(
        candidate["subclaims"],
        start=1,
    ):
        st.markdown(
            f"**Subclaim {index}: {subclaim['verdict']}**"
        )
        st.text(subclaim["claim_text"])
        st.text(subclaim["reason"])

        if subclaim.get("calculation"):
            st.json(subclaim["calculation"])

    citation_ids = sorted(
        {
            evidence_id
            for subclaim in candidate["subclaims"]
            for evidence_id in subclaim["evidence_ids"]
        }
    )

    if st.checkbox(
        "Show cited evidence and source pages",
        key=f"evaluation_sources_{widget_prefix}",
    ):
        if citation_ids:
            render_evidence_panel(
                results=[
                    {
                        "claim_text": candidate["claim_text"],
                        "document_id": candidate["document_id"],
                        "evidence": [
                            run["evidence"][evidence_id]
                            for evidence_id in citation_ids
                        ],
                    }
                ],
                documents=run["documents"],
                selected_document_ids=run["document_ids"],
                render_page_callback=render_page,
            )
        else:
            st.info(
                "This draft has no citations. "
                "For UNKNOWN, inspect the complete scoped PDF."
            )

    scoped_document = next(
        document
        for document in run["documents"]
        if (
            document["document_id"]
            == candidate["document_id"]
        )
    )

    pdf_path = scoped_document["pdf_path"]

    from pathlib import Path

    if Path(pdf_path).exists():
        st.download_button(
            "Download scoped PDF for full-document review",
            data=Path(pdf_path).read_bytes(),
            file_name=(
                f"{candidate['document_id']}.pdf"
            ),
            mime="application/pdf",
            key=f"evaluation_pdf_{widget_prefix}",
        )

    st.caption(
        "Edit the JSON below to correct claim wording, "
        "overall/subclaim labels, citation IDs, explanations "
        "and expected calculations. Citation IDs must exist "
        "in this dataset snapshot."
    )

    with st.form(
        key=f"evaluation_review_{widget_prefix}"
    ):
        edited_json = st.text_area(
            "Editable candidate",
            value=json.dumps(
                candidate,
                indent=2,
                ensure_ascii=False,
            ),
            height=400,
        )

        reviewer = st.text_input(
            "Reviewer",
            value=record.get("reviewed_by") or "",
        )

        notes = st.text_area(
            "Review notes",
            value=record.get("review_notes") or "",
            height=90,
        )
        st.caption(
            "Coverage fields: straightforward claims use empty "
            "challenge_tags; isolated difficult claims use one "
            "challenge_tag; combined difficult claims use two or more. "
            "Every challenge_tag must also appear in case_tags. "
            "Undefined calculations require actual: null and a reason."
        )
        # source_checked = st.checkbox(
        #     "I checked the original source, reporting periods, "
        #     "units, chart/OCR readings and calculations."
        # )

        source_checked = st.checkbox(
            "I checked the original source, reporting periods, units, "
            "chart/OCR readings, calculations, numerical tolerances "
            "and any negative-baseline sign convention."
        )

        unknown_scope_checked = st.checkbox(
            "For any UNKNOWN subclaim, I checked the complete "
            "scoped PDF for supporting or conflicting evidence.",
            value=record.get(
                "unknown_scope_checked", False
            ),
        )

        save_draft = st.form_submit_button(
            "Save as draft"
        )

        approve = st.form_submit_button(
            "Approve",
            type="primary",
        )

        reject = st.form_submit_button(
            "Reject"
        )

    if not (save_draft or approve or reject):
        return

    try:
        value = json.loads(edited_json)

        status = (
            "approved"
            if approve
            else "rejected"
            if reject
            else "draft"
        )

        update_record(
            dataset_id,
            claim_id,
            value,
            status=status,
            reviewer=reviewer,
            notes=notes,
            source_checked=source_checked,
            unknown_scope_checked=unknown_scope_checked,
        )

        st.rerun()

    except Exception as exc:
        st.error(str(exc))


def _render_downloads(run: dict) -> None:
    manifest_path = ROOT / "manifest.json"

    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return

    if (
        manifest.get("dataset_id") != run["dataset_id"]
        or manifest.get("revision") != run["revision"]
    ):
        return

    st.caption(
        "Downloaded files contain approved records only."
    )

    for filename in (
        "claims.jsonl",
        "ground_truth.jsonl",
        "manifest.json",
    ):
        path = ROOT / filename

        if path.exists():
            st.download_button(
                f"Download {filename}",
                data=path.read_bytes(),
                file_name=filename,
                mime=(
                    "application/json"
                    if filename.endswith(".json")
                    else "application/x-ndjson"
                ),
                key=(
                    f"evaluation_download_"
                    f"{run['dataset_id']}_{filename}"
                ),
            )