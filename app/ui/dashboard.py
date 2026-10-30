from __future__ import annotations

from html import escape

import streamlit as st

from services.application import aggregate_verdict
from services.verification_status import execution_failed as _execution_failed
from ui.calculation import _format, render_calculation


CSS = """
<style>
:root { --brand: #2454ff; --lime: #d6ff58; --ink: #121315; }
.stApp { background: #f7f8fa; color: var(--ink); }
.block-container { max-width: 1540px; padding: 1.6rem 2.5rem 3rem; }
header[data-testid="stHeader"] { background: transparent; }
h1,h2,h3,h4 { letter-spacing: -0.035em; color: var(--ink); }
[data-testid="stSidebar"] { background: #edf0f6; border-right: 1px solid #dfe3eb; }
[data-testid="stVerticalBlock"] { gap: 0.8rem; }
[data-testid="stForm"] { background: white; border: 1px solid #e2e5eb; border-radius: 14px; padding: 1rem; }
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  color: white; background: #121315; border-color: #121315; border-radius: 9px;
  box-shadow: inset 4px 0 0 var(--lime); font-weight: 700;
}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
  background: #273045; border-color: #273045; color: white;
}
[data-testid="stExpander"] { background: white; border-color: #e0e4ec; border-radius: 12px; }
.cv-nav { background: #121315; color: white; padding: 18px 24px; border-radius: 14px 14px 0 0;
  display:flex; justify-content:space-between; align-items:center; gap:12px; }
.cv-logo { font-size: 1.25rem; font-weight: 800; letter-spacing: -0.035em; }
.cv-status { color: #d6ff58; font-size: .8rem; }
.st-key-dashboard_hero { background:#2454ff; color:white; padding:28px; border-radius:0 0 14px 14px; margin-bottom:20px; }
.st-key-dashboard_hero h1 { font-size:clamp(2.2rem,4vw,3.65rem); line-height:1.07; color:white; padding:0; margin:0 0 12px; }
.st-key-dashboard_hero p { color:#e9eeff; }
.st-key-dashboard_hero [data-testid="stBaseButton-secondary"] { background:#d6ff58; color:#111; border:0; font-weight:750; }
.cv-file { padding:14px 16px; background:rgba(255,255,255,.13); border:1px solid rgba(255,255,255,.2); border-radius:10px; margin-bottom:12px; }
.cv-file strong { display:block; color:white; overflow-wrap:anywhere; font-size:1rem; }
.cv-file small { display:block; color:#e0e6ff; padding-top:5px; }
.cv-eyebrow { font-size:.7rem; text-transform:uppercase; letter-spacing:.12em; font-weight:750; color:#60708d; }
.cv-verdict { background:white; border:1px solid #e2e5eb; border-radius:14px; padding:22px; margin:8px 0; }
.cv-verdict h2 { font-size:1.65rem; padding:8px 0; margin:0; overflow-wrap:anywhere; }
.cv-indicator { display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:10px; }
.cv-comparison { display:grid; grid-template-columns:1fr 1fr; margin-top:16px; gap:14px; }
.cv-comparison small { display:block; color:#6b7280; }
.cv-comparison strong { display:block; font-size:1.8rem; font-weight:800; letter-spacing:-.04em; }
.cv-actual { color:#2454ff; }
[class*="st-key-calculation_"] [data-testid="stExpander"] { background:#13161d; border:0; }
[class*="st-key-calculation_"] [data-testid="stExpander"] :is(p,h3,h4,label,span) { color:#e5e9f2; }
[class*="st-key-calculation_"] [data-testid="stCode"] { background:#222733; }
[class*="st-key-calculation_"] [data-testid="stCode"] span { color:#d6ff58; }
.st-key-source_panel { background:white; border:1px solid #e2e5eb; border-radius:14px; padding:18px; }
.st-key-source_panel [data-testid="stImage"] { border:1px solid #edf0f6; border-radius:8px; background:#f7f8fa; }
@media(max-width:700px) { .block-container { padding:1rem; } .st-key-dashboard_hero { padding:20px; }
  .cv-nav { padding:15px; } .cv-status { font-size:.7rem; } }
</style>
"""
COLORS = {"SUPPORTED": "#1f9d68", "CONTRADICTED": "#e44855", "PARTIALLY_SUPPORTED": "#d49a16", "UNKNOWN": "#8290a6"}


