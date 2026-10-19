from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any
from ui.calculation import render_calculation
from textwrap import dedent

import streamlit as st


VERDICT_CONFIG = {
    "SUPPORTED": {
        "icon": "✓",
        "label": "SUPPORTED",
        "colour": "#34d399",
        "background": "rgba(52, 211, 153, 0.10)",
    },
    "CONTRADICTED": {
        "icon": "✕",
        "label": "CONTRADICTED",
        "colour": "#fb7185",
        "background": "rgba(251, 113, 133, 0.10)",
    },
    "PARTIALLY_SUPPORTED": {
        "icon": "◐",
        "label": "PARTIALLY SUPPORTED",
        "colour": "#fbbf24",
        "background": "rgba(251, 191, 36, 0.10)",
    },
    "UNKNOWN": {
        "icon": "?",
        "label": "UNKNOWN",
        "colour": "#94a3b8",
        "background": "rgba(148, 163, 184, 0.10)",
    },
}

def render_verdict_html(
    html: str,
    *,
    unsafe_allow_html: bool = True,
) -> None:
    html = "".join(
        line.strip()
        for line in html.splitlines()
    )

    st.markdown(
        html,
        unsafe_allow_html=unsafe_allow_html,
    )
    
def render_verdicts(
    results: list[dict[str, Any]] | dict[str, Any] | None,
) -> None:
    """
    Render an overall verdict and expandable sub-claim verdicts.

    Supports:
    - Current claim_verifier.py audit dictionaries
    - Future verification-pipeline dictionaries
    - Single and compound claims
    """

    if not results:
        return

    if isinstance(results, dict):
        results = [results]

    normalized_results = [
        normalize_result(result)
        for result in results
    ]

    overall = aggregate_results(normalized_results)

    st.markdown("## Verification results")

    render_overall_verdict(overall)

    if len(normalized_results) == 1:
        render_visible_details(normalized_results[0])
        render_calculation(normalized_results[0])
        return

    st.markdown("### Individual claims")

    for index, result in enumerate(
        normalized_results,
        start=1,
    ):
        config = verdict_config(result["verdict"])

        title = (
            f"{config['icon']} Claim {index}: "
            f"{result['claim_text']}"
        )

        with st.expander(
            title,
            expanded=index == 1,
        ):
            render_subclaim_verdict(result)
            render_visible_details(result)
            render_calculation(result)


def render_overall_verdict(
    result: dict[str, Any],
) -> None:
    config = verdict_config(result["verdict"])
    confidence = result["confidence"]

    # st.markdown(
    render_verdict_html(
        dedent(
        f"""
        <div style="
            padding: 1.7rem;
            border-radius: 18px;
            border: 1px solid {config["colour"]};
            background: {config["background"]};
            box-shadow: 0 12px 35px rgba(0, 0, 0, 0.18);
            margin-bottom: 1rem;
        ">
            <div style="
                display:flex;
                justify-content:space-between;
                align-items:center;
                gap:1rem;
                flex-wrap:wrap;
            ">
                <div>
                    <div style="
                        color:{config["colour"]};
                        font-size:0.75rem;
                        font-weight:800;
                        letter-spacing:0.16em;
                        margin-bottom:0.35rem;
                    ">
                        OVERALL VERDICT
                    </div>

                    <div style="
                        color:{config["colour"]};
                        font-size:1.75rem;
                        font-weight:850;
                    ">
                        {config["icon"]} {config["label"]}
                    </div>
                </div>

                <div style="
                    text-align:right;
                    min-width:130px;
                ">
                    <div style="
                        color:#94a3b8;
                        font-size:0.8rem;
                    ">
                        Confidence
                    </div>

                    <div style="
                        font-size:1.65rem;
                        font-weight:800;
                    ">
                        {confidence:.0f}%
                    </div>
                </div>
            </div>

            <div style="
                height:8px;
                margin-top:1.2rem;
                border-radius:999px;
                background:rgba(148,163,184,0.18);
                overflow:hidden;
            ">
                <div style="
                    width:{confidence:.0f}%;
                    height:100%;
                    border-radius:999px;
                    background:{config["colour"]};
                "></div>
            </div>
        </div>
        """
        ),
        unsafe_allow_html=True,
        
    )

    if result.get("reason"):
        st.write(result["reason"])

    counts = result.get("verdict_counts") or {}

    if result.get("claim_count", 1) > 1:
        columns = st.columns(4)

        columns[0].metric(
            "Supported",
            counts.get("SUPPORTED", 0),
        )
        columns[1].metric(
            "Contradicted",
            counts.get("CONTRADICTED", 0),
        )
        columns[2].metric(
            "Partial",
            counts.get("PARTIALLY_SUPPORTED", 0),
        )
        columns[3].metric(
            "Unknown",
            counts.get("UNKNOWN", 0),
        )


