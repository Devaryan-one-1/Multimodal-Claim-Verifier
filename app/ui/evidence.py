from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

import streamlit as st


def render_evidence(
    evidence_items: list[dict[str, Any]] | None,
    on_view_page: Callable[[int, dict[str, Any]], None] | None = None,
    *,
    claim: dict[str, Any] | None = None,
    key_prefix: str = "evidence",
) -> int | None:
    """
    Show relevant table cells and provide a full-table toggle.

    Optional backend fields:
    - relevant_row_indices: zero-based indices into rows
    - relevant_years: years required for the claim
    - relevant_column_indices: zero-based indices, including
      the row-label column
    """

    if not evidence_items:
        st.info("No evidence details were returned for this claim.")
        return None

    selected_page = None

    for index, item in enumerate(evidence_items):
        evidence = dict(item)
        page = _page_number(evidence)

        kind = str(
            evidence.get("type")
            or evidence.get("modality")
            or evidence.get("kind")
            or "evidence"
        )

        source = (
            evidence.get("table_id")
            or evidence.get("chart_id")
            or evidence.get("evidence_id")
            or evidence.get("id")
            or f"Evidence {index + 1}"
        )

        key = hashlib.sha256(
            json.dumps(
                [
                    key_prefix,
                    index,
                    evidence.get("document_id"),
                    source,
                    page,
                ],
                default=str,
            ).encode()
        ).hexdigest()[:20]

        card_title = (
            f"{kind.replace('_', ' ').title()} "
            f"· Page {page or '—'} · {source}"
        )

        with st.expander(
            card_title,
            expanded=index == 0,
        ):
            if evidence.get("document_id"):
                st.caption(
                    f"Document: {evidence['document_id']}"
                )

            title = (
                evidence.get("title")
                or evidence.get("table_title")
                or evidence.get("chart_title")
            )

            if title:
                st.markdown(f"**{title}**")

            if evidence.get("rows"):
                _render_table(
                    evidence,
                    claim
                    or evidence.get("claim_context")
                    or {},
                    key,
                )
            else:
                content = (
                    evidence.get("content")
                    or evidence.get("text")
                    or evidence.get("description")
                    or evidence.get("caption")
                )

                if content:
                    # Plain text preserves financial dollar signs.
                    st.text(str(content))
                else:
                    st.caption(
                        "The backend returned a citation without "
                        "its text or table rows."
                    )

            if kind.lower() == "chart":
                image_path = (
                    evidence.get("image_path")
                    or evidence.get("chart_image_path")
                )

                if image_path and Path(image_path).is_file():
                    st.image(
                        str(image_path),
                        use_container_width=True,
                    )

            score = evidence.get(
                "rerank_score",
                evidence.get("score"),
            )

            try:
                if (
                    score is not None
                    and math.isfinite(float(score))
                ):
                    st.caption(
                        f"Retrieval score: {float(score):.3f}"
                    )
            except (TypeError, ValueError):
                pass

            if evidence.get("preview_only"):
                st.caption(
                    "UI preview evidence. It has not been verified "
                    "as supporting the entered claim."
                )

            can_view = (
                page is not None
                and bool(evidence.get("document_id"))
            )

            if st.button(
                "View Page",
                key=f"view_{key}",
                disabled=not can_view,
                use_container_width=True,
            ):
                selected_page = page

                if on_view_page:
                    on_view_page(page, evidence)

            if not can_view:
                st.caption(
                    "A document ID and valid page number are "
                    "required to open the source."
                )

    return selected_page