def apply_dashboard_style():
    st.markdown(CSS, unsafe_allow_html=True)


def render_nav(ready_count: int, configured: bool):
    state = f"{ready_count} documents ready" if configured else "API key required"
    st.markdown(f'<div class="cv-nav"><span class="cv-logo">Claim Verifier</span>'
                f'<span class="cv-status">{escape(state)}</span></div>', unsafe_allow_html=True)


def render_hero(document: dict | None, show_upload):
    with st.container(key="dashboard_hero"):
        heading, source = st.columns([1.65, 1], gap="large")
        with heading:
            st.markdown("# Claims meet evidence.")
            st.write("Check financial statements against the source.")
            st.caption("Text · Tables · Charts · OCR · Numerical verification")
        with source:
            if document:
                details = (f"{document.get('page_count', 0):,} pages · "
                           f"{document.get('table_count', 0):,} tables · "
                           f"{document.get('chart_count', 0):,} charts detected")
                st.markdown('<div class="cv-file"><strong>' + escape(document["filename"]) + '</strong><small>'
                            + escape(details) + '</small></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="cv-file"><strong>Start with a financial PDF</strong>'
                            '<small>Upload and process a report to verify claims.</small></div>', unsafe_allow_html=True)
            st.button("Manage / upload PDFs" if document else "Upload PDF", on_click=show_upload,
                      key="hero_upload", use_container_width=True)


def render_overall(results: list[dict]):
    if len(results) > 1:
        failed = sum(_execution_failed(r) for r in results)
        if failed:
            st.warning(f"Review incomplete: {failed} of {len(results)} claims could not be verified.")
            st.caption("Failed execution is not a factual UNKNOWN result. Retry the failed claims.")
        else:
            label = aggregate_verdict(results)
            st.markdown(f"### Overall: {label.replace('_', ' ')}")
            st.caption(f"{len(results)} atomic claims verified independently.")


def render_result(result: dict, index: int, multiple: bool):
    if _execution_failed(result):
        st.error("Verification failed — no factual verdict was established.")
        st.write(result.get("claim_text") or "")
        stage = result.get("error_stage") or "verification"
        if stage == "claim_parser" or result.get("parsing_error"):
            st.info("The claim could not be parsed. State the company, metric and exact reporting periods, then retry.")
        elif result.get("pause"):
            st.info("The processing service paused. Retry when the service is available.")
        else:
            st.info("Retry this claim. If it fails again, inspect the technical details below.")
        with st.expander("Technical details", expanded=False):
            st.write(result.get("error") or result.get("reason") or "The request did not complete.")
            st.caption(f"Stage: {stage}")
        unavailable = dict(result, calculation=None,
                           calculation_unavailable_reason="Verification did not complete.")
        with st.container(key=f"calculation_{index}"):
            render_calculation(unavailable)
        return
    verdict = result.get("verdict") or "UNKNOWN"
    color = COLORS.get(verdict, COLORS["UNKNOWN"])
    comparison = result.get("comparison") or {}
    calculation = result.get("calculation") or {}
    unit = comparison.get("unit") or calculation.get("result_unit")
    precision = calculation.get("result_precision")
    values = ""
    if comparison.get("claimed") is not None and comparison.get("actual") is not None:
        claimed = _format(comparison["claimed"], unit, precision)
        actual = _format(comparison["actual"], unit, precision)
        values = ('<div class="cv-comparison"><div><small>Claimed</small><strong>' + escape(claimed)
                  + '</strong></div><div><small>Actual</small><strong class="cv-actual">' + escape(actual) + '</strong></div></div>')
    st.markdown('<div class="cv-verdict"><div class="cv-eyebrow">'
                + (f"Claim {index + 1}" if multiple else "Verification result")
                + f'</div><h2><span class="cv-indicator" style="background:{color}"></span>'
                + escape(verdict.replace("_", " ")) + '</h2>' + values + '</div>', unsafe_allow_html=True)
    st.write(result.get("claim_text") or "")
    st.write(result.get("reason") or "No explanation was returned.")
    confidence = result.get("confidence")
    if confidence is not None:
        with st.expander("Model metadata", expanded=False):
            st.write({"uncalibrated_model_confidence": confidence})
            st.caption("This is not measured accuracy or a calibrated probability.")
    metadata = [result.get("company_name"), result.get("reporting_period")]
    st.caption(" · ".join(str(v) for v in metadata if v))
    with st.container(key=f"calculation_{index}"):
        render_calculation(result)
    notes = result.get("notes") or []
    if notes:
        with st.expander("Processing notes", expanded=False):
            for note in notes:
                st.write(note)

