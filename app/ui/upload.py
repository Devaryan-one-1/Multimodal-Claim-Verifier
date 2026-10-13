from __future__ import annotations

import hashlib
import inspect
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import streamlit as st


MAX_FILES = 10
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
PDF_SIGNATURE = b"%PDF-"

PROCESSING_STAGES = [
    "Saving document",
    "Extracting text",
    "Analyzing layout",
    "Extracting tables",
    "Detecting charts",
    "Building evidence",
    "Creating search index",
    "Finalizing",
]


@dataclass
class ProcessingReporter:
    filename: str
    progress_bar: Any
    stage_placeholder: Any
    time_placeholder: Any
    log_placeholder: Any
    started_at: float = field(default_factory=time.perf_counter)
    logs: list[str] = field(default_factory=list)

    def update(
        self,
        stage: str,
        completed: int,
        total: int = len(PROCESSING_STAGES),
        message: str | None = None,
    ) -> None:
        completed = max(0, min(completed, total))
        fraction = completed / total if total else 0

        elapsed = time.perf_counter() - self.started_at
        eta = calculate_eta(elapsed, completed, total)

        self.progress_bar.progress(
            fraction,
            text=f"{stage} ({completed}/{total})",
        )

        self.stage_placeholder.markdown(f"**Current stage:** {stage}")

        eta_text = (
            format_duration(eta)
            if eta is not None
            else "Calculating…"
        )

        self.time_placeholder.caption(
            f"Elapsed: {format_duration(elapsed)} · "
            f"Estimated remaining: {eta_text}"
        )

        timestamp = time.strftime("%H:%M:%S")
        log_message = message or stage
        self.logs.append(f"[{timestamp}] {log_message}")

        self.log_placeholder.code(
            "\n".join(self.logs[-100:]),
            language="text",
        )

    def log(self, message: str) -> None:
        elapsed = time.perf_counter() - self.started_at
        timestamp = time.strftime("%H:%M:%S")

        self.logs.append(
            f"[{timestamp}] +{elapsed:.1f}s {message}"
        )

        self.log_placeholder.code(
            "\n".join(self.logs[-100:]),
            language="text",
        )

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.started_at


