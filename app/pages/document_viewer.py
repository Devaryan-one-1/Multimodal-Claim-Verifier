from __future__ import annotations

import streamlit as st

from extraction.image_renderer import render_page
from ui.document_viewer import render_document_viewer
from ui.sidebar import apply_theme


st.set_page_config(
    page_title="Document Viewer",
    page_icon="▧",
    layout="wide",
)

apply_theme()

documents = st.session_state.get(
    "upload_documents",
    [],
)

document_id = st.session_state.get(
    "viewer_document_id",
)

document = next(
    (
        item for item in documents
        if item.get("document_id") == document_id
    ),
    None,
)

if document is None:
    st.warning(
        "No document was selected. Return to the main page and "
        "choose Open document."
    )

    if st.button("Return to verifier"):
        # st.switch_page("app/ui_preview.py")
        st.switch_page("app/main.py")

    st.stop()

top_left, top_right = st.columns([4, 1])

with top_left:
    st.title(
        document.get("company_name")
        or document.get("filename")
        or "Document Viewer"
    )
    st.caption(document.get("filename", document_id))

with top_right:
    if st.button(
        "← Back to verifier",
        use_container_width=True,
    ):
        # st.switch_page("app/ui_preview.py")
        st.switch_page("app/main.py")

page_count = int(document.get("page_count") or 0)

render_document_viewer(
    document_id=document_id,
    page_count=page_count,
    initial_page=1,
    render_page_callback=render_page,
)