# from __future__ import annotations

# from html import escape

# import streamlit as st

# from services.application import aggregate_verdict
# from ui.calculation import _format, render_calculation


# CSS = """
# <style>
# :root { --brand: #2454ff; --lime: #d6ff58; --ink: #121315; }
# .stApp { background: #f7f8fa; color: var(--ink); }
# .block-container { max-width: 1540px; padding: 1.6rem 2.5rem 3rem; }
# header[data-testid="stHeader"] { background: transparent; }
# h1,h2,h3,h4 { letter-spacing: -0.035em; color: var(--ink); }
# [data-testid="stSidebar"] { background: #edf0f6; border-right: 1px solid #dfe3eb; }
# [data-testid="stVerticalBlock"] { gap: 0.8rem; }
# [data-testid="stForm"] { background: white; border: 1px solid #e2e5eb; border-radius: 14px; padding: 1rem; }
# [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
#   color: white; background: #121315; border-color: #121315; border-radius: 9px;
#   box-shadow: inset 4px 0 0 var(--lime); font-weight: 700;
# }
# [data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
#   background: #273045; border-color: #273045; color: white;
# }
# [data-testid="stExpander"] { background: white; border-color: #e0e4ec; border-radius: 12px; }
# .cv-nav { background: #121315; color: white; padding: 18px 24px; border-radius: 14px 14px 0 0;
#   display:flex; justify-content:space-between; align-items:center; gap:12px; }
# .cv-logo { font-size: 1.25rem; font-weight: 800; letter-spacing: -0.035em; }
# .cv-status { color: #d6ff58; font-size: .8rem; }
# .st-key-dashboard_hero { background:#2454ff; color:white; padding:28px; border-radius:0 0 14px 14px; margin-bottom:20px; }
# .st-key-dashboard_hero h1 { font-size:clamp(2.2rem,4vw,3.65rem); line-height:1.07; color:white; padding:0; margin:0 0 12px; }
# .st-key-dashboard_hero p { color:#e9eeff; }
# .st-key-dashboard_hero [data-testid="stBaseButton-secondary"] { background:#d6ff58; color:#111; border:0; font-weight:750; }
# .cv-file { padding:14px 16px; background:rgba(255,255,255,.13); border:1px solid rgba(255,255,255,.2); border-radius:10px; margin-bottom:12px; }
# .cv-file strong { display:block; color:white; overflow-wrap:anywhere; font-size:1rem; }
# .cv-file small { display:block; color:#e0e6ff; padding-top:5px; }
# .cv-eyebrow { font-size:.7rem; text-transform:uppercase; letter-spacing:.12em; font-weight:750; color:#60708d; }
# .cv-verdict { background:white; border:1px solid #e2e5eb; border-radius:14px; padding:22px; margin:8px 0; }
# .cv-verdict h2 { font-size:1.65rem; padding:8px 0; margin:0; overflow-wrap:anywhere; }
# .cv-indicator { display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:10px; }
# .cv-comparison { display:grid; grid-template-columns:1fr 1fr; margin-top:16px; gap:14px; }
# .cv-comparison small { display:block; color:#6b7280; }
# .cv-comparison strong { display:block; font-size:1.8rem; font-weight:800; letter-spacing:-.04em; }
# .cv-actual { color:#2454ff; }
# [class*="st-key-calculation_"] [data-testid="stExpander"] { background:#13161d; border:0; }
# [class*="st-key-calculation_"] [data-testid="stExpander"] :is(p,h3,h4,label,span) { color:#e5e9f2; }
# [class*="st-key-calculation_"] [data-testid="stCode"] { background:#222733; }
# [class*="st-key-calculation_"] [data-testid="stCode"] span { color:#d6ff58; }
# .st-key-source_panel { background:white; border:1px solid #e2e5eb; border-radius:14px; padding:18px; }
# .st-key-source_panel [data-testid="stImage"] { border:1px solid #edf0f6; border-radius:8px; background:#f7f8fa; }
# @media(max-width:700px) { .block-container { padding:1rem; } .st-key-dashboard_hero { padding:20px; }
#   .cv-nav { padding:15px; } .cv-status { font-size:.7rem; } }
# </style>
# """
# COLORS = {"SUPPORTED": "#1f9d68", "CONTRADICTED": "#e44855", "PARTIALLY_SUPPORTED": "#d49a16", "UNKNOWN": "#8290a6"}


