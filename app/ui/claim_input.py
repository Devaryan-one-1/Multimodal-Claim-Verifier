from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import streamlit as st


VERIFICATION_STAGES = [
    "Parsing claim",
    "Resolving document scope",
    "Retrieving evidence",
    "Verifying calculations",
    "Reasoning over evidence",
    "Generating verdict",
]


@dataclass
class VerificationReporter:
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
        message: str | None = None,
    ) -> None:
        total = len(VERIFICATION_STAGES)
        completed = max(0, min(completed, total))

        fraction = completed / total
        elapsed = time.perf_counter() - self.started_at

        eta = None
        if completed > 0 and completed < total:
            eta = (elapsed / completed) * (total - completed)

        self.progress_bar.progress(
            fraction,
            text=f"{stage} ({completed}/{total})",
        )

        self.stage_placeholder.markdown(
            f"**Current stage:** {stage}"
        )

        eta_text = (
            format_duration(eta)
            if eta is not None
            else "Calculating…"
        )

        self.time_placeholder.caption(
            f"Elapsed: {format_duration(elapsed)} · "
            f"Estimated remaining: {eta_text}"
        )

        self.log(message or stage)

    def log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        elapsed = time.perf_counter() - self.started_at

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


def render_claim_input(
    selected_document_ids: list[str],
    documents: list[dict[str, Any]],
    verify_callback: Callable[..., Any] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Render claim input, recent history, verification progress, and retries.

    Preferred callback signature:

        verify_callback(
            claim_text=...,
            document_ids=...,
            reporter=...,
        ) -> list[dict] | dict
    """

    initialize_claim_state()

    st.markdown(
        '<div id="claim-input"></div>',
        unsafe_allow_html=True,
    )

    st.subheader("Verify a financial claim")

    render_recent_claims()
    render_document_scope(
        selected_document_ids=selected_document_ids,
        documents=documents,
    )

    with st.form(
        "claim_verification_form",
        clear_on_submit=False,
    ):
        st.text_area(
            "Enter a claim",
            key="claim_text_input",
            height=120,
            placeholder=(
                "Revenue grew by 25% in 2025.\n\n"
                "Compound example: Revenue grew by 25% and "
                "operating margin reached 30%."
            ),
        )

        submitted = st.form_submit_button(
            "VERIFY",
            type="primary",
            use_container_width=True,
            disabled=not selected_document_ids,
        )

    if not selected_document_ids:
        st.info(
            "Select at least one processed document before verifying."
        )

    results = None

    if submitted:
        claim_text = normalize_claim(
            st.session_state.claim_text_input
        )

        error = validate_claim(claim_text)

        if error:
            st.warning(error)
        elif verify_callback is None:
            st.error(
                "The verification callback has not been connected."
            )
        else:
            st.session_state.opened_history_results = None
            results = execute_verification(
                claim_text=claim_text,
                document_ids=selected_document_ids,
                verify_callback=verify_callback,
            )

            st.session_state.latest_verification_results = results

            add_history_entry(
                claim_text=claim_text,
                document_ids=selected_document_ids,
                results=results,
            )

    retry_results = render_failed_claim_retries(
        results=(
            results
            or st.session_state.latest_verification_results
        ),
        document_ids=selected_document_ids,
        verify_callback=verify_callback,
    )

    if retry_results is not None:
        results = retry_results

    if results is not None:
        return results

    return st.session_state.opened_history_results


def execute_verification(
    claim_text: str,
    document_ids: list[str],
    verify_callback: Callable[..., Any],
) -> list[dict[str, Any]]:
    with st.status(
        "Verifying claim",
        expanded=True,
    ) as status:
        stage_placeholder = st.empty()
        progress_bar = st.progress(0)
        time_placeholder = st.empty()

        with st.expander(
            "Technical verification log",
            expanded=False,
        ):
            log_placeholder = st.empty()

        reporter = VerificationReporter(
            progress_bar=progress_bar,
            stage_placeholder=stage_placeholder,
            time_placeholder=time_placeholder,
            log_placeholder=log_placeholder,
        )

        try:
            reporter.update(
                "Parsing claim",
                completed=1,
                message=f"Received claim: {claim_text}",
            )

            with st.spinner("Analyzing evidence..."):
                raw_results = call_verification_callback(
                    verify_callback=verify_callback,
                    claim_text=claim_text,
                    document_ids=document_ids,
                    reporter=reporter,
                )

            results = normalize_results(
                raw_results=raw_results,
                original_claim=claim_text,
                elapsed=reporter.elapsed_seconds,
            )

            reporter.update(
                "Generating verdict",
                completed=len(VERIFICATION_STAGES),
                message=(
                    f"Generated {len(results)} result(s)"
                ),
            )

            status.update(
                label="Verification completed",
                state="complete",
                expanded=False,
            )

            return results

        except Exception as exc:
            reporter.log(
                f"{type(exc).__name__}: {exc}"
            )

            status.update(
                label="Verification failed",
                state="error",
                expanded=True,
            )

            return [
                {
                    "claim_text": claim_text,
                    "status": "error",
                    "verdict": "UNKNOWN",
                    "confidence": 0.0,
                    "reason": (
                        "Verification could not be completed."
                    ),
                    "error": str(exc),
                    "notes": reporter.logs,
                    "processing_time_seconds": (
                        reporter.elapsed_seconds
                    ),
                }
            ]


def call_verification_callback(
    verify_callback: Callable[..., Any],
    claim_text: str,
    document_ids: list[str],
    reporter: VerificationReporter,
) -> Any:
    parameters = inspect.signature(
        verify_callback
    ).parameters

    arguments = {
        "claim_text": claim_text,
        "document_ids": document_ids,
    }

    if "reporter" in parameters:
        arguments["reporter"] = reporter

    return verify_callback(**arguments)


def normalize_results(
    raw_results: Any,
    original_claim: str,
    elapsed: float,
) -> list[dict[str, Any]]:
    if raw_results is None:
        raise ValueError(
            "The verification callback returned no result."
        )

    if isinstance(raw_results, dict):
        raw_results = [raw_results]

    if not isinstance(raw_results, list):
        raise TypeError(
            "The verification callback must return a dictionary "
            "or list of dictionaries."
        )

    normalized = []

    for result in raw_results:
        if not isinstance(result, dict):
            normalized.append(
                {
                    "claim_text": original_claim,
                    "status": "error",
                    "verdict": "UNKNOWN",
                    "error": (
                        "A sub-claim returned an invalid result."
                    ),
                    "reason": (
                        "The result was not a dictionary."
                    ),
                }
            )
            continue

        item = dict(result)

        item.setdefault(
            "claim_text",
            original_claim,
        )
        item.setdefault(
            "processing_time_seconds",
            elapsed,
        )
        item.setdefault(
            "status",
            (
                "error"
                if item.get("error")
                else "complete"
            ),
        )

        normalized.append(item)

    return normalized


def render_document_scope(
    selected_document_ids: list[str],
    documents: list[dict[str, Any]],
) -> None:
    document_map = {
        document.get("document_id"): document
        for document in documents
    }

    selected_documents = [
        document_map[document_id]
        for document_id in selected_document_ids
        if document_id in document_map
    ]

    with st.expander(
        f"Document scope ({len(selected_documents)})",
        expanded=False,
    ):
        if not selected_documents:
            st.caption("No documents selected.")
            return

        for document in selected_documents:
            company = (
                document.get("company_name")
                or "Unknown company"
            )
            filename = (
                document.get("filename")
                or document.get("document_id")
            )

            st.markdown(
                f"✓ **{company}**  \n"
                f"<span class='small-muted'>{filename}</span>",
                unsafe_allow_html=True,
            )

        st.caption(
            "If the claim names a company, the future verification "
            "pipeline will restrict retrieval to that company. "
            "Otherwise, all selected documents are searched."
        )


def render_recent_claims() -> None:
    history = st.session_state.claim_history

    if not history:
        return

    with st.expander(
        "Recent claims",
        expanded=False,
    ):
        for entry in reversed(history[-10:]):
            entry_id = entry["id"]
            claim = entry["claim_text"]
            verdict = history_verdict(entry["results"])

            st.markdown(
                f"**{verdict_icon(verdict)} {verdict}**  \n"
                f"{claim}"
            )

            refill_col, open_col = st.columns(2)

            if refill_col.button(
                "Refill claim",
                key=f"refill_claim_{entry_id}",
                use_container_width=True,
            ):
                st.session_state.claim_text_input = claim
                st.session_state.opened_history_results = None
                st.rerun()

            if open_col.button(
                "Open result",
                key=f"open_claim_{entry_id}",
                use_container_width=True,
            ):
                st.session_state.opened_history_results = (
                    entry["results"]
                )
                st.session_state.latest_verification_results = (
                    entry["results"]
                )
                st.rerun()

            st.divider()


def render_failed_claim_retries(
    results: list[dict[str, Any]] | None,
    document_ids: list[str],
    verify_callback: Callable[..., Any] | None,
) -> list[dict[str, Any]] | None:
    if not results or verify_callback is None:
        return None

    updated_results = list(results)
    changed = False

    for index, result in enumerate(results):
        failed = (
            result.get("status") == "error"
            or bool(result.get("error"))
        )

        if not failed:
            continue

        claim_text = result.get("claim_text", "Failed claim")

        with st.container(border=True):
            st.error(
                f"Could not verify: {claim_text}"
            )

            if result.get("error"):
                st.caption(str(result["error"]))

            if st.button(
                "Retry this claim",
                key=f"retry_claim_{index}_{claim_text}",
                use_container_width=True,
            ):
                retry_result = execute_verification(
                    claim_text=claim_text,
                    document_ids=result.get("document_ids") or document_ids,
                    verify_callback=verify_callback,
                )

                if len(retry_result) == 1:
                    updated_results[index] = retry_result[0]
                else:
                    updated_results[index:index + 1] = (
                        retry_result
                    )

                changed = True

    if changed:
        from services.application import aggregate_verdict
        for item in updated_results:
            item["overall_verdict"] = aggregate_verdict(updated_results)
        st.session_state.opened_history_results = None
        st.session_state.latest_verification_results = (
            updated_results
        )
        return updated_results

    return None


def add_history_entry(
    claim_text: str,
    document_ids: list[str],
    results: list[dict[str, Any]],
) -> None:
    st.session_state.claim_history.append(
        {
            "id": uuid.uuid4().hex,
            "claim_text": claim_text,
            "document_ids": list(document_ids),
            "results": results,
            "created_at": time.time(),
        }
    )

    st.session_state.claim_history = (
        st.session_state.claim_history[-50:]
    )


def history_verdict(results: list[dict[str, Any]]) -> str:
    from services.application import aggregate_verdict
    return aggregate_verdict(results)


def verdict_icon(verdict: str) -> str:
    return {
        "SUPPORTED": "✓",
        "CONTRADICTED": "✕",
        "PARTIALLY_SUPPORTED": "◐",
        "UNKNOWN": "?",
        "MIXED": "◆",
    }.get(verdict, "?")


def validate_claim(claim_text: str) -> str | None:
    if not claim_text:
        return "Enter a claim before selecting Verify."

    if len(claim_text) < 8:
        return "The claim is too short to verify reliably."

    if len(claim_text) > 5_000:
        return "The claim must be fewer than 5,000 characters."

    return None


def normalize_claim(claim_text: str) -> str:
    return " ".join((claim_text or "").split())


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"

    seconds = max(0, int(seconds))

    if seconds < 60:
        return f"{seconds}s"

    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{minutes}m {remaining_seconds}s"


def initialize_claim_state() -> None:
    st.session_state.setdefault(
        "claim_text_input",
        "",
    )
    st.session_state.setdefault(
        "claim_history",
        [],
    )
    st.session_state.setdefault(
        "latest_verification_results",
        [],
    )
    st.session_state.setdefault(
        "opened_history_results",
        None,
    )
# from __future__ import annotation

# from dataclasses import dataclass

# import streamlit as st


# @dataclass(frozen=True)
# class ClaimSubmission:
#     text: str
#     document_ids: list[str]


# def render_claim_input(
#     selected_document_ids: list[str],
#     disabled: bool = False,
# ) -> ClaimSubmission | None:
#     """Render the claim input and return a submission when Verify is clicked."""

#     st.subheader("Verify a financial claim")
#     st.caption(
#         "You may enter one claim or a compound claim. "
#         "Compound claims will receive separate verdicts."
#     )

#     with st.form("claim_verification_form", clear_on_submit=False):
#         claim_text = st.text_area(
#             "Claim",
#             height=110,
#             placeholder=(
#                 "Example: Revenue grew by 25% in 2025 and operating "
#                 "margin reached 30%."
#             ),
#         )

#         scope_text = (
#             f"{len(selected_document_ids)} document(s) selected"
#             if selected_document_ids
#             else "No processed documents selected"
#         )
#         st.caption(scope_text)

#         submitted = st.form_submit_button(
#             "Verify claim",
#             type="primary",
#             use_container_width=True,
#             disabled=disabled or not selected_document_ids,
#         )

#     if not submitted:
#         return None

#     cleaned_claim = " ".join(claim_text.split())

#     if not cleaned_claim:
#         st.warning("Enter a claim before starting verification.")
#         return None

#     if len(cleaned_claim) < 8:
#         st.warning("The claim is too short to verify reliably.")
#         return None

#     return ClaimSubmission(
#         text=cleaned_claim,
#         document_ids=selected_document_ids,
#     )
# from __future__ import annotation

# import inspect
# import time
# import uuid
# from dataclasses import dataclass, field
# from typing import Any, Callable

# import streamlit as st


# VERIFICATION_STAGES = [
#     "Parsing claim",
#     "Resolving document scope",
#     "Retrieving evidence",
#     "Verifying calculations",
#     "Reasoning over evidence",
#     "Generating verdict",
# ]


# @dataclass
# class VerificationReporter:
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
#         message: str | None = None,
#     ) -> None:
#         total = len(VERIFICATION_STAGES)
#         completed = max(0, min(completed, total))

#         fraction = completed / total
#         elapsed = time.perf_counter() - self.started_at

#         eta = None
#         if completed > 0 and completed < total:
#             eta = (elapsed / completed) * (total - completed)

#         self.progress_bar.progress(
#             fraction,
#             text=f"{stage} ({completed}/{total})",
#         )

#         self.stage_placeholder.markdown(
#             f"**Current stage:** {stage}"
#         )

#         eta_text = (
#             format_duration(eta)
#             if eta is not None
#             else "Calculating…"
#         )

#         self.time_placeholder.caption(
#             f"Elapsed: {format_duration(elapsed)} · "
#             f"Estimated remaining: {eta_text}"
#         )

#         self.log(message or stage)

#     def log(self, message: str) -> None:
#         timestamp = time.strftime("%H:%M:%S")
#         elapsed = time.perf_counter() - self.started_at

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


# def render_claim_input(
#     selected_document_ids: list[str],
#     documents: list[dict[str, Any]],
#     verify_callback: Callable[..., Any] | None = None,
# ) -> list[dict[str, Any]] | None:
#     """
#     Render claim input, recent history, verification progress, and retries.

#     Preferred callback signature:

#         verify_callback(
#             claim_text=...,
#             document_ids=...,
#             reporter=...,
#         ) -> list[dict] | dict
#     """

#     initialize_claim_state()

#     st.markdown(
#         '<div id="claim-input"></div>',
#         unsafe_allow_html=True,
#     )

#     st.subheader("Verify a financial claim")

#     render_recent_claims()
#     render_document_scope(
#         selected_document_ids=selected_document_ids,
#         documents=documents,
#     )

#     with st.form(
#         "claim_verification_form",
#         clear_on_submit=False,
#     ):
#         st.text_area(
#             "Enter a claim",
#             key="claim_text_input",
#             height=120,
#             placeholder=(
#                 "Revenue grew by 25% in 2025.\n\n"
#                 "Compound example: Revenue grew by 25% and "
#                 "operating margin reached 30%."
#             ),
#         )

#         submitted = st.form_submit_button(
#             "VERIFY",
#             type="primary",
#             use_container_width=True,
#             disabled=not selected_document_ids,
#         )

#     if not selected_document_ids:
#         st.info(
#             "Select at least one processed document before verifying."
#         )

#     results = None

#     if submitted:
#         claim_text = normalize_claim(
#             st.session_state.claim_text_input
#         )

#         error = validate_claim(claim_text)

#         if error:
#             st.warning(error)
#         elif verify_callback is None:
#             st.error(
#                 "The verification callback has not been connected."
#             )
#         else:
#             results = execute_verification(
#                 claim_text=claim_text,
#                 document_ids=selected_document_ids,
#                 verify_callback=verify_callback,
#             )

#             st.session_state.latest_verification_results = results

#             add_history_entry(
#                 claim_text=claim_text,
#                 document_ids=selected_document_ids,
#                 results=results,
#             )

#     retry_results = render_failed_claim_retries(
#         results=(
#             results
#             or st.session_state.latest_verification_results
#         ),
#         document_ids=selected_document_ids,
#         verify_callback=verify_callback,
#     )

#     if retry_results is not None:
#         results = retry_results

#     if results is not None:
#         return results

#     return st.session_state.opened_history_results


# def execute_verification(
#     claim_text: str,
#     document_ids: list[str],
#     verify_callback: Callable[..., Any],
# ) -> list[dict[str, Any]]:
#     with st.status(
#         "Verifying claim",
#         expanded=True,
#     ) as status:
#         stage_placeholder = st.empty()
#         progress_bar = st.progress(0)
#         time_placeholder = st.empty()

#         with st.expander(
#             "Technical verification log",
#             expanded=False,
#         ):
#             log_placeholder = st.empty()

#         reporter = VerificationReporter(
#             progress_bar=progress_bar,
#             stage_placeholder=stage_placeholder,
#             time_placeholder=time_placeholder,
#             log_placeholder=log_placeholder,
#         )

#         try:
#             reporter.update(
#                 "Parsing claim",
#                 completed=1,
#                 message=f"Received claim: {claim_text}",
#             )

#             with st.spinner("Analyzing evidence..."):
#                 raw_results = call_verification_callback(
#                     verify_callback=verify_callback,
#                     claim_text=claim_text,
#                     document_ids=document_ids,
#                     reporter=reporter,
#                 )

#             results = normalize_results(
#                 raw_results=raw_results,
#                 original_claim=claim_text,
#                 elapsed=reporter.elapsed_seconds,
#             )

#             reporter.update(
#                 "Generating verdict",
#                 completed=len(VERIFICATION_STAGES),
#                 message=(
#                     f"Generated {len(results)} result(s)"
#                 ),
#             )

#             status.update(
#                 label="Verification completed",
#                 state="complete",
#                 expanded=False,
#             )

#             return results

#         except Exception as exc:
#             reporter.log(
#                 f"{type(exc).__name__}: {exc}"
#             )

#             status.update(
#                 label="Verification failed",
#                 state="error",
#                 expanded=True,
#             )

#             return [
#                 {
#                     "claim_text": claim_text,
#                     "status": "error",
#                     "verdict": "UNKNOWN",
#                     "confidence": 0.0,
#                     "reason": (
#                         "Verification could not be completed."
#                     ),
#                     "error": str(exc),
#                     "notes": reporter.logs,
#                     "processing_time_seconds": (
#                         reporter.elapsed_seconds
#                     ),
#                 }
#             ]


# def call_verification_callback(
#     verify_callback: Callable[..., Any],
#     claim_text: str,
#     document_ids: list[str],
#     reporter: VerificationReporter,
# ) -> Any:
#     parameters = inspect.signature(
#         verify_callback
#     ).parameters

#     arguments = {
#         "claim_text": claim_text,
#         "document_ids": document_ids,
#     }

#     if "reporter" in parameters:
#         arguments["reporter"] = reporter

#     return verify_callback(**arguments)


# def normalize_results(
#     raw_results: Any,
#     original_claim: str,
#     elapsed: float,
# ) -> list[dict[str, Any]]:
#     if raw_results is None:
#         raise ValueError(
#             "The verification callback returned no result."
#         )

#     if isinstance(raw_results, dict):
#         raw_results = [raw_results]

#     if not isinstance(raw_results, list):
#         raise TypeError(
#             "The verification callback must return a dictionary "
#             "or list of dictionaries."
#         )

#     normalized = []

#     for result in raw_results:
#         if not isinstance(result, dict):
#             normalized.append(
#                 {
#                     "claim_text": original_claim,
#                     "status": "error",
#                     "verdict": "UNKNOWN",
#                     "error": (
#                         "A sub-claim returned an invalid result."
#                     ),
#                     "reason": (
#                         "The result was not a dictionary."
#                     ),
#                 }
#             )
#             continue

#         item = dict(result)

#         item.setdefault(
#             "claim_text",
#             original_claim,
#         )
#         item.setdefault(
#             "processing_time_seconds",
#             elapsed,
#         )
#         item.setdefault(
#             "status",
#             (
#                 "error"
#                 if item.get("error")
#                 else "complete"
#             ),
#         )

#         normalized.append(item)

#     return normalized


# def render_document_scope(
#     selected_document_ids: list[str],
#     documents: list[dict[str, Any]],
# ) -> None:
#     document_map = {
#         document.get("document_id"): document
#         for document in documents
#     }

#     selected_documents = [
#         document_map[document_id]
#         for document_id in selected_document_ids
#         if document_id in document_map
#     ]

#     with st.expander(
#         f"Document scope ({len(selected_documents)})",
#         expanded=False,
#     ):
#         if not selected_documents:
#             st.caption("No documents selected.")
#             return

#         for document in selected_documents:
#             company = (
#                 document.get("company_name")
#                 or "Unknown company"
#             )
#             filename = (
#                 document.get("filename")
#                 or document.get("document_id")
#             )

#             st.markdown(
#                 f"✓ **{company}**  \n"
#                 f"<span class='small-muted'>{filename}</span>",
#                 unsafe_allow_html=True,
#             )

#         st.caption(
#             "If the claim names a company, the future verification "
#             "pipeline will restrict retrieval to that company. "
#             "Otherwise, all selected documents are searched."
#         )


# def render_recent_claims() -> None:
#     history = st.session_state.claim_history

#     if not history:
#         return

#     with st.expander(
#         "Recent claims",
#         expanded=False,
#     ):
#         for entry in reversed(history[-10:]):
#             entry_id = entry["id"]
#             claim = entry["claim_text"]
#             verdict = history_verdict(entry["results"])

#             st.markdown(
#                 f"**{verdict_icon(verdict)} {verdict}**  \n"
#                 f"{claim}"
#             )

#             refill_col, open_col = st.columns(2)

#             if refill_col.button(
#                 "Refill claim",
#                 key=f"refill_claim_{entry_id}",
#                 use_container_width=True,
#             ):
#                 st.session_state.claim_text_input = claim
#                 st.session_state.opened_history_results = None
#                 st.rerun()

#             if open_col.button(
#                 "Open result",
#                 key=f"open_claim_{entry_id}",
#                 use_container_width=True,
#             ):
#                 st.session_state.opened_history_results = (
#                     entry["results"]
#                 )
#                 st.session_state.latest_verification_results = (
#                     entry["results"]
#                 )
#                 st.rerun()

#             st.divider()


# def render_failed_claim_retries(
#     results: list[dict[str, Any]] | None,
#     document_ids: list[str],
#     verify_callback: Callable[..., Any] | None,
# ) -> list[dict[str, Any]] | None:
#     if not results or verify_callback is None:
#         return None

#     updated_results = list(results)
#     changed = False

#     for index, result in enumerate(results):
#         failed = (
#             result.get("status") == "error"
#             or bool(result.get("error"))
#         )

#         if not failed:
#             continue

#         claim_text = result.get("claim_text", "Failed claim")

#         with st.container(border=True):
#             st.error(
#                 f"Could not verify: {claim_text}"
#             )

#             if result.get("error"):
#                 st.caption(str(result["error"]))

#             if st.button(
#                 "Retry this claim",
#                 key=f"retry_claim_{index}_{claim_text}",
#                 use_container_width=True,
#             ):
#                 retry_result = execute_verification(
#                     claim_text=claim_text,
#                     document_ids=document_ids,
#                     verify_callback=verify_callback,
#                 )

#                 if len(retry_result) == 1:
#                     updated_results[index] = retry_result[0]
#                 else:
#                     updated_results[index:index + 1] = (
#                         retry_result
#                     )

#                 changed = True

#     if changed:
#         st.session_state.latest_verification_results = (
#             updated_results
#         )
#         return updated_results

#     return None


# def add_history_entry(
#     claim_text: str,
#     document_ids: list[str],
#     results: list[dict[str, Any]],
# ) -> None:
#     st.session_state.claim_history.append(
#         {
#             "id": uuid.uuid4().hex,
#             "claim_text": claim_text,
#             "document_ids": list(document_ids),
#             "results": results,
#             "created_at": time.time(),
#         }
#     )

#     st.session_state.claim_history = (
#         st.session_state.claim_history[-50:]
#     )


# def history_verdict(
#     results: list[dict[str, Any]],
# ) -> str:
#     verdicts = {
#         str(result.get("verdict", "UNKNOWN")).upper()
#         for result in results
#     }

#     if len(verdicts) == 1:
#         return next(iter(verdicts))

#     if "CONTRADICTED" in verdicts:
#         return "MIXED"

#     if "PARTIALLY_SUPPORTED" in verdicts:
#         return "PARTIALLY_SUPPORTED"

#     return "MIXED"


# def verdict_icon(verdict: str) -> str:
#     return {
#         "SUPPORTED": "✓",
#         "CONTRADICTED": "✕",
#         "PARTIALLY_SUPPORTED": "◐",
#         "UNKNOWN": "?",
#         "MIXED": "◆",
#     }.get(verdict, "?")


# def validate_claim(claim_text: str) -> str | None:
#     if not claim_text:
#         return "Enter a claim before selecting Verify."

#     if len(claim_text) < 8:
#         return "The claim is too short to verify reliably."

#     if len(claim_text) > 5_000:
#         return "The claim must be fewer than 5,000 characters."

#     return None


# def normalize_claim(claim_text: str) -> str:
#     return " ".join((claim_text or "").split())


# def format_duration(seconds: float | None) -> str:
#     if seconds is None:
#         return "—"

#     seconds = max(0, int(seconds))

#     if seconds < 60:
#         return f"{seconds}s"

#     minutes, remaining_seconds = divmod(seconds, 60)
#     return f"{minutes}m {remaining_seconds}s"


# def initialize_claim_state() -> None:
#     st.session_state.setdefault(
#         "claim_text_input",
#         "",
#     )
#     st.session_state.setdefault(
#         "claim_history",
#         [],
#     )
#     st.session_state.setdefault(
#         "latest_verification_results",
#         [],
#     )
#     st.session_state.setdefault(
#         "opened_history_results",
#         None,
#     )
# # from __future__ import annotation

# # from dataclasses import dataclass

# # import streamlit as st


# # @dataclass(frozen=True)
# # class ClaimSubmission:
# #     text: str
# #     document_ids: list[str]


# # def render_claim_input(
# #     selected_document_ids: list[str],
# #     disabled: bool = False,
# # ) -> ClaimSubmission | None:
# #     """Render the claim input and return a submission when Verify is clicked."""

# #     st.subheader("Verify a financial claim")
# #     st.caption(
# #         "You may enter one claim or a compound claim. "
# #         "Compound claims will receive separate verdicts."
# #     )

# #     with st.form("claim_verification_form", clear_on_submit=False):
# #         claim_text = st.text_area(
# #             "Claim",
# #             height=110,
# #             placeholder=(
# #                 "Example: Revenue grew by 25% in 2025 and operating "
# #                 "margin reached 30%."
# #             ),
# #         )

# #         scope_text = (
# #             f"{len(selected_document_ids)} document(s) selected"
# #             if selected_document_ids
# #             else "No processed documents selected"
# #         )
# #         st.caption(scope_text)

# #         submitted = st.form_submit_button(
# #             "Verify claim",
# #             type="primary",
# #             use_container_width=True,
# #             disabled=disabled or not selected_document_ids,
# #         )

# #     if not submitted:
# #         return None

# #     cleaned_claim = " ".join(claim_text.split())

# #     if not cleaned_claim:
# #         st.warning("Enter a claim before starting verification.")
# #         return None

# #     if len(cleaned_claim) < 8:
# #         st.warning("The claim is too short to verify reliably.")
# #         return None

# #     return ClaimSubmission(
# #         text=cleaned_claim,
# #         document_ids=selected_document_ids,
# #     )