def render_upload(
    process_document: Callable[..., dict[str, Any]] | None = None,
    permanently_delete_document: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Render the full multi-document upload interface.

    Preferred processing callback:

        process_document(
            file_bytes=...,
            filename=...,
            document_id=...,
            company_name=...,
            reporter=...,
        ) -> dict

    The callback result may contain:

        status
        company_name
        page_count
        text_extracted
        text_page_count
        table_count
        chart_count
        evidence_count
        processing_time_seconds
        pdf_path
    """

    initialize_upload_state()

    render_upload_header()

    uploaded_files = st.file_uploader(
        "Upload financial PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=f"pdf_upload_{st.session_state.get('upload_widget_version', 0)}",
        help=(
            "Upload up to 10 PDF files. Each file must be no larger "
            "than 10 MB."
        ),
    )

    render_limits()

    if uploaded_files:
        process_uploaded_files(
            uploaded_files=uploaded_files,
            process_document=process_document,
        )

    documents = st.session_state.upload_documents

    if documents:
        render_combined_summary(documents)

        st.markdown("### Documents")

        for document in list(documents):
            render_document_card(
                document=document,
                permanently_delete_document=permanently_delete_document,
            )

    return st.session_state.upload_documents


def render_upload_header() -> None:
    st.subheader("Upload financial documents")
    st.caption("Extract text, tables, charts and OCR evidence from your reports.")


def render_limits() -> None:
    st.caption(
        "PDF only · Maximum 10 documents · Maximum 10 MB per file"
    )


def process_uploaded_files(
    uploaded_files: list[Any],
    process_document: Callable[..., dict[str, Any]] | None,
) -> None:
    if len(uploaded_files) > MAX_FILES:
        st.error(
            f"You selected {len(uploaded_files)} files. "
            f"Only the first {MAX_FILES} will be considered."
        )
        uploaded_files = uploaded_files[:MAX_FILES]

    existing_fingerprints = {
        document.get("fingerprint")
        for document in st.session_state.upload_documents
    }

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.getvalue()
        fingerprint = hashlib.sha256(file_bytes).hexdigest()

        if fingerprint in existing_fingerprints:
            continue

        validation_error = validate_pdf(
            uploaded_file.name,
            file_bytes,
        )

        document_id = create_document_id(
            uploaded_file.name,
            fingerprint,
        )

        document = {
            "document_id": document_id,
            "filename": uploaded_file.name,
            "company_name": company_from_filename(
                uploaded_file.name
            ),
            "fingerprint": fingerprint,
            "size_bytes": len(file_bytes),
            "status": (
                "error"
                if validation_error
                else "processing"
            ),
            "error": validation_error,
            "logs": [],
            "page_count": 0,
            "text_extracted": False,
            "text_page_count": 0,
            "table_count": 0,
            "chart_count": 0,
            "evidence_count": 0,
        }

        st.session_state.upload_documents.append(document)
        existing_fingerprints.add(fingerprint)

        if validation_error:
            continue

        process_one_document(
            document=document,
            file_bytes=file_bytes,
            process_document=process_document,
        )


def process_one_document(
    document: dict[str, Any],
    file_bytes: bytes,
    process_document: Callable[..., dict[str, Any]] | None,
) -> None:
    filename = document["filename"]

    with st.status(
        f"Processing {filename}",
        expanded=True,
    ) as status:
        stage_placeholder = st.empty()
        progress_bar = st.progress(0)
        time_placeholder = st.empty()

        with st.expander(
            "Technical processing log",
            expanded=False,
        ):
            log_placeholder = st.empty()

        reporter = ProcessingReporter(
            filename=filename,
            progress_bar=progress_bar,
            stage_placeholder=stage_placeholder,
            time_placeholder=time_placeholder,
            log_placeholder=log_placeholder,
        )

        try:
            reporter.update(
                "Saving document",
                completed=1,
                message=f"Accepted {filename}",
            )

            if process_document is None:
                raise RuntimeError(
                    "No document-processing callback was provided."
                )

            result = call_processing_callback(
                process_document=process_document,
                document=document,
                file_bytes=file_bytes,
                reporter=reporter,
            )

            reporter.update(
                "Finalizing",
                completed=len(PROCESSING_STAGES),
                message="Document processing completed",
            )

            document.update(result or {})
            document["status"] = "ready"
            document["error"] = None
            document["logs"] = reporter.logs
            document["processing_time_seconds"] = (
                result.get("processing_time_seconds")
                if result
                and result.get("processing_time_seconds")
                is not None
                else reporter.elapsed_seconds
            )

            status.update(
                label=f"{filename} processed",
                state="complete",
                expanded=False,
            )

        except Exception as exc:
            document["status"] = "error"
            document["error"] = str(exc)
            document["logs"] = reporter.logs

            reporter.log(
                f"{type(exc).__name__}: {exc}"
            )

            status.update(
                label=f"{filename} failed",
                state="error",
                expanded=True,
            )


def call_processing_callback(
    process_document: Callable[..., dict[str, Any]],
    document: dict[str, Any],
    file_bytes: bytes,
    reporter: ProcessingReporter,
) -> dict[str, Any]:
    parameters = inspect.signature(
        process_document
    ).parameters

    arguments = {
        "file_bytes": file_bytes,
        "filename": document["filename"],
        "document_id": document["document_id"],
        "company_name": document["company_name"],
    }

    if "reporter" in parameters:
        arguments["reporter"] = reporter

    result = process_document(**arguments)

    if result is None:
        return {}

    if not isinstance(result, dict):
        raise TypeError(
            "process_document must return a dictionary."
        )

    return result


def render_combined_summary(
    documents: list[dict[str, Any]],
) -> None:
    ready = [
        document for document in documents
        if document.get("status") == "ready"
    ]
    processing = [
        document for document in documents
        if document.get("status") == "processing"
    ]
    failed = [
        document for document in documents
        if document.get("status") == "error"
    ]

    total_pages = sum(
        safe_int(document.get("page_count"))
        for document in ready
    )
    total_tables = sum(
        safe_int(document.get("table_count"))
        for document in ready
    )
    total_charts = sum(
        safe_int(document.get("chart_count"))
        for document in ready
    )

    st.markdown("### Processing summary")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Documents ready",
        f"{len(ready)}/{len(documents)}",
    )
    col2.metric("Pages", total_pages)
    col3.metric("Tables", total_tables)
    col4.metric("Charts", total_charts)

    if processing:
        st.info(
            f"{len(processing)} document(s) are processing."
        )

    if failed:
        st.warning(
            f"{len(failed)} document(s) failed. "
            "Other documents remain available."
        )


def render_document_card(
    document: dict[str, Any],
    permanently_delete_document: Callable[[str], None] | None,
) -> None:
    document_id = document["document_id"]
    status = document.get("status", "unknown")
    company_name = document.get(
        "company_name",
        "Unknown company",
    )
    filename = document.get("filename", document_id)

    icon = {
        "ready": "✓",
        "processing": "◌",
        "error": "!",
    }.get(status, "○")

    with st.container(border=True):
        header_col, state_col = st.columns([4, 1])

        with header_col:
            st.markdown(f"#### {icon} {company_name}")
            st.caption(filename)

        with state_col:
            st.markdown(
                f"**{status.replace('_', ' ').title()}**"
            )

        edited_company = st.text_input(
            "Company label",
            value=company_name,
            key=f"company_name_{document_id}",
            help=(
                "This editable label starts from the filename. "
                "Confirm it against the document; it is not extracted company identity."
            ),
        )

        document["company_name"] = edited_company.strip() or company_name

        if status == "ready":
            render_completed_statistics(document)
            render_completed_actions(
                document=document,
                permanently_delete_document=(
                    permanently_delete_document
                ),
            )

        elif status == "error":
            st.error(
                document.get("error")
                or "Document processing failed."
            )

            render_log_expander(document)
            render_removal_controls(
                document=document,
                permanently_delete_document=(
                    permanently_delete_document
                ),
            )

        elif status == "processing":
            st.info("This document is still processing.")


def render_completed_statistics(
    document: dict[str, Any],
) -> None:
    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Pages",
        document.get("page_count", "—"),
    )

    text_value = (
        document.get("text_page_count")
        or (
            "Complete"
            if document.get("text_extracted")
            else "—"
        )
    )
    col2.metric("Text extracted", text_value)

    col3.metric(
        "Tables",
        document.get("table_count", 0),
    )
    col4.metric(
        "Charts",
        document.get("chart_count", 0),
    )

    processing_time = document.get(
        "processing_time_seconds"
    )

    if processing_time is not None:
        st.caption(
            "Processing completed in "
            f"{format_duration(processing_time)}"
        )

    render_log_expander(document)


def render_completed_actions(
    document: dict[str, Any],
    permanently_delete_document: Callable[[str], None] | None,
) -> None:
    document_id = document["document_id"]

    verify_col, open_col, remove_col = st.columns(3)

    if verify_col.button(
        "Verify claims",
        key=f"verify_{document_id}",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.selected_document_ids = [
            document_id
        ]
        st.session_state.scroll_to_claim_input = True
        st.rerun()

    if open_col.button(
        "Open document",
        key=f"open_{document_id}",
        use_container_width=True,
    ):
        st.session_state.dashboard_viewer_document = document_id
        st.session_state.dashboard_citation = {}
        st.rerun()

    if remove_col.button(
        "Remove",
        key=f"remove_{document_id}",
        use_container_width=True,
    ):
        st.session_state[
            f"show_remove_options_{document_id}"
        ] = True

    if st.session_state.get(
        f"show_remove_options_{document_id}",
        False,
    ):
        render_removal_controls(
            document=document,
            permanently_delete_document=(
                permanently_delete_document
            ),
        )


def render_removal_controls(
    document: dict[str, Any],
    permanently_delete_document: Callable[[str], None] | None,
) -> None:
    document_id = document["document_id"]

    st.warning(
        "Choose whether to remove this document only from the "
        "current session or permanently delete its stored data."
    )

    session_col, permanent_col, cancel_col = st.columns(3)

    if session_col.button(
        "Session only",
        key=f"session_remove_{document_id}",
        use_container_width=True,
    ):
        remove_from_session(document_id)
        st.rerun()

    confirm_delete = permanent_col.checkbox(
        "Confirm deletion",
        key=f"confirm_delete_{document_id}",
    )

    if permanent_col.button(
        "Delete permanently",
        key=f"permanent_remove_{document_id}",
        disabled=not confirm_delete,
        use_container_width=True,
    ):
        if permanently_delete_document is None:
            st.error(
                "Permanent deletion is not connected to the "
                "storage service yet."
            )
        else:
            permanently_delete_document(document_id)
            remove_from_session(document_id)
            st.rerun()

    if cancel_col.button(
        "Cancel",
        key=f"cancel_remove_{document_id}",
        use_container_width=True,
    ):
        st.session_state[
            f"show_remove_options_{document_id}"
        ] = False
        st.rerun()


def render_log_expander(
    document: dict[str, Any],
) -> None:
    logs = document.get("logs") or []

    if not logs:
        return

    with st.expander(
        "Technical processing log",
        expanded=False,
    ):
        st.code(
            "\n".join(str(log) for log in logs),
            language="text",
        )


def remove_from_session(document_id: str) -> None:
    st.session_state.upload_widget_version = st.session_state.get("upload_widget_version", 0) + 1
    st.session_state.upload_documents = [
        document
        for document in st.session_state.upload_documents
        if document.get("document_id") != document_id
    ]

    selected = st.session_state.get(
        "selected_document_ids",
        [],
    )

    st.session_state.selected_document_ids = [
        item for item in selected
        if item != document_id
    ]


def validate_pdf(
    filename: str,
    file_bytes: bytes,
) -> str | None:
    if not filename.lower().endswith(".pdf"):
        return "Only PDF files are supported."

    if not file_bytes:
        return "The uploaded file is empty."

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        size_mb = len(file_bytes) / (1024 * 1024)
        return (
            f"{filename} is {size_mb:.1f} MB. "
            "The maximum file size is 10 MB."
        )

    if not file_bytes.startswith(PDF_SIGNATURE):
        return "The uploaded file does not have a valid PDF signature."

    return None


def create_document_id(
    filename: str,
    fingerprint: str,
) -> str:
    stem = Path(filename).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem)
    stem = stem.strip("-")[:45] or "document"
    return f"{stem}-{fingerprint[:10]}"


def company_from_filename(filename: str) -> str:
    stem = Path(filename).stem

    cleaned = re.sub(
        r"(?i)\b("
        r"annual|financial|report|form|10-k|10k|"
        r"fy|quarterly|results"
        r")\b",
        " ",
        stem,
    )
    cleaned = re.sub(r"\b20\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"[_\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned.title() or "Unknown company"


def calculate_eta(
    elapsed: float,
    completed: int,
    total: int,
) -> float | None:
    if completed <= 0 or completed >= total:
        return 0 if completed >= total else None

    average_per_stage = elapsed / completed
    return average_per_stage * (total - completed)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))

    if seconds < 60:
        return f"{seconds}s"

    minutes, remaining_seconds = divmod(seconds, 60)

    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"

    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes}m"


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def initialize_upload_state() -> None:
    st.session_state.setdefault(
        "upload_documents",
        [],
    )
    st.session_state.setdefault(
        "selected_document_ids",
        [],
    )
    st.session_state.setdefault(
        "scroll_to_claim_input",
        False,
    )
# from __future__ import annotations

# import hashlib
# import re
# from pathlib import Path
# from typing import Any, Callable

# import streamlit as st


# MAX_FILES = 10
# MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
# PDF_SIGNATURE = b"%PDF-"


# def render_upload(
#     process_document: Callable[..., dict[str, Any]] | None = None,
# ) -> list[dict[str, Any]]:
#     """
#     Render a multi-PDF uploader and process valid files immediately.

#     Expected callback signature:

#         process_document(
#             file_bytes=...,
#             filename=...,
#             document_id=...,
#             company_name=...,
#         ) -> dict

#     The callback should return document metadata including status, page_count,
#     table_count, and chart_count when available.
#     """

#     _initialize_upload_state()

#     st.subheader("Upload financial documents")
#     st.caption(
#         f"PDF only · Up to {MAX_FILES} files · "
#         "Maximum 10 MB per file"
#     )

#     uploaded_files = st.file_uploader(
#         "Choose annual reports or financial PDFs",
#         type=["pdf"],
#         accept_multiple_files=True,
#         label_visibility="collapsed",
#     )

#     if not uploaded_files:
#         return st.session_state.documents

#     if len(uploaded_files) > MAX_FILES:
#         st.error(
#             f"You selected {len(uploaded_files)} files. "
#             f"Only the first {MAX_FILES} can be processed."
#         )
#         uploaded_files = uploaded_files[:MAX_FILES]

#     for uploaded_file in uploaded_files:
#         file_bytes = uploaded_file.getvalue()
#         fingerprint = hashlib.sha256(file_bytes).hexdigest()

#         if fingerprint in st.session_state.processed_fingerprints:
#             continue

#         validation_error = validate_pdf(
#             filename=uploaded_file.name,
#             file_bytes=file_bytes,
#         )

#         if validation_error:
#             _register_failed_document(
#                 uploaded_file.name,
#                 fingerprint,
#                 validation_error,
#             )
#             continue

#         document_id = create_document_id(
#             uploaded_file.name,
#             fingerprint,
#         )

#         placeholder_company = company_from_filename(uploaded_file.name)

#         pending_document = {
#             "document_id": document_id,
#             "filename": uploaded_file.name,
#             "company_name": placeholder_company,
#             "fingerprint": fingerprint,
#             "size_bytes": len(file_bytes),
#             "status": "processing",
#             "error": None,
#         }

#         st.session_state.documents.append(pending_document)
#         st.session_state.processed_fingerprints.add(fingerprint)

#         with st.status(
#             f"Processing {uploaded_file.name}",
#             expanded=True,
#         ) as status:
#             st.write("Validating PDF")
#             st.write("Extracting text, tables, and document structure")

#             try:
#                 if process_document is None:
#                     result = {
#                         "status": "awaiting_pipeline",
#                         "message": (
#                             "Upload accepted. Connect document_pipeline.py "
#                             "to begin backend processing."
#                         ),
#                     }
#                 else:
#                     result = process_document(
#                         file_bytes=file_bytes,
#                         filename=uploaded_file.name,
#                         document_id=document_id,
#                         company_name=placeholder_company,
#                     )

#                 _update_document(
#                     document_id,
#                     {
#                         **(result or {}),
#                         "status": (
#                             result.get("status", "ready")
#                             if result
#                             else "ready"
#                         ),
#                     },
#                 )

#                 current = _find_document(document_id)

#                 if current.get("status") in {
#                     "ready",
#                     "completed",
#                     "processed",
#                 }:
#                     current["status"] = "ready"
#                     status.update(
#                         label=f"{uploaded_file.name} processed",
#                         state="complete",
#                         expanded=False,
#                     )
#                 elif current.get("status") == "awaiting_pipeline":
#                     status.update(
#                         label=f"{uploaded_file.name} accepted",
#                         state="complete",
#                         expanded=False,
#                     )
#                 else:
#                     status.update(
#                         label=f"{uploaded_file.name}: processing response",
#                         state="complete",
#                         expanded=False,
#                     )

#             except Exception as exc:
#                 _update_document(
#                     document_id,
#                     {
#                         "status": "error",
#                         "error": str(exc),
#                     },
#                 )
#                 status.update(
#                     label=f"{uploaded_file.name} failed",
#                     state="error",
#                     expanded=True,
#                 )
#                 st.error(str(exc))

#     _render_uploaded_documents(process_document)
#     return st.session_state.documents


# def validate_pdf(filename: str, file_bytes: bytes) -> str | None:
#     if not filename.lower().endswith(".pdf"):
#         return "Only PDF files are supported."

#     if not file_bytes:
#         return "The uploaded file is empty."

#     if len(file_bytes) > MAX_FILE_SIZE_BYTES:
#         size_mb = len(file_bytes) / (1024 * 1024)
#         return f"The file is {size_mb:.1f} MB; the limit is 10 MB."

#     if not file_bytes.startswith(PDF_SIGNATURE):
#         return "The file does not have a valid PDF signature."

#     return None


# def create_document_id(filename: str, fingerprint: str) -> str:
#     stem = Path(filename).stem.lower()
#     stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
#     stem = stem[:45] or "document"
#     return f"{stem}-{fingerprint[:10]}"


# def company_from_filename(filename: str) -> str:
#     stem = Path(filename).stem
#     cleaned = re.sub(
#         r"(?i)\b(annual|financial|report|10-k|10k|fy|form)\b",
#         " ",
#         stem,
#     )
#     cleaned = re.sub(r"[_\-]+", " ", cleaned)
#     cleaned = re.sub(r"\b20\d{2}\b", " ", cleaned)
#     cleaned = re.sub(r"\s+", " ", cleaned).strip()
#     return cleaned.title() or "Unknown company"


# def _render_uploaded_documents(
#     process_document: Callable[..., dict[str, Any]] | None,
# ) -> None:
#     if not st.session_state.documents:
#         return

#     st.markdown("#### Documents")

#     for index, document in enumerate(st.session_state.documents):
#         name = document.get("filename", "Document")
#         state = document.get("status", "unknown")

#         with st.expander(
#             f"{name} — {state.replace('_', ' ').title()}",
#             expanded=state == "error",
#         ):
#             company_name = st.text_input(
#                 "Company name",
#                 value=document.get("company_name", ""),
#                 key=f"company_{document['document_id']}",
#                 help="Edit this if automatic company detection was incorrect.",
#             )

#             if company_name != document.get("company_name"):
#                 st.session_state.documents[index][
#                     "company_name"
#                 ] = company_name

#             col1, col2, col3 = st.columns(3)
#             col1.metric(
#                 "Pages",
#                 document.get("page_count", "—"),
#             )
#             col2.metric(
#                 "Tables",
#                 document.get("table_count", "—"),
#             )
#             col3.metric(
#                 "Charts",
#                 document.get("chart_count", "—"),
#             )

#             if document.get("message"):
#                 st.info(document["message"])

#             if document.get("error"):
#                 st.error(document["error"])

#                 if st.button(
#                     "Retry processing",
#                     key=f"retry_{document['document_id']}",
#                     disabled=process_document is None,
#                 ):
#                     st.info(
#                         "Retry will be enabled when the document pipeline "
#                         "stores the uploaded file bytes."
#                     )


# def _initialize_upload_state() -> None:
#     st.session_state.setdefault("documents", [])
#     st.session_state.setdefault("processed_fingerprints", set())


# def _register_failed_document(
#     filename: str,
#     fingerprint: str,
#     error: str,
# ) -> None:
#     st.session_state.documents.append(
#         {
#             "document_id": create_document_id(filename, fingerprint),
#             "filename": filename,
#             "company_name": company_from_filename(filename),
#             "fingerprint": fingerprint,
#             "status": "error",
#             "error": error,
#         }
#     )
#     st.session_state.processed_fingerprints.add(fingerprint)


# def _find_document(document_id: str) -> dict[str, Any]:
#     for document in st.session_state.documents:
#         if document.get("document_id") == document_id:
#             return document
#     raise KeyError(f"Unknown document: {document_id}")


# def _update_document(
#     document_id: str,
#     updates: dict[str, Any],
# ) -> None:
#     document = _find_document(document_id)
#     document.update(updates)
# from __future__ import annotations

# import hashlib
# import inspect
# import re
# import time
# from dataclasses import dataclass, field
# from pathlib import Path
# from typing import Any, Callable

# import streamlit as st


# MAX_FILES = 10
# MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
# PDF_SIGNATURE = b"%PDF-"

# PROCESSING_STAGES = [
#     "Saving document",
#     "Extracting text",
#     "Analyzing layout",
#     "Extracting tables",
#     "Detecting charts",
#     "Building evidence",
#     "Creating search index",
#     "Finalizing",
# ]


# @dataclass
# class ProcessingReporter:
#     filename: str
#     progress_bar: Any
#     stage_placeholder: Any
#     time_placeholder: Any
#     log_placeholder: Any
#     started_at: float = field(default_factory=time.perf_counter)
#     logs: list[str] = field(default_factory=list)

#     def update(
#         self,
#         stage: str,
#         completed: int,
#         total: int = len(PROCESSING_STAGES),
#         message: str | None = None,
#     ) -> None:
#         completed = max(0, min(completed, total))
#         fraction = completed / total if total else 0

#         elapsed = time.perf_counter() - self.started_at
#         eta = calculate_eta(elapsed, completed, total)

#         self.progress_bar.progress(
#             fraction,
#             text=f"{stage} ({completed}/{total})",
#         )

#         self.stage_placeholder.markdown(f"**Current stage:** {stage}")

#         eta_text = (
#             format_duration(eta)
#             if eta is not None
#             else "Calculating…"
#         )

#         self.time_placeholder.caption(
#             f"Elapsed: {format_duration(elapsed)} · "
#             f"Estimated remaining: {eta_text}"
#         )

#         timestamp = time.strftime("%H:%M:%S")
#         log_message = message or stage
#         self.logs.append(f"[{timestamp}] {log_message}")

#         self.log_placeholder.code(
#             "\n".join(self.logs[-100:]),
#             language="text",
#         )

#     def log(self, message: str) -> None:
#         elapsed = time.perf_counter() - self.started_at
#         timestamp = time.strftime("%H:%M:%S")

#         self.logs.append(
#             f"[{timestamp}] +{elapsed:.1f}s {message}"
#         )

#         self.log_placeholder.code(
#             "\n".join(self.logs[-100:]),
#             language="text",
#         )

#     @property
#     def elapsed_seconds(self) -> float:
#         return time.perf_counter() - self.started_at


# def render_upload(
#     process_document: Callable[..., dict[str, Any]] | None = None,
#     permanently_delete_document: Callable[[str], None] | None = None,
# ) -> list[dict[str, Any]]:
#     """
#     Render the full multi-document upload interface.

#     Preferred processing callback:

#         process_document(
#             file_bytes=...,
#             filename=...,
#             document_id=...,
#             company_name=...,
#             reporter=...,
#         ) -> dict

#     The callback result may contain:

#         status
#         company_name
#         page_count
#         text_extracted
#         text_page_count
#         table_count
#         chart_count
#         evidence_count
#         processing_time_seconds
#         pdf_path
#     """

#     initialize_upload_state()

#     render_upload_header()

#     uploaded_files = st.file_uploader(
#         "Upload financial PDFs",
#         type=["pdf"],
#         accept_multiple_files=True,
#         label_visibility="collapsed",
#         help=(
#             "Upload up to 10 PDF files. Each file must be no larger "
#             "than 10 MB."
#         ),
#     )

#     render_limits()

#     if uploaded_files:
#         process_uploaded_files(
#             uploaded_files=uploaded_files,
#             process_document=process_document,
#         )

#     documents = st.session_state.upload_documents

#     if documents:
#         render_combined_summary(documents)

#         st.markdown("### Documents")

#         for document in list(documents):
#             render_document_card(
#                 document=document,
#                 permanently_delete_document=permanently_delete_document,
#             )

#     return st.session_state.upload_documents


# def render_upload_header() -> None:
#     st.markdown(
#         """
#         <div class="ui-card" style="
#             padding: 2rem;
#             text-align: center;
#             background:
#                 radial-gradient(
#                     circle at top,
#                     rgba(56,189,248,0.12),
#                     transparent 60%
#                 ),
#                 rgba(20,25,36,0.82);
#         ">
#             <div style="
#                 font-size: 0.78rem;
#                 letter-spacing: 0.18em;
#                 color: #38bdf8;
#                 font-weight: 700;
#             ">
#                 MULTIMODAL CLAIM VERIFIER
#             </div>
#             <h2 style="margin:0.55rem 0 0.25rem 0;">
#                 Upload Financial Documents
#             </h2>
#             <div class="small-muted">
#                 Analyze text, tables, charts, OCR, and financial evidence
#             </div>
#         </div>
#         """,
#         unsafe_allow_html=True,
#     )


# def render_limits() -> None:
#     st.caption(
#         "PDF only · Maximum 10 documents · Maximum 10 MB per file"
#     )


# def process_uploaded_files(
#     uploaded_files: list[Any],
#     process_document: Callable[..., dict[str, Any]] | None,
# ) -> None:
#     if len(uploaded_files) > MAX_FILES:
#         st.error(
#             f"You selected {len(uploaded_files)} files. "
#             f"Only the first {MAX_FILES} will be considered."
#         )
#         uploaded_files = uploaded_files[:MAX_FILES]

#     existing_fingerprints = {
#         document.get("fingerprint")
#         for document in st.session_state.upload_documents
#     }

#     for uploaded_file in uploaded_files:
#         file_bytes = uploaded_file.getvalue()
#         fingerprint = hashlib.sha256(file_bytes).hexdigest()

#         if fingerprint in existing_fingerprints:
#             continue

#         validation_error = validate_pdf(
#             uploaded_file.name,
#             file_bytes,
#         )

#         document_id = create_document_id(
#             uploaded_file.name,
#             fingerprint,
#         )

#         document = {
#             "document_id": document_id,
#             "filename": uploaded_file.name,
#             "company_name": company_from_filename(
#                 uploaded_file.name
#             ),
#             "fingerprint": fingerprint,
#             "size_bytes": len(file_bytes),
#             "status": (
#                 "error"
#                 if validation_error
#                 else "processing"
#             ),
#             "error": validation_error,
#             "logs": [],
#             "page_count": 0,
#             "text_extracted": False,
#             "text_page_count": 0,
#             "table_count": 0,
#             "chart_count": 0,
#             "evidence_count": 0,
#         }

#         st.session_state.upload_documents.append(document)
#         existing_fingerprints.add(fingerprint)

#         if validation_error:
#             continue

#         process_one_document(
#             document=document,
#             file_bytes=file_bytes,
#             process_document=process_document,
#         )


# def process_one_document(
#     document: dict[str, Any],
#     file_bytes: bytes,
#     process_document: Callable[..., dict[str, Any]] | None,
# ) -> None:
#     filename = document["filename"]

#     with st.status(
#         f"Processing {filename}",
#         expanded=True,
#     ) as status:
#         stage_placeholder = st.empty()
#         progress_bar = st.progress(0)
#         time_placeholder = st.empty()

#         with st.expander(
#             "Technical processing log",
#             expanded=False,
#         ):
#             log_placeholder = st.empty()

#         reporter = ProcessingReporter(
#             filename=filename,
#             progress_bar=progress_bar,
#             stage_placeholder=stage_placeholder,
#             time_placeholder=time_placeholder,
#             log_placeholder=log_placeholder,
#         )

#         try:
#             reporter.update(
#                 "Saving document",
#                 completed=1,
#                 message=f"Accepted {filename}",
#             )

#             if process_document is None:
#                 raise RuntimeError(
#                     "No document-processing callback was provided."
#                 )

#             result = call_processing_callback(
#                 process_document=process_document,
#                 document=document,
#                 file_bytes=file_bytes,
#                 reporter=reporter,
#             )

#             reporter.update(
#                 "Finalizing",
#                 completed=len(PROCESSING_STAGES),
#                 message="Document processing completed",
#             )

#             document.update(result or {})
#             document["status"] = "ready"
#             document["error"] = None
#             document["logs"] = reporter.logs
#             document["processing_time_seconds"] = (
#                 result.get("processing_time_seconds")
#                 if result
#                 and result.get("processing_time_seconds")
#                 is not None
#                 else reporter.elapsed_seconds
#             )

#             status.update(
#                 label=f"{filename} processed",
#                 state="complete",
#                 expanded=False,
#             )

#         except Exception as exc:
#             document["status"] = "error"
#             document["error"] = str(exc)
#             document["logs"] = reporter.logs

#             reporter.log(
#                 f"{type(exc).__name__}: {exc}"
#             )

#             status.update(
#                 label=f"{filename} failed",
#                 state="error",
#                 expanded=True,
#             )


# def call_processing_callback(
#     process_document: Callable[..., dict[str, Any]],
#     document: dict[str, Any],
#     file_bytes: bytes,
#     reporter: ProcessingReporter,
# ) -> dict[str, Any]:
#     parameters = inspect.signature(
#         process_document
#     ).parameters

#     arguments = {
#         "file_bytes": file_bytes,
#         "filename": document["filename"],
#         "document_id": document["document_id"],
#         "company_name": document["company_name"],
#     }

#     if "reporter" in parameters:
#         arguments["reporter"] = reporter

#     result = process_document(**arguments)

#     if result is None:
#         return {}

#     if not isinstance(result, dict):
#         raise TypeError(
#             "process_document must return a dictionary."
#         )

#     return result


# def render_combined_summary(
#     documents: list[dict[str, Any]],
# ) -> None:
#     ready = [
#         document for document in documents
#         if document.get("status") == "ready"
#     ]
#     processing = [
#         document for document in documents
#         if document.get("status") == "processing"
#     ]
#     failed = [
#         document for document in documents
#         if document.get("status") == "error"
#     ]

#     total_pages = sum(
#         safe_int(document.get("page_count"))
#         for document in ready
#     )
#     total_tables = sum(
#         safe_int(document.get("table_count"))
#         for document in ready
#     )
#     total_charts = sum(
#         safe_int(document.get("chart_count"))
#         for document in ready
#     )

#     st.markdown("### Processing summary")

#     col1, col2, col3, col4 = st.columns(4)

#     col1.metric(
#         "Documents ready",
#         f"{len(ready)}/{len(documents)}",
#     )
#     col2.metric("Pages", total_pages)
#     col3.metric("Tables", total_tables)
#     col4.metric("Charts", total_charts)

#     if processing:
#         st.info(
#             f"{len(processing)} document(s) are processing."
#         )

#     if failed:
#         st.warning(
#             f"{len(failed)} document(s) failed. "
#             "Other documents remain available."
#         )


# def render_document_card(
#     document: dict[str, Any],
#     permanently_delete_document: Callable[[str], None] | None,
# ) -> None:
#     document_id = document["document_id"]
#     status = document.get("status", "unknown")
#     company_name = document.get(
#         "company_name",
#         "Unknown company",
#     )
#     filename = document.get("filename", document_id)

#     icon = {
#         "ready": "✓",
#         "processing": "◌",
#         "error": "!",
#     }.get(status, "○")

#     with st.container(border=True):
#         header_col, state_col = st.columns([4, 1])

#         with header_col:
#             st.markdown(f"#### {icon} {company_name}")
#             st.caption(filename)

#         with state_col:
#             st.markdown(
#                 f"**{status.replace('_', ' ').title()}**"
#             )

#         edited_company = st.text_input(
#             "Company name",
#             value=company_name,
#             key=f"company_name_{document_id}",
#             help=(
#                 "Correct this if the company name was detected "
#                 "incorrectly."
#             ),
#         )

#         document["company_name"] = edited_company.strip() or company_name

#         if status == "ready":
#             render_completed_statistics(document)
#             render_completed_actions(
#                 document=document,
#                 permanently_delete_document=(
#                     permanently_delete_document
#                 ),
#             )

#         elif status == "error":
#             st.error(
#                 document.get("error")
#                 or "Document processing failed."
#             )

#             render_log_expander(document)
#             render_removal_controls(
#                 document=document,
#                 permanently_delete_document=(
#                     permanently_delete_document
#                 ),
#             )

#         elif status == "processing":
#             st.info("This document is still processing.")


# def render_completed_statistics(
#     document: dict[str, Any],
# ) -> None:
#     col1, col2, col3, col4 = st.columns(4)

#     col1.metric(
#         "Pages",
#         document.get("page_count", "—"),
#     )

#     text_value = (
#         document.get("text_page_count")
#         or (
#             "Complete"
#             if document.get("text_extracted")
#             else "—"
#         )
#     )
#     col2.metric("Text extracted", text_value)

#     col3.metric(
#         "Tables",
#         document.get("table_count", 0),
#     )
#     col4.metric(
#         "Charts",
#         document.get("chart_count", 0),
#     )

#     processing_time = document.get(
#         "processing_time_seconds"
#     )

#     if processing_time is not None:
#         st.caption(
#             "Processing completed in "
#             f"{format_duration(processing_time)}"
#         )

#     render_log_expander(document)


# def render_completed_actions(
#     document: dict[str, Any],
#     permanently_delete_document: Callable[[str], None] | None,
# ) -> None:
#     document_id = document["document_id"]

#     verify_col, open_col, remove_col = st.columns(3)

#     if verify_col.button(
#         "Verify claims",
#         key=f"verify_{document_id}",
#         type="primary",
#         use_container_width=True,
#     ):
#         st.session_state.selected_document_ids = [
#             document_id
#         ]
#         st.session_state.scroll_to_claim_input = True
#         st.rerun()

#     if open_col.button(
#         "Open document",
#         key=f"open_{document_id}",
#         use_container_width=True,
#     ):
#         st.session_state.viewer_document_id = document_id
#         st.switch_page("pages/document_viewer.py")

#     if remove_col.button(
#         "Remove",
#         key=f"remove_{document_id}",
#         use_container_width=True,
#     ):
#         st.session_state[
#             f"show_remove_options_{document_id}"
#         ] = True

#     if st.session_state.get(
#         f"show_remove_options_{document_id}",
#         False,
#     ):
#         render_removal_controls(
#             document=document,
#             permanently_delete_document=(
#                 permanently_delete_document
#             ),
#         )


# def render_removal_controls(
#     document: dict[str, Any],
#     permanently_delete_document: Callable[[str], None] | None,
# ) -> None:
#     document_id = document["document_id"]

#     st.warning(
#         "Choose whether to remove this document only from the "
#         "current session or permanently delete its stored data."
#     )

#     session_col, permanent_col, cancel_col = st.columns(3)

#     if session_col.button(
#         "Session only",
#         key=f"session_remove_{document_id}",
#         use_container_width=True,
#     ):
#         remove_from_session(document_id)
#         st.rerun()

#     confirm_delete = permanent_col.checkbox(
#         "Confirm deletion",
#         key=f"confirm_delete_{document_id}",
#     )

#     if permanent_col.button(
#         "Delete permanently",
#         key=f"permanent_remove_{document_id}",
#         disabled=not confirm_delete,
#         use_container_width=True,
#     ):
#         if permanently_delete_document is None:
#             st.error(
#                 "Permanent deletion is not connected to the "
#                 "storage service yet."
#             )
#         else:
#             permanently_delete_document(document_id)
#             remove_from_session(document_id)
#             st.rerun()

#     if cancel_col.button(
#         "Cancel",
#         key=f"cancel_remove_{document_id}",
#         use_container_width=True,
#     ):
#         st.session_state[
#             f"show_remove_options_{document_id}"
#         ] = False
#         st.rerun()


# def render_log_expander(
#     document: dict[str, Any],
# ) -> None:
#     logs = document.get("logs") or []

#     if not logs:
#         return

#     with st.expander(
#         "Technical processing log",
#         expanded=False,
#     ):
#         st.code(
#             "\n".join(str(log) for log in logs),
#             language="text",
#         )


# def remove_from_session(document_id: str) -> None:
#     st.session_state.upload_documents = [
#         document
#         for document in st.session_state.upload_documents
#         if document.get("document_id") != document_id
#     ]

#     selected = st.session_state.get(
#         "selected_document_ids",
#         [],
#     )

#     st.session_state.selected_document_ids = [
#         item for item in selected
#         if item != document_id
#     ]


# def validate_pdf(
#     filename: str,
#     file_bytes: bytes,
# ) -> str | None:
#     if not filename.lower().endswith(".pdf"):
#         return "Only PDF files are supported."

#     if not file_bytes:
#         return "The uploaded file is empty."

#     if len(file_bytes) > MAX_FILE_SIZE_BYTES:
#         size_mb = len(file_bytes) / (1024 * 1024)
#         return (
#             f"{filename} is {size_mb:.1f} MB. "
#             "The maximum file size is 10 MB."
#         )

#     if not file_bytes.startswith(PDF_SIGNATURE):
#         return "The uploaded file does not have a valid PDF signature."

#     return None


# def create_document_id(
#     filename: str,
#     fingerprint: str,
# ) -> str:
#     stem = Path(filename).stem.lower()
#     stem = re.sub(r"[^a-z0-9]+", "-", stem)
#     stem = stem.strip("-")[:45] or "document"
#     return f"{stem}-{fingerprint[:10]}"


# def company_from_filename(filename: str) -> str:
#     stem = Path(filename).stem

#     cleaned = re.sub(
#         r"(?i)\b("
#         r"annual|financial|report|form|10-k|10k|"
#         r"fy|quarterly|results"
#         r")\b",
#         " ",
#         stem,
#     )
#     cleaned = re.sub(r"\b20\d{2}\b", " ", cleaned)
#     cleaned = re.sub(r"[_\-]+", " ", cleaned)
#     cleaned = re.sub(r"\s+", " ", cleaned).strip()

#     return cleaned.title() or "Unknown company"


# def calculate_eta(
#     elapsed: float,
#     completed: int,
#     total: int,
# ) -> float | None:
#     if completed <= 0 or completed >= total:
#         return 0 if completed >= total else None

#     average_per_stage = elapsed / completed
#     return average_per_stage * (total - completed)


# def format_duration(seconds: float) -> str:
#     seconds = max(0, int(seconds))

#     if seconds < 60:
#         return f"{seconds}s"

#     minutes, remaining_seconds = divmod(seconds, 60)

#     if minutes < 60:
#         return f"{minutes}m {remaining_seconds}s"

#     hours, remaining_minutes = divmod(minutes, 60)
#     return f"{hours}h {remaining_minutes}m"


# def safe_int(value: Any) -> int:
#     try:
#         return int(value or 0)
#     except (TypeError, ValueError):
#         return 0


# def initialize_upload_state() -> None:
#     st.session_state.setdefault(
#         "upload_documents",
#         [],
#     )
#     st.session_state.setdefault(
#         "selected_document_ids",
#         [],
#     )
#     st.session_state.setdefault(
#         "scroll_to_claim_input",
#         False,
#     )
# # from __future__ import annotations

# # import hashlib
# # import re
# # from pathlib import Path
# # from typing import Any, Callable

# # import streamlit as st


# # MAX_FILES = 10
# # MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
# # PDF_SIGNATURE = b"%PDF-"


# # def render_upload(
# #     process_document: Callable[..., dict[str, Any]] | None = None,
# # ) -> list[dict[str, Any]]:
# #     """
# #     Render a multi-PDF uploader and process valid files immediately.

# #     Expected callback signature:

# #         process_document(
# #             file_bytes=...,
# #             filename=...,
# #             document_id=...,
# #             company_name=...,
# #         ) -> dict

# #     The callback should return document metadata including status, page_count,
# #     table_count, and chart_count when available.
# #     """

# #     _initialize_upload_state()

# #     st.subheader("Upload financial documents")
# #     st.caption(
# #         f"PDF only · Up to {MAX_FILES} files · "
# #         "Maximum 10 MB per file"
# #     )

# #     uploaded_files = st.file_uploader(
# #         "Choose annual reports or financial PDFs",
# #         type=["pdf"],
# #         accept_multiple_files=True,
# #         label_visibility="collapsed",
# #     )

# #     if not uploaded_files:
# #         return st.session_state.documents

# #     if len(uploaded_files) > MAX_FILES:
# #         st.error(
# #             f"You selected {len(uploaded_files)} files. "
# #             f"Only the first {MAX_FILES} can be processed."
# #         )
# #         uploaded_files = uploaded_files[:MAX_FILES]

# #     for uploaded_file in uploaded_files:
# #         file_bytes = uploaded_file.getvalue()
# #         fingerprint = hashlib.sha256(file_bytes).hexdigest()

# #         if fingerprint in st.session_state.processed_fingerprints:
# #             continue

# #         validation_error = validate_pdf(
# #             filename=uploaded_file.name,
# #             file_bytes=file_bytes,
# #         )

# #         if validation_error:
# #             _register_failed_document(
# #                 uploaded_file.name,
# #                 fingerprint,
# #                 validation_error,
# #             )
# #             continue

# #         document_id = create_document_id(
# #             uploaded_file.name,
# #             fingerprint,
# #         )

# #         placeholder_company = company_from_filename(uploaded_file.name)

# #         pending_document = {
# #             "document_id": document_id,
# #             "filename": uploaded_file.name,
# #             "company_name": placeholder_company,
# #             "fingerprint": fingerprint,
# #             "size_bytes": len(file_bytes),
# #             "status": "processing",
# #             "error": None,
# #         }

# #         st.session_state.documents.append(pending_document)
# #         st.session_state.processed_fingerprints.add(fingerprint)

# #         with st.status(
# #             f"Processing {uploaded_file.name}",
# #             expanded=True,
# #         ) as status:
# #             st.write("Validating PDF")
# #             st.write("Extracting text, tables, and document structure")

# #             try:
# #                 if process_document is None:
# #                     result = {
# #                         "status": "awaiting_pipeline",
# #                         "message": (
# #                             "Upload accepted. Connect document_pipeline.py "
# #                             "to begin backend processing."
# #                         ),
# #                     }
# #                 else:
# #                     result = process_document(
# #                         file_bytes=file_bytes,
# #                         filename=uploaded_file.name,
# #                         document_id=document_id,
# #                         company_name=placeholder_company,
# #                     )

# #                 _update_document(
# #                     document_id,
# #                     {
# #                         **(result or {}),
# #                         "status": (
# #                             result.get("status", "ready")
# #                             if result
# #                             else "ready"
# #                         ),
# #                     },
# #                 )

# #                 current = _find_document(document_id)

# #                 if current.get("status") in {
# #                     "ready",
# #                     "completed",
# #                     "processed",
# #                 }:
# #                     current["status"] = "ready"
# #                     status.update(
# #                         label=f"{uploaded_file.name} processed",
# #                         state="complete",
# #                         expanded=False,
# #                     )
# #                 elif current.get("status") == "awaiting_pipeline":
# #                     status.update(
# #                         label=f"{uploaded_file.name} accepted",
# #                         state="complete",
# #                         expanded=False,
# #                     )
# #                 else:
# #                     status.update(
# #                         label=f"{uploaded_file.name}: processing response",
# #                         state="complete",
# #                         expanded=False,
# #                     )

# #             except Exception as exc:
# #                 _update_document(
# #                     document_id,
# #                     {
# #                         "status": "error",
# #                         "error": str(exc),
# #                     },
# #                 )
# #                 status.update(
# #                     label=f"{uploaded_file.name} failed",
# #                     state="error",
# #                     expanded=True,
# #                 )
# #                 st.error(str(exc))

# #     _render_uploaded_documents(process_document)
# #     return st.session_state.documents


# # def validate_pdf(filename: str, file_bytes: bytes) -> str | None:
# #     if not filename.lower().endswith(".pdf"):
# #         return "Only PDF files are supported."

# #     if not file_bytes:
# #         return "The uploaded file is empty."

# #     if len(file_bytes) > MAX_FILE_SIZE_BYTES:
# #         size_mb = len(file_bytes) / (1024 * 1024)
# #         return f"The file is {size_mb:.1f} MB; the limit is 10 MB."

# #     if not file_bytes.startswith(PDF_SIGNATURE):
# #         return "The file does not have a valid PDF signature."

# #     return None


# # def create_document_id(filename: str, fingerprint: str) -> str:
# #     stem = Path(filename).stem.lower()
# #     stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
# #     stem = stem[:45] or "document"
# #     return f"{stem}-{fingerprint[:10]}"


# # def company_from_filename(filename: str) -> str:
# #     stem = Path(filename).stem
# #     cleaned = re.sub(
# #         r"(?i)\b(annual|financial|report|10-k|10k|fy|form)\b",
# #         " ",
# #         stem,
# #     )
# #     cleaned = re.sub(r"[_\-]+", " ", cleaned)
# #     cleaned = re.sub(r"\b20\d{2}\b", " ", cleaned)
# #     cleaned = re.sub(r"\s+", " ", cleaned).strip()
# #     return cleaned.title() or "Unknown company"


# # def _render_uploaded_documents(
# #     process_document: Callable[..., dict[str, Any]] | None,
# # ) -> None:
# #     if not st.session_state.documents:
# #         return

# #     st.markdown("#### Documents")

# #     for index, document in enumerate(st.session_state.documents):
# #         name = document.get("filename", "Document")
# #         state = document.get("status", "unknown")

# #         with st.expander(
# #             f"{name} — {state.replace('_', ' ').title()}",
# #             expanded=state == "error",
# #         ):
# #             company_name = st.text_input(
# #                 "Company name",
# #                 value=document.get("company_name", ""),
# #                 key=f"company_{document['document_id']}",
# #                 help="Edit this if automatic company detection was incorrect.",
# #             )

# #             if company_name != document.get("company_name"):
# #                 st.session_state.documents[index][
# #                     "company_name"
# #                 ] = company_name

# #             col1, col2, col3 = st.columns(3)
# #             col1.metric(
# #                 "Pages",
# #                 document.get("page_count", "—"),
# #             )
# #             col2.metric(
# #                 "Tables",
# #                 document.get("table_count", "—"),
# #             )
# #             col3.metric(
# #                 "Charts",
# #                 document.get("chart_count", "—"),
# #             )

# #             if document.get("message"):
# #                 st.info(document["message"])

# #             if document.get("error"):
# #                 st.error(document["error"])

# #                 if st.button(
# #                     "Retry processing",
# #                     key=f"retry_{document['document_id']}",
# #                     disabled=process_document is None,
# #                 ):
# #                     st.info(
# #                         "Retry will be enabled when the document pipeline "
# #                         "stores the uploaded file bytes."
# #                     )


# # def _initialize_upload_state() -> None:
# #     st.session_state.setdefault("documents", [])
# #     st.session_state.setdefault("processed_fingerprints", set())


# # def _register_failed_document(
# #     filename: str,
# #     fingerprint: str,
# #     error: str,
# # ) -> None:
# #     st.session_state.documents.append(
# #         {
# #             "document_id": create_document_id(filename, fingerprint),
# #             "filename": filename,
# #             "company_name": company_from_filename(filename),
# #             "fingerprint": fingerprint,
# #             "status": "error",
# #             "error": error,
# #         }
# #     )
# #     st.session_state.processed_fingerprints.add(fingerprint)


# # def _find_document(document_id: str) -> dict[str, Any]:
# #     for document in st.session_state.documents:
# #         if document.get("document_id") == document_id:
# #             return document
# #     raise KeyError(f"Unknown document: {document_id}")


# # def _update_document(
# #     document_id: str,
# #     updates: dict[str, Any],
# # ) -> None:
# #     document = _find_document(document_id)
# #     document.update(updates)