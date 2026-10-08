from __future__ import annotations

import streamlit as st

from evaluation.coverage import (
    CASE_TAGS,
    MODE_TARGETS,
    coverage_counts,
)


def render_coverage_summary(run: dict) -> None:
    approved = coverage_counts(run)
    drafts = coverage_counts(
        run,
        statuses=("draft",),
    )

    st.subheader("Difficulty coverage")

    st.dataframe(
        [
            {
                "Group": mode.replace("_", " ").title(),
                "Approved": approved["by_mode"][mode],
                "Draft": drafts["by_mode"][mode],
                "Target": target,
                "Still needed": max(
                    0,
                    target - approved["by_mode"][mode],
                ),
            }
            for mode, target in MODE_TARGETS.items()
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.caption(
        f"Approved straightforward: "
        f"{approved['straightforward']}/50 · "
        f"Approved difficult: {approved['difficult']}/50"
    )

    if approved["unclassified"] or drafts["unclassified"]:
        st.warning(
            "Some older records have no challenge classification. "
            "Add challenge_mode, case_tags and challenge_tags "
            "during review, or build a new dataset."
        )

    st.subheader("Difficult-case categories")

    st.dataframe(
        [
            {
                "Category": tag.replace("_", " ").title(),
                "Approved claims": approved["by_tag"][tag],
                "Draft claims": drafts["by_tag"][tag],
            }
            for tag in CASE_TAGS
        ],
        hide_index=True,
        use_container_width=True,
    )

    missing = [
        tag.replace("_", " ")
        for tag in CASE_TAGS
        if approved["by_tag"][tag] == 0
    ]

    if missing:
        st.info(
            "No approved examples yet for: "
            + ", ".join(missing)
            + ". Check whether the selected documents contain "
            "suitable evidence for these cases."
        )

    st.caption(
        "A claim can appear in several category counts. "
        "Only its challenge_mode determines whether it belongs "
        "to the straightforward, isolated or combined quota."
    )