def render_subclaim_verdict(
    result: dict[str, Any],
) -> None:
    config = verdict_config(result["verdict"])
    confidence = result["confidence"]

    # st.markdown(
    render_verdict_html(
        dedent(
        f"""
        <div style="
            padding:1rem 1.1rem;
            border-radius:14px;
            border-left:5px solid {config["colour"]};
            background:{config["background"]};
            margin-bottom:1rem;
        ">
            <div style="
                display:flex;
                justify-content:space-between;
                align-items:center;
                gap:1rem;
            ">
                <div style="
                    color:{config["colour"]};
                    font-size:1.15rem;
                    font-weight:800;
                ">
                    {config["icon"]} {config["label"]}
                </div>

                <div style="font-weight:700;">
                    {confidence:.0f}%
                </div>
            </div>

            <div style="
                height:6px;
                margin-top:0.8rem;
                border-radius:999px;
                background:rgba(148,163,184,0.18);
                overflow:hidden;
            ">
                <div style="
                    width:{confidence:.0f}%;
                    height:100%;
                    background:{config["colour"]};
                "></div>
            </div>
        </div>
        """
        ),
        unsafe_allow_html=True,
        
    )


def render_visible_details(
    result: dict[str, Any],
) -> None:
    metadata_col1, metadata_col2 = st.columns(2)

    metadata_col1.markdown(
        f"**Company:** {result['company_name']}"
    )
    metadata_col2.markdown(
        f"**Reporting period:** {result['reporting_period']}"
    )

    value_col1, value_col2 = st.columns(2)

    value_col1.metric(
        result.get("claimed_label", "Claimed value"),
        format_value(
            result.get("claimed_value"),
            result.get("unit"),
        ),
    )

    value_col2.metric(
        result.get("actual_label", "Actual value"),
        format_value(
            result.get("actual_value"),
            result.get("unit"),
        ),
        delta=format_difference(
            result.get("difference"),
            result.get("unit"),
        ),
        delta_color="off",
    )

    st.markdown("**Evidence-grounded explanation**")

    if result.get("reason"):
        st.write(result["reason"])
    else:
        st.caption(
            "No explanation was returned by the verification pipeline."
        )


# def render_calculation(
#     result: dict[str, Any],
# ) -> None:
#     calculation = result.get("calculation") or {}

#     if not calculation:
#         return

#     with st.expander(
#         "Calculation details",
#         expanded=False,
#     ):
#         old_value = calculation.get(
#             "old_value",
#             calculation.get("old"),
#         )
#         new_value = calculation.get(
#             "new_value",
#             calculation.get("new"),
#         )

#         if old_value is not None or new_value is not None:
#             col1, col2 = st.columns(2)

#             col1.metric(
#                 "Previous value",
#                 format_value(old_value),
#             )
#             col2.metric(
#                 "Current value",
#                 format_value(new_value),
#             )

#         formula = (
#             result.get("formula")
#             or calculation.get("formula")
#             or calculation.get("expression")
#         )

#         if formula:
#             st.code(str(formula), language=None)

#         if calculation.get("value") is not None:
#             st.markdown(
#                 "**Calculated result:** "
#                 f"{format_value(calculation['value'], result.get('unit'))}"
#             )


