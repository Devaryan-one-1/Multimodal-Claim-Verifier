from __future__ import annotations

from typing import Any

import streamlit as st


def apply_theme() -> None:
    """Apply the professional neutral/futuristic application theme."""

    st.markdown(
        """
        <style>
        :root {
            --surface: rgba(20, 25, 36, 0.82);
            --surface-strong: rgba(28, 35, 49, 0.96);
            --border: rgba(125, 211, 252, 0.18);
            --accent: #38bdf8;
            --accent-secondary: #818cf8;
            --success: #34d399;
            --danger: #fb7185;
            --warning: #fbbf24;
            --muted: #94a3b8;
        }

        .stApp {
            background:
                radial-gradient(
                    circle at 15% 0%,
                    rgba(56, 189, 248, 0.09),
                    transparent 32%
                ),
                radial-gradient(
                    circle at 90% 10%,
                    rgba(129, 140, 248, 0.08),
                    transparent 28%
                ),
                #0b0f17;
        }

        [data-testid="stSidebar"] {
            background: rgba(12, 17, 27, 0.97);
            border-right: 1px solid var(--border);
        }

        div[data-testid="stMetric"] {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 0.8rem;
        }

        div[data-testid="stExpander"] {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
        }

        .ui-card {
            padding: 1rem 1.1rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            box-shadow: 0 12px 35px rgba(0, 0, 0, 0.18);
            margin-bottom: 0.9rem;
        }

        .status-chip {
            display: inline-block;
            padding: 0.22rem 0.65rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.02em;
        }

        .chip-ready {
            color: #a7f3d0;
            background: rgba(52, 211, 153, 0.12);
            border: 1px solid rgba(52, 211, 153, 0.25);
        }

        .chip-processing {
            color: #bae6fd;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.25);
        }

        .chip-error {
            color: #fecdd3;
            background: rgba(251, 113, 133, 0.12);
            border: 1px solid rgba(251, 113, 133, 0.25);
        }

        .verdict-supported {
            border-color: rgba(52, 211, 153, 0.55);
        }

        .verdict-contradicted {
            border-color: rgba(251, 113, 133, 0.55);
        }

        .verdict-partially-supported {
            border-color: rgba(251, 191, 36, 0.55);
        }

        .verdict-unknown {
            border-color: rgba(148, 163, 184, 0.45);
        }

        .small-muted {
            color: var(--muted);
            font-size: 0.86rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(
    documents: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Render navigation, document scope, processing status, API configuration,
    and recent verification history.

    API keys remain only in Streamlit session state.
    """

    documents = documents or []
    history = history or []

    with st.sidebar:
        st.title("Claim Verifier")
        st.caption("Multimodal financial intelligence")

        st.divider()
        st.subheader("Document scope")

        ready_documents = [
            item for item in documents
            if item.get("status", "ready") == "ready"
        ]

        labels = {
            _document_label(item): item["document_id"]
            for item in ready_documents
            if item.get("document_id")
        }

        selected_labels = st.multiselect(
            "Verify against",
            options=list(labels),
            default=list(labels),
            placeholder="Select one or more documents",
            help=(
                "If a company is named in the claim, the verification "
                "pipeline can narrow this scope automatically."
            ),
        )

        selected_document_ids = [
            labels[label] for label in selected_labels
        ]

        st.divider()
        st.subheader("Processing status")

        if not documents:
            st.caption("No documents uploaded.")
        else:
            for document in documents:
                _render_document_status(document)

        st.divider()
        with st.expander("Configuration", expanded=False):
            provider = st.selectbox(
                "Model provider",
                options=["OpenAI", "Other"],
                index=0,
            )

            api_key = st.text_input(
                "Temporary API key",
                type="password",
                key="temporary_api_key",
                help=(
                    "Stored only in this browser session and never written "
                    "to persistent history."
                ),
            )

            top_k = st.slider(
                "Evidence results",
                min_value=3,
                max_value=15,
                value=5,
            )

        st.divider()
        st.subheader("Recent verification")

        if not history:
            st.caption("No claims verified in this session.")
        else:
            for item in reversed(history[-8:]):
                verdict = str(item.get("verdict", "UNKNOWN"))
                claim = str(item.get("claim", "Untitled claim"))
                st.caption(f"{_verdict_icon(verdict)} {claim[:55]}")

    return {
        "selected_document_ids": selected_document_ids,
        "provider": provider,
        "api_key": api_key,
        "top_k": top_k,
    }


def _document_label(document: dict[str, Any]) -> str:
    company = document.get("company_name") or "Unknown company"
    filename = document.get("filename") or document.get("document_id", "")
    return f"{company} · {filename}"


def _render_document_status(document: dict[str, Any]) -> None:
    status = str(document.get("status", "ready")).lower()
    company = document.get("company_name") or "Detecting company"
    filename = document.get("filename") or document.get("document_id", "")

    icons = {
        "ready": "●",
        "processing": "◌",
        "error": "!",
        "queued": "○",
    }

    st.markdown(
        f"**{icons.get(status, '○')} {company}**  \n"
        f"<span class='small-muted'>{filename} · {status.title()}</span>",
        unsafe_allow_html=True,
    )


def _verdict_icon(verdict: str) -> str:
    return {
        "SUPPORTED": "✓",
        "CONTRADICTED": "✕",
        "PARTIALLY_SUPPORTED": "◐",
        "UNKNOWN": "?",
    }.get(verdict.upper(), "?")