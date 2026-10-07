from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import streamlit as st

from ui.document_viewer import (
    go_to_page,
    render_document_viewer,
)
from ui.evidence import (
    evidence_from_result,
    render_evidence,
)


def render_evidence_panel(
    results: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    selected_document_ids: list[str],
    render_page_callback: Callable[..., dict[str, Any]],
) -> None:
    """
    Render claim evidence beside the selected source page.
    """

    ready = {
        item["document_id"]: item
        for item in documents
        if (
            item.get("status") == "ready"
            and item.get("document_id")
        )
    }

    eligible = [
        document_id
        for document_id in selected_document_ids
        if document_id in ready
    ]

    default_document = (
        eligible[0]
        if len(eligible) == 1
        else None
    )

    groups = []

    for result in results:
        scoped = result.get("document_ids") or []

        default = (
            result.get("document_id")
            or (
                scoped[0]
                if len(scoped) == 1
                else default_document
            )
        )

        groups.append(
            evidence_from_result(
                result,
                default,
            )
        )

    signature = hashlib.sha256(
        json.dumps(
            [eligible, results],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()

    key = "selected_source_evidence"

    saved = st.session_state.get(key) or {}

    if saved.get("signature") != signature:
        st.session_state[key] = {
            "signature": signature,
            "evidence": None,
        }

    def select_evidence(page, evidence):
        document_id = evidence.get("document_id")
        document = ready.get(document_id)

        if (
            document is None
            or document_id not in eligible
        ):
            st.warning(
                "This citation does not identify "
                "a selected, ready document."
            )
            return

        if not (
            1
            <= page
            <= int(document.get("page_count", 0))
        ):
            st.warning(
                "The cited page is outside "
                "the source document."
            )
            return

        st.session_state[key] = {
            "signature": signature,
            "evidence": dict(evidence),
        }

        go_to_page(document_id, page)

    evidence_column, viewer_column = st.columns(
        [0.9, 1.4],
        gap="large",
    )

    with evidence_column:
        st.subheader("Evidence")

        for index, (result, items) in enumerate(
            zip(results, groups)
        ):
            if len(results) > 1:
                st.markdown(
                    f"**Claim {index + 1}:** "
                    f"{result.get('claim_text') or 'Claim'}"
                )

            render_evidence(
                items,
                on_view_page=select_evidence,
                claim=result.get("parsed_claim"),
                key_prefix=f"{signature}_{index}",
            )

    with viewer_column:
        evidence = (
            st.session_state[key].get("evidence")
        )

        if not evidence:
            st.subheader("Source document")

            st.info(
                "Select View Page on an evidence card "
                "to open and highlight its source."
            )
            return

        document_id = evidence["document_id"]
        document = ready[document_id]

        st.caption(
            document.get("filename")
            or document_id
        )

        render_document_viewer(
            document_id=document_id,
            page_count=int(document["page_count"]),
            initial_page=int(
                evidence.get("page")
                or evidence.get("page_number")
            ),
            evidence=evidence,
            render_page_callback=render_page_callback,
        )