def evidence_from_result(
    result: dict[str, Any],
    default_document_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read evidence from the current audit or a future pipeline.

    A citation stub remains a stub; this function does not
    invent evidence content.
    """

    items = (
        result.get("evidence")
        or result.get("evidence_items")
        or result.get("retrieved_evidence")
    )

    fusion = result.get("fused_evidence") or {}

    if not items and isinstance(fusion, dict):
        items = fusion.get("ranked_evidence")

    if not items:
        source = result.get("evidence_source")

        items = (
            [source]
            if isinstance(source, dict)
            else []
        )

    if isinstance(items, dict):
        items = [items]

    if not isinstance(items, list):
        return []

    default_document_id = (
        result.get("document_id")
        or default_document_id
    )

    claim = result.get("parsed_claim") or {
        "claim_text": result.get("claim_text", "")
    }

    output = []

    for item in items:
        if not isinstance(item, dict):
            continue

        evidence = dict(item)

        if (
            not evidence.get("document_id")
            and default_document_id
        ):
            evidence["document_id"] = default_document_id

        evidence.setdefault("claim_context", claim)

        if (
            not evidence.get("bbox")
            and evidence.get("location")
        ):
            evidence["bbox"] = evidence["location"]

        output.append(evidence)

    return output


def _render_table(
    evidence: dict[str, Any],
    claim: dict[str, Any],
    key: str,
) -> None:
    rows = [
        list(row)
        for row in evidence.get("rows", [])
        if isinstance(row, (list, tuple))
    ]

    if not rows:
        st.caption("No usable table rows were returned.")
        return

    width = max(len(row) for row in rows)

    headers = _headers(evidence, width)

    selected_rows = _relevant_rows(
        rows,
        evidence,
        claim,
    )

    selected_columns = _relevant_columns(
        headers,
        evidence,
        claim,
    )

    if selected_rows:
        st.caption(
            "Preview rows and reporting periods"
            if evidence.get("preview_only")
            else "Relevant rows and reporting periods"
        )

        st.dataframe(
            _records(
                selected_rows,
                headers,
                selected_columns,
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption(
            "No specific row could be matched. Open the "
            "full table to inspect the evidence."
        )

    if st.checkbox(
        "Show full table",
        key=f"full_table_{key}",
    ):
        st.dataframe(
            _records(
                rows,
                headers,
                list(range(width)),
            ),
            hide_index=True,
            use_container_width=True,
        )

    if evidence.get("unit") or evidence.get("currency"):
        st.caption(
            f"Currency: {evidence.get('currency') or 'Not supplied'}"
            f" · Unit: {evidence.get('unit') or 'Not supplied'}"
        )


def _headers(
    evidence: dict[str, Any],
    width: int,
) -> list[str]:
    given = evidence.get("headers") or []

    headers = [
        str(given[i])
        if i < len(given) and given[i]
        else (
            "Line item"
            if i == 0
            else f"Column {i}"
        )
        for i in range(width)
    ]

    for column in evidence.get("column_map") or []:
        if not isinstance(column, dict):
            continue

        index = _integer(column.get("col"))

        if (
            index is not None
            and 0 < index < width
            and column.get("year") is not None
        ):
            period = (
                str(column.get("period") or "")
                .replace("_", " ")
                .strip()
            )

            headers[index] = (
                str(column["year"])
                + (f" · {period}" if period else "")
            )

    # Repeated years must not overwrite dataframe columns.
    used: dict[str, int] = {}
    output = []

    for label in headers:
        used[label] = used.get(label, 0) + 1

        output.append(
            label
            if used[label] == 1
            else f"{label} ({used[label]})"
        )

    return output


def _relevant_rows(rows, evidence, claim):
    explicit = evidence.get("relevant_row_indices")

    if isinstance(explicit, list):
        indices = [
            _integer(value)
            for value in explicit
        ]

        return [
            rows[i]
            for i in dict.fromkeys(indices)
            if i is not None and 0 <= i < len(rows)
        ]

    segment = _words(claim.get("segment"))
    metric = _words(claim.get("metric"))
    subject = _words(claim.get("subject"))

    target = segment | metric or subject

    if not target:
        return rows if len(rows) <= 3 else []

    matched = [
        row
        for row in rows
        if row and target <= _words(row[0])
    ]

    if not matched:
        return []

    exact = [
        row
        for row in matched
        if _words(row[0]) == target
    ]

    return exact or matched


def _relevant_columns(headers, evidence, claim):
    explicit = evidence.get("relevant_column_indices")

    if isinstance(explicit, list):
        indices = [
            _integer(value)
            for value in explicit
        ]

        return list(
            dict.fromkeys(
                [0]
                + [
                    i
                    for i in indices
                    if i is not None
                    and 0 <= i < len(headers)
                ]
            )
        )

    years = evidence.get("relevant_years") or [
        claim.get("start_period"),
        claim.get("end_period"),
    ]

    if not isinstance(years, (list, tuple, set)):
        years = [years]

    years = {
        str(year)
        for year in years
        if year is not None
    }

    if not years:
        return list(range(len(headers)))

    columns = [
        i
        for i, label in enumerate(headers)
        if i > 0
        and years.intersection(
            re.findall(
                r"\b(?:19|20)\d{2}\b",
                label,
            )
        )
    ]

    return (
        [0, *columns]
        if columns
        else list(range(len(headers)))
    )


def _records(rows, headers, columns):
    return [
        {
            headers[i]: (
                str(row[i])
                if i < len(row) and row[i] is not None
                else ""
            )
            for i in columns
        }
        for row in rows
    ]


def _words(value: Any) -> set[str]:
    return set(
        re.findall(
            r"[a-z0-9]+",
            str(value or "").casefold(),
        )
    )


def _integer(value: Any) -> int | None:
    try:
        if (
            value is None
            or isinstance(value, bool)
            or float(value) != int(value)
        ):
            return None

        return int(value)

    except (TypeError, ValueError, OverflowError):
        return None


def _page_number(
    evidence: dict[str, Any],
) -> int | None:
    page = _integer(
        evidence.get("page")
        or evidence.get("page_number")
    )

    return (
        page
        if page is not None and page > 0
        else None
    )
# from __future__ import annotations

# from typing import Any, Callable

# import streamlit as st


# def render_evidence(
#     evidence_items: list[dict[str, Any]] | None,
#     on_view_page: Callable[[int, dict[str, Any]], None] | None = None,
# ) -> int | None:
#     """
#     Render expandable evidence cards.

#     Returns the page number selected by the user.
#     """

#     evidence_items = evidence_items or []

#     st.subheader("Evidence")

#     if not evidence_items:
#         st.info(
#             "No detailed evidence cards were returned. The later "
#             "verification-pipeline integration must attach the retrieved "
#             "evidence objects to each result."
#         )
#         return None

#     selected_page = None

#     for index, evidence in enumerate(evidence_items, start=1):
#         page = _page_number(evidence)
#         evidence_type = str(
#             evidence.get("type")
#             or evidence.get("kind")
#             or "evidence"
#         ).title()

#         source_name = (
#             evidence.get("table_id")
#             or evidence.get("chart_id")
#             or evidence.get("id")
#             or evidence.get("evidence_id")
#             or f"Evidence {index}"
#         )

#         score = evidence.get(
#             "rerank_score",
#             evidence.get("score"),
#         )

#         title = f"{evidence_type} · Page {page or '—'} · {source_name}"

#         with st.expander(title, expanded=index == 1):
#             if score is not None:
#                 st.caption(f"Retrieval score: {float(score):.3f}")

#             content = (
#                 evidence.get("content")
#                 or evidence.get("text")
#                 or evidence.get("description")
#                 or evidence.get("caption")
#             )

#             if content:
#                 st.write(content)

#             _render_structured_table(evidence)

#             if evidence.get("bbox"):
#                 st.caption(
#                     f"Highlighted region: {evidence['bbox']}"
#                 )

#             if page is not None:
#                 if st.button(
#                     "View cited page",
#                     key=f"view_evidence_{index}_{page}",
#                     use_container_width=True,
#                 ):
#                     selected_page = page

#                     if on_view_page is not None:
#                         on_view_page(page, evidence)

#     return selected_page


# def evidence_from_result(
#     result: dict[str, Any],
# ) -> list[dict[str, Any]]:
#     """Extract evidence from either the current or future result schema."""

#     evidence = (
#         result.get("evidence")
#         or result.get("evidence_items")
#         or result.get("retrieved_evidence")
#     )

#     if evidence:
#         return evidence if isinstance(evidence, list) else [evidence]

#     source = result.get("evidence_source")

#     if source:
#         return [source]

#     return []


# def _page_number(evidence: dict[str, Any]) -> int | None:
#     value = (
#         evidence.get("page")
#         or evidence.get("page_number")
#     )

#     try:
#         return int(value)
#     except (TypeError, ValueError):
#         return None


# def _render_structured_table(evidence: dict[str, Any]) -> None:
#     headers = evidence.get("headers") or []
#     rows = evidence.get("rows") or []

#     if not rows:
#         return

#     if headers:
#         table_rows = []

#         for row in rows:
#             padded = list(row) + [""] * max(
#                 0,
#                 len(headers) - len(row),
#             )
#             table_rows.append(dict(zip(headers, padded)))

#         st.dataframe(
#             table_rows,
#             use_container_width=True,
#             hide_index=True,
#         )
#     else:
#         st.dataframe(
#             rows,
#             use_container_width=True,
#             hide_index=True,
#         )
# from __future__ import annotations

# import hashlib
# import json
# import math
# import re
# from pathlib import Path
# from typing import Any, Callable

# import streamlit as st


# def render_evidence(
#     evidence_items: list[dict[str, Any]] | None,
#     on_view_page: Callable[[int, dict[str, Any]], None] | None = None,
#     *,
#     claim: dict[str, Any] | None = None,
#     key_prefix: str = "evidence",
# ) -> int | None:
#     """
#     Show relevant table cells and provide a full-table toggle.

#     Optional backend fields:
#     - relevant_row_indices: zero-based indices into rows
#     - relevant_years: years required for the claim
#     - relevant_column_indices: zero-based indices, including
#       the row-label column
#     """

#     if not evidence_items:
#         st.info("No evidence details were returned for this claim.")
#         return None

#     selected_page = None

#     for index, item in enumerate(evidence_items):
#         evidence = dict(item)
#         page = _page_number(evidence)

#         kind = str(
#             evidence.get("type")
#             or evidence.get("modality")
#             or evidence.get("kind")
#             or "evidence"
#         )

#         source = (
#             evidence.get("table_id")
#             or evidence.get("chart_id")
#             or evidence.get("evidence_id")
#             or evidence.get("id")
#             or f"Evidence {index + 1}"
#         )

#         key = hashlib.sha256(
#             json.dumps(
#                 [
#                     key_prefix,
#                     index,
#                     evidence.get("document_id"),
#                     source,
#                     page,
#                 ],
#                 default=str,
#             ).encode()
#         ).hexdigest()[:20]

#         card_title = (
#             f"{kind.replace('_', ' ').title()} "
#             f"· Page {page or '—'} · {source}"
#         )

#         with st.expander(
#             card_title,
#             expanded=index == 0,
#         ):
#             if evidence.get("document_id"):
#                 st.caption(
#                     f"Document: {evidence['document_id']}"
#                 )

#             title = (
#                 evidence.get("title")
#                 or evidence.get("table_title")
#                 or evidence.get("chart_title")
#             )

#             if title:
#                 st.markdown(f"**{title}**")

#             if evidence.get("rows"):
#                 _render_table(
#                     evidence,
#                     claim
#                     or evidence.get("claim_context")
#                     or {},
#                     key,
#                 )
#             else:
#                 content = (
#                     evidence.get("content")
#                     or evidence.get("text")
#                     or evidence.get("description")
#                     or evidence.get("caption")
#                 )

#                 if content:
#                     # Plain text preserves financial dollar signs.
#                     st.text(str(content))
#                 else:
#                     st.caption(
#                         "The backend returned a citation without "
#                         "its text or table rows."
#                     )

#             if kind.lower() == "chart":
#                 image_path = (
#                     evidence.get("image_path")
#                     or evidence.get("chart_image_path")
#                 )

#                 if image_path and Path(image_path).is_file():
#                     st.image(
#                         str(image_path),
#                         use_container_width=True,
#                     )

#             score = evidence.get(
#                 "rerank_score",
#                 evidence.get("score"),
#             )

#             try:
#                 if (
#                     score is not None
#                     and math.isfinite(float(score))
#                 ):
#                     st.caption(
#                         f"Retrieval score: {float(score):.3f}"
#                     )
#             except (TypeError, ValueError):
#                 pass

#             if evidence.get("preview_only"):
#                 st.caption(
#                     "UI preview evidence. It has not been verified "
#                     "as supporting the entered claim."
#                 )

#             can_view = (
#                 page is not None
#                 and bool(evidence.get("document_id"))
#             )

#             if st.button(
#                 "View Page",
#                 key=f"view_{key}",
#                 disabled=not can_view,
#                 use_container_width=True,
#             ):
#                 selected_page = page

#                 if on_view_page:
#                     on_view_page(page, evidence)

#             if not can_view:
#                 st.caption(
#                     "A document ID and valid page number are "
#                     "required to open the source."
#                 )

#     return selected_page


# def evidence_from_result(
#     result: dict[str, Any],
#     default_document_id: str | None = None,
# ) -> list[dict[str, Any]]:
#     """
#     Read evidence from the current audit or a future pipeline.

#     A citation stub remains a stub; this function does not
#     invent evidence content.
#     """

#     items = (
#         result.get("evidence")
#         or result.get("evidence_items")
#         or result.get("retrieved_evidence")
#     )

#     fusion = result.get("fused_evidence") or {}

#     if not items and isinstance(fusion, dict):
#         items = fusion.get("ranked_evidence")

#     if not items:
#         source = result.get("evidence_source")

#         items = (
#             [source]
#             if isinstance(source, dict)
#             else []
#         )

#     if isinstance(items, dict):
#         items = [items]

#     if not isinstance(items, list):
#         return []

#     default_document_id = (
#         result.get("document_id")
#         or default_document_id
#     )

#     claim = result.get("parsed_claim") or {
#         "claim_text": result.get("claim_text", "")
#     }

#     output = []

#     for item in items:
#         if not isinstance(item, dict):
#             continue

#         evidence = dict(item)

#         if (
#             not evidence.get("document_id")
#             and default_document_id
#         ):
#             evidence["document_id"] = default_document_id

#         evidence.setdefault("claim_context", claim)

#         if (
#             not evidence.get("bbox")
#             and evidence.get("location")
#         ):
#             evidence["bbox"] = evidence["location"]

#         output.append(evidence)

#     return output


# def _render_table(
#     evidence: dict[str, Any],
#     claim: dict[str, Any],
#     key: str,
# ) -> None:
#     rows = [
#         list(row)
#         for row in evidence.get("rows", [])
#         if isinstance(row, (list, tuple))
#     ]

#     if not rows:
#         st.caption("No usable table rows were returned.")
#         return

#     width = max(len(row) for row in rows)

#     headers = _headers(evidence, width)

#     selected_rows = _relevant_rows(
#         rows,
#         evidence,
#         claim,
#     )

#     selected_columns = _relevant_columns(
#         headers,
#         evidence,
#         claim,
#     )

#     if selected_rows:
#         st.caption(
#             "Preview rows and reporting periods"
#             if evidence.get("preview_only")
#             else "Relevant rows and reporting periods"
#         )

#         st.dataframe(
#             _records(
#                 selected_rows,
#                 headers,
#                 selected_columns,
#             ),
#             hide_index=True,
#             use_container_width=True,
#         )
#     else:
#         st.caption(
#             "No specific row could be matched. Open the "
#             "full table to inspect the evidence."
#         )

#     if st.checkbox(
#         "Show full table",
#         key=f"full_table_{key}",
#     ):
#         st.dataframe(
#             _records(
#                 rows,
#                 headers,
#                 list(range(width)),
#             ),
#             hide_index=True,
#             use_container_width=True,
#         )

#     if evidence.get("unit") or evidence.get("currency"):
#         st.caption(
#             f"Currency: {evidence.get('currency') or 'Not supplied'}"
#             f" · Unit: {evidence.get('unit') or 'Not supplied'}"
#         )


# def _headers(
#     evidence: dict[str, Any],
#     width: int,
# ) -> list[str]:
#     given = evidence.get("headers") or []

#     headers = [
#         str(given[i])
#         if i < len(given) and given[i]
#         else (
#             "Line item"
#             if i == 0
#             else f"Column {i}"
#         )
#         for i in range(width)
#     ]

#     for column in evidence.get("column_map") or []:
#         if not isinstance(column, dict):
#             continue

#         index = _integer(column.get("col"))

#         if (
#             index is not None
#             and 0 < index < width
#             and column.get("year") is not None
#         ):
#             period = (
#                 str(column.get("period") or "")
#                 .replace("_", " ")
#                 .strip()
#             )

#             headers[index] = (
#                 str(column["year"])
#                 + (f" · {period}" if period else "")
#             )

#     # Repeated years must not overwrite dataframe columns.
#     used: dict[str, int] = {}
#     output = []

#     for label in headers:
#         used[label] = used.get(label, 0) + 1

#         output.append(
#             label
#             if used[label] == 1
#             else f"{label} ({used[label]})"
#         )

#     return output


# def _relevant_rows(rows, evidence, claim):
#     explicit = evidence.get("relevant_row_indices")

#     if isinstance(explicit, list):
#         indices = [
#             _integer(value)
#             for value in explicit
#         ]

#         return [
#             rows[i]
#             for i in dict.fromkeys(indices)
#             if i is not None and 0 <= i < len(rows)
#         ]

#     segment = _words(claim.get("segment"))
#     metric = _words(claim.get("metric"))
#     subject = _words(claim.get("subject"))

#     target = segment | metric or subject

#     if not target:
#         return rows if len(rows) <= 3 else []

#     matched = [
#         row
#         for row in rows
#         if row and target <= _words(row[0])
#     ]

#     if not matched:
#         return []

#     exact = [
#         row
#         for row in matched
#         if _words(row[0]) == target
#     ]

#     return exact or matched


# def _relevant_columns(headers, evidence, claim):
#     explicit = evidence.get("relevant_column_indices")

#     if isinstance(explicit, list):
#         indices = [
#             _integer(value)
#             for value in explicit
#         ]

#         return list(
#             dict.fromkeys(
#                 [0]
#                 + [
#                     i
#                     for i in indices
#                     if i is not None
#                     and 0 <= i < len(headers)
#                 ]
#             )
#         )

#     years = evidence.get("relevant_years") or [
#         claim.get("start_period"),
#         claim.get("end_period"),
#     ]

#     if not isinstance(years, (list, tuple, set)):
#         years = [years]

#     years = {
#         str(year)
#         for year in years
#         if year is not None
#     }

#     if not years:
#         return list(range(len(headers)))

#     columns = [
#         i
#         for i, label in enumerate(headers)
#         if i > 0
#         and years.intersection(
#             re.findall(
#                 r"\b(?:19|20)\d{2}\b",
#                 label,
#             )
#         )
#     ]

#     return (
#         [0, *columns]
#         if columns
#         else list(range(len(headers)))
#     )


# def _records(rows, headers, columns):
#     return [
#         {
#             headers[i]: (
#                 str(row[i])
#                 if i < len(row) and row[i] is not None
#                 else ""
#             )
#             for i in columns
#         }
#         for row in rows
#     ]


# def _words(value: Any) -> set[str]:
#     return set(
#         re.findall(
#             r"[a-z0-9]+",
#             str(value or "").casefold(),
#         )
#     )


# def _integer(value: Any) -> int | None:
#     try:
#         if (
#             value is None
#             or isinstance(value, bool)
#             or float(value) != int(value)
#         ):
#             return None

#         return int(value)

#     except (TypeError, ValueError, OverflowError):
#         return None


# def _page_number(
#     evidence: dict[str, Any],
# ) -> int | None:
#     page = _integer(
#         evidence.get("page")
#         or evidence.get("page_number")
#     )

#     return (
#         page
#         if page is not None and page > 0
#         else None
#     )
# # from __future__ import annotations

# # from typing import Any, Callable

# # import streamlit as st


# # def render_evidence(
# #     evidence_items: list[dict[str, Any]] | None,
# #     on_view_page: Callable[[int, dict[str, Any]], None] | None = None,
# # ) -> int | None:
# #     """
# #     Render expandable evidence cards.

# #     Returns the page number selected by the user.
# #     """

# #     evidence_items = evidence_items or []

# #     st.subheader("Evidence")

# #     if not evidence_items:
# #         st.info(
# #             "No detailed evidence cards were returned. The later "
# #             "verification-pipeline integration must attach the retrieved "
# #             "evidence objects to each result."
# #         )
# #         return None

# #     selected_page = None

# #     for index, evidence in enumerate(evidence_items, start=1):
# #         page = _page_number(evidence)
# #         evidence_type = str(
# #             evidence.get("type")
# #             or evidence.get("kind")
# #             or "evidence"
# #         ).title()

# #         source_name = (
# #             evidence.get("table_id")
# #             or evidence.get("chart_id")
# #             or evidence.get("id")
# #             or evidence.get("evidence_id")
# #             or f"Evidence {index}"
# #         )

# #         score = evidence.get(
# #             "rerank_score",
# #             evidence.get("score"),
# #         )

# #         title = f"{evidence_type} · Page {page or '—'} · {source_name}"

# #         with st.expander(title, expanded=index == 1):
# #             if score is not None:
# #                 st.caption(f"Retrieval score: {float(score):.3f}")

# #             content = (
# #                 evidence.get("content")
# #                 or evidence.get("text")
# #                 or evidence.get("description")
# #                 or evidence.get("caption")
# #             )

# #             if content:
# #                 st.write(content)

# #             _render_structured_table(evidence)

# #             if evidence.get("bbox"):
# #                 st.caption(
# #                     f"Highlighted region: {evidence['bbox']}"
# #                 )

# #             if page is not None:
# #                 if st.button(
# #                     "View cited page",
# #                     key=f"view_evidence_{index}_{page}",
# #                     use_container_width=True,
# #                 ):
# #                     selected_page = page

# #                     if on_view_page is not None:
# #                         on_view_page(page, evidence)

# #     return selected_page


# # def evidence_from_result(
# #     result: dict[str, Any],
# # ) -> list[dict[str, Any]]:
# #     """Extract evidence from either the current or future result schema."""

# #     evidence = (
# #         result.get("evidence")
# #         or result.get("evidence_items")
# #         or result.get("retrieved_evidence")
# #     )

# #     if evidence:
# #         return evidence if isinstance(evidence, list) else [evidence]

# #     source = result.get("evidence_source")

# #     if source:
# #         return [source]

# #     return []


# # def _page_number(evidence: dict[str, Any]) -> int | None:
# #     value = (
# #         evidence.get("page")
# #         or evidence.get("page_number")
# #     )

# #     try:
# #         return int(value)
# #     except (TypeError, ValueError):
# #         return None


# # def _render_structured_table(evidence: dict[str, Any]) -> None:
# #     headers = evidence.get("headers") or []
# #     rows = evidence.get("rows") or []

# #     if not rows:
# #         return

# #     if headers:
# #         table_rows = []

# #         for row in rows:
# #             padded = list(row) + [""] * max(
# #                 0,
# #                 len(headers) - len(row),
# #             )
# #             table_rows.append(dict(zip(headers, padded)))

# #         st.dataframe(
# #             table_rows,
# #             use_container_width=True,
# #             hide_index=True,
# #         )
# #     else:
# #         st.dataframe(
# #             rows,
# #             use_container_width=True,
# #             hide_index=True,
# #         )