# def apply_dashboard_style():
#     st.markdown(CSS, unsafe_allow_html=True)


# def render_nav(ready_count: int, configured: bool):
#     state = f"{ready_count} documents ready" if configured else "API key required"
#     st.markdown(f'<div class="cv-nav"><span class="cv-logo">Claim Verifier</span>'
#                 f'<span class="cv-status">{escape(state)}</span></div>', unsafe_allow_html=True)


# def render_hero(document: dict | None, show_upload):
#     with st.container(key="dashboard_hero"):
#         heading, source = st.columns([1.65, 1], gap="large")
#         with heading:
#             st.markdown("# Claims meet evidence.")
#             st.write("Check financial statements against the source.")
#             st.caption("Text · Tables · Charts · OCR · Numerical verification")
#         with source:
#             if document:
#                 details = (f"{document.get('page_count', 0):,} pages · "
#                            f"{document.get('table_count', 0):,} tables · "
#                            f"{document.get('chart_count', 0):,} charts detected")
#                 st.markdown('<div class="cv-file"><strong>' + escape(document["filename"]) + '</strong><small>'
#                             + escape(details) + '</small></div>', unsafe_allow_html=True)
#             else:
#                 st.markdown('<div class="cv-file"><strong>Start with a financial PDF</strong>'
#                             '<small>Upload and process a report to verify claims.</small></div>', unsafe_allow_html=True)
#             st.button("Manage / upload PDFs" if document else "Upload PDF", on_click=show_upload,
#                       key="hero_upload", use_container_width=True)


# def render_overall(results: list[dict]):
#     if len(results) > 1:
#         label = aggregate_verdict(results)
#         st.markdown(f"### Overall: {label.replace('_', ' ')}")
#         st.caption(f"{len(results)} atomic claims verified independently.")


# def render_result(result: dict, index: int, multiple: bool):
#     verdict = result.get("verdict") or "UNKNOWN"
#     color = COLORS.get(verdict, COLORS["UNKNOWN"])
#     comparison = result.get("comparison") or {}
#     calculation = result.get("calculation") or {}
#     unit = comparison.get("unit") or calculation.get("result_unit")
#     precision = calculation.get("result_precision")
#     values = ""
#     if comparison.get("claimed") is not None and comparison.get("actual") is not None:
#         claimed = _format(comparison["claimed"], unit, precision)
#         actual = _format(comparison["actual"], unit, precision)
#         values = ('<div class="cv-comparison"><div><small>Claimed</small><strong>' + escape(claimed)
#                   + '</strong></div><div><small>Actual</small><strong class="cv-actual">' + escape(actual) + '</strong></div></div>')
#     st.markdown('<div class="cv-verdict"><div class="cv-eyebrow">'
#                 + (f"Claim {index + 1}" if multiple else "Verification result")
#                 + f'</div><h2><span class="cv-indicator" style="background:{color}"></span>'
#                 + escape(verdict.replace("_", " ")) + '</h2>' + values + '</div>', unsafe_allow_html=True)
#     st.write(result.get("claim_text") or "")
#     st.write(result.get("reason") or "No explanation was returned.")
#     confidence = result.get("confidence")
#     if confidence is not None:
#         st.caption(f"Model confidence: {confidence:.0%} · Uncalibrated; this is not measured accuracy.")
#     else:
#         st.caption("Confidence unavailable.")
#     metadata = [result.get("company_name"), result.get("reporting_period")]
#     st.caption(" · ".join(str(v) for v in metadata if v))
#     with st.container(key=f"calculation_{index}"):
#         render_calculation(result)
#     notes = result.get("notes") or []
#     if notes:
#         with st.expander("Processing notes", expanded=False):
#             for note in notes:
#                 st.write(note)