def normalize_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize current deterministic results and future pipeline results.
    """

    parsed_claim = result.get("parsed_claim") or {}
    comparison = result.get("comparison") or {}
    calculation = result.get("calculation") or {}

    verdict = normalize_verdict(
        result.get("verdict")
        or comparison.get("verdict")
        or "UNKNOWN"
    )

    confidence = normalize_confidence(
        result.get("confidence")
    )

    claimed_value = first_not_none(
        result.get("claimed_value"),
        result.get("claimed"),
        comparison.get("claimed"),
        parsed_claim.get("claimed_change"),
    )

    actual_value = first_not_none(
        result.get("actual_value"),
        result.get("actual"),
        comparison.get("actual"),
        calculation.get("value"),
    )

    difference = first_not_none(
        result.get("difference"),
        comparison.get("difference"),
    )

    start_period = first_not_none(
        result.get("start_period"),
        parsed_claim.get("start_period"),
    )

    end_period = first_not_none(
        result.get("end_period"),
        parsed_claim.get("end_period"),
    )

    reporting_period = (
        result.get("reporting_period")
        or build_reporting_period(
            start_period,
            end_period,
        )
    )

    company_name = (
        result.get("company_name")
        or result.get("company")
        or result.get("document_name")
        or "Not specified"
    )

    claim_text = (
        result.get("claim_text")
        or result.get("claim")
        or parsed_claim.get("claim_text")
        or "Claim"
    )

    reason = (
        result.get("reason")
        or result.get("explanation")
        or notes_to_reason(result.get("notes"))
    )

    unit = (
        result.get("unit")
        or parsed_claim.get("unit")
    )

    metric = (
        result.get("metric")
        or parsed_claim.get("metric")
        or "value"
    )

    return {
        "claim_text": str(claim_text),
        "verdict": verdict,
        "confidence": confidence,
        "claimed_value": claimed_value,
        "actual_value": actual_value,
        "difference": difference,
        "claimed_label": f"Claimed {metric}",
        "actual_label": f"Actual {metric}",
        "company_name": str(company_name),
        "reporting_period": reporting_period,
        "reason": reason,
        "unit": unit,
        "calculation": calculation,
        "formula": result.get("formula"),
        "evidence": (
            result.get("evidence")
            or result.get("evidence_items")
            or []
        ),
        "raw": result,
    }


def aggregate_results(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Aggregate individual claim verdicts.

    Rules:
    - Every result identical -> that verdict.
    - Mixed verdicts -> PARTIALLY_SUPPORTED.
    - No results -> UNKNOWN.
    """

    if not results:
        return {
            "verdict": "UNKNOWN",
            "confidence": 0.0,
            "reason": "No claim results were returned.",
            "claim_count": 0,
            "verdict_counts": {},
        }

    verdicts = [
        result["verdict"]
        for result in results
    ]

    counts = Counter(verdicts)

    if len(counts) == 1:
        overall_verdict = verdicts[0]
    else:
        overall_verdict = "PARTIALLY_SUPPORTED"

    confidence = sum(
        result["confidence"]
        for result in results
    ) / len(results)

    if len(results) == 1:
        reason = results[0].get("reason") or ""
    elif overall_verdict == "SUPPORTED":
        reason = (
            "All components of the compound claim are supported "
            "by the supplied evidence."
        )
    elif overall_verdict == "CONTRADICTED":
        reason = (
            "All components of the compound claim are contradicted "
            "by the supplied evidence."
        )
    elif overall_verdict == "UNKNOWN":
        reason = (
            "None of the claim components could be verified from "
            "the supplied evidence."
        )
    else:
        reason = (
            "The compound claim has mixed outcomes. Expand the "
            "individual claims to review each verdict."
        )

    return {
        "verdict": overall_verdict,
        "confidence": confidence,
        "reason": reason,
        "claim_count": len(results),
        "verdict_counts": dict(counts),
    }


def normalize_verdict(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value

    verdict = str(value or "UNKNOWN").upper()
    verdict = verdict.replace(" ", "_")

    if verdict not in VERDICT_CONFIG:
        return "UNKNOWN"

    return verdict


def normalize_confidence(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    if confidence <= 1:
        confidence *= 100

    return max(0.0, min(confidence, 100.0))


def build_reporting_period(
    start_period: Any,
    end_period: Any,
) -> str:
    if start_period is not None and end_period is not None:
        return f"{start_period}–{end_period}"

    if end_period is not None:
        return str(end_period)

    if start_period is not None:
        return str(start_period)

    return "Not specified"


def notes_to_reason(notes: Any) -> str:
    if not notes:
        return ""

    if isinstance(notes, str):
        return notes

    return " ".join(
        str(note)
        for note in notes
        if note
    )


def format_value(
    value: Any,
    unit: str | None = None,
) -> str:
    if value is None:
        return "—"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return escape(str(value))

    unit_lower = str(unit or "").lower()

    if unit_lower in {
        "percent",
        "percentage",
        "%",
        "percentage_points",
    }:
        return f"{number:,.2f}%"

    if unit_lower in {
        "dollars",
        "usd",
        "$",
    }:
        return f"${number:,.2f}"

    return f"{number:,.2f}"


def format_difference(
    difference: Any,
    unit: str | None = None,
) -> str | None:
    if difference is None:
        return None

    try:
        number = float(difference)
    except (TypeError, ValueError):
        return str(difference)

    unit_lower = str(unit or "").lower()

    if unit_lower in {
        "percent",
        "percentage",
        "%",
        "percentage_points",
    }:
        return f"{number:+,.2f} points"

    return f"{number:+,.2f}"


def first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def verdict_config(
    verdict: str,
) -> dict[str, str]:
    return VERDICT_CONFIG.get(
        verdict,
        VERDICT_CONFIG["UNKNOWN"],
    )
# from __future__ import annotation

# from typing import Any

# import streamlit as st


# VERDICT_STYLE = {
#     "SUPPORTED": {
#         "icon": "✓",
#         "label": "SUPPORTED",
#         "class": "verdict-supported",
#     },
#     "CONTRADICTED": {
#         "icon": "✕",
#         "label": "CONTRADICTED",
#         "class": "verdict-contradicted",
#     },
#     "PARTIALLY_SUPPORTED": {
#         "icon": "◐",
#         "label": "PARTIALLY SUPPORTED",
#         "class": "verdict-partially-supported",
#     },
#     "UNKNOWN": {
#         "icon": "?",
#         "label": "UNKNOWN",
#         "class": "verdict-unknown",
#     },
# }


# def render_verdicts(
#     results: list[dict[str, Any]] | dict[str, Any] | None,
# ) -> None:
#     """Render one or multiple verification results."""

#     if not results:
#         return

#     if isinstance(results, dict):
#         results = [results]

#     st.subheader("Verification results")

#     for index, result in enumerate(results, start=1):
#         normalized = normalize_result(result)

#         title = (
#             f"Claim {index}: {normalized['claim']}"
#             if len(results) > 1
#             else normalized["claim"]
#         )

#         with st.container():
#             st.markdown(f"#### {title}")
#             _render_verdict_card(normalized)
#             _render_result_details(normalized)


# def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
#     """
#     Normalize both the current deterministic audit schema and the future
#     reasoning-pipeline schema.
#     """

#     comparison = result.get("comparison") or {}
#     calculation = result.get("calculation") or {}
#     parsed_claim = result.get("parsed_claim") or {}

#     verdict = str(
#         result.get("verdict")
#         or comparison.get("verdict")
#         or "UNKNOWN"
#     ).upper()

#     confidence = result.get("confidence")
#     if confidence is not None and confidence <= 1:
#         confidence *= 100

#     claimed_value = (
#         result.get("claimed_value")
#         if result.get("claimed_value") is not None
#         else comparison.get(
#             "claimed",
#             parsed_claim.get("claimed_change"),
#         )
#     )

#     actual_value = (
#         result.get("actual_value")
#         if result.get("actual_value") is not None
#         else comparison.get(
#             "actual",
#             calculation.get("value"),
#         )
#     )

#     reason = (
#         result.get("reason")
#         or result.get("explanation")
#         or _reason_from_notes(result.get("notes"))
#     )

#     return {
#         "claim": (
#             result.get("claim")
#             or result.get("claim_text")
#             or parsed_claim.get("claim_text")
#             or "Claim"
#         ),
#         "verdict": verdict,
#         "confidence": confidence,
#         "reason": reason,
#         "claimed_value": claimed_value,
#         "actual_value": actual_value,
#         "difference": comparison.get("difference"),
#         "formula": (
#             result.get("formula")
#             or calculation.get("formula")
#             or calculation.get("expression")
#         ),
#         "calculation": calculation,
#         "processing_time": (
#             result.get("processing_time")
#             or result.get("processing_time_seconds")
#         ),
#         "evidence": (
#             result.get("evidence")
#             or result.get("evidence_items")
#             or []
#         ),
#         "evidence_source": result.get("evidence_source"),
#         "raw": result,
#     }


# def _render_verdict_card(result: dict[str, Any]) -> None:
#     style = VERDICT_STYLE.get(
#         result["verdict"],
#         VERDICT_STYLE["UNKNOWN"],
#     )

#     confidence_text = (
#         f"{result['confidence']:.0f}% confidence"
#         if result["confidence"] is not None
#         else "Confidence pending reasoning pipeline"
#     )

#     st.markdown(
#         f"""
#         <div class="ui-card {style['class']}">
#             <div style="font-size:1.55rem;font-weight:800;">
#                 {style['icon']} {style['label']}
#             </div>
#             <div class="small-muted" style="margin-top:0.35rem;">
#                 {confidence_text}
#             </div>
#         </div>
#         """,
#         unsafe_allow_html=True,
#     )

#     if result["reason"]:
#         st.write(result["reason"])


# def _render_result_details(result: dict[str, Any]) -> None:
#     col1, col2, col3, col4 = st.columns(4)

#     col1.metric(
#         "Claimed",
#         _format_value(result["claimed_value"]),
#     )
#     col2.metric(
#         "Actual",
#         _format_value(result["actual_value"]),
#     )
#     col3.metric(
#         "Difference",
#         _format_value(result["difference"]),
#     )
#     col4.metric(
#         "Processing time",
#         _format_time(result["processing_time"]),
#     )

#     calculation = result.get("calculation") or {}

#     if calculation or result.get("formula"):
#         with st.expander("Calculation", expanded=True):
#             old_value = calculation.get(
#                 "old_value",
#                 calculation.get("old"),
#             )
#             new_value = calculation.get(
#                 "new_value",
#                 calculation.get("new"),
#             )

#             detail_col1, detail_col2 = st.columns(2)
#             detail_col1.write(
#                 f"**Old value:** {_format_value(old_value)}"
#             )
#             detail_col2.write(
#                 f"**New value:** {_format_value(new_value)}"
#             )

#             if result.get("formula"):
#                 st.code(str(result["formula"]), language=None)

#             if calculation.get("value") is not None:
#                 st.write(
#                     "**Calculated result:** "
#                     f"{_format_value(calculation['value'])}"
#                 )


# def _reason_from_notes(notes: Any) -> str:
#     if not notes:
#         return ""
#     if isinstance(notes, str):
#         return notes
#     return " ".join(str(note) for note in notes)


# def _format_value(value: Any) -> str:
#     if value is None:
#         return "—"

#     if isinstance(value, float):
#         return f"{value:,.2f}"

#     if isinstance(value, int):
#         return f"{value:,}"

#     return str(value)


# def _format_time(value: Any) -> str:
#     if value is None:
#         return "—"

#     try:
#         return f"{float(value):.2f}s"
#     except (TypeError, ValueError):
#         return str(value)