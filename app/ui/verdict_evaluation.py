from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from evaluation.verdict_evaluation import (
    DEFAULT_DATASET_DIR, LABELS, LEVELS, SCHEMA_VERSION, evaluate_verdicts,
)


def _percentage(value):
    return "—" if value is None else f"{value:.1%}"


def _number(value):
    return "—" if value is None else f"{value:.3f}"


def render_verdict_evaluation() -> None:
    st.divider()
    st.subheader("Final-verdict evaluation")
    st.caption("Accuracy, per-verdict precision/recall/F1, macro and weighted averages, and confusion matrices.")
    required = [DEFAULT_DATASET_DIR / name for name in ("claims.jsonl", "ground_truth.jsonl", "manifest.json")]
    if not all(path.is_file() for path in required):
        st.info("Approve and export your evaluation dataset first.")
        return
    try:
        manifest = json.loads(required[-1].read_text(encoding="utf-8"))
        st.caption(f"Dataset: {manifest['dataset_id']} · {manifest['claim_count']} reviewed claims")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"Could not read the dataset manifest: {exc}")
        return

    modes = {
        "Reuse matching cache; verify missing predictions": "reuse",
        "Cache only — no API requests": "cache_only",
        "Fresh predictions — verify everything again": "fresh",
    }
    selected_mode = st.selectbox("Prediction mode", options=list(modes), key="verdict_eval_mode")
    mode = modes[selected_mode]
    stop_on_error = st.checkbox("Stop on the first verification error", value=True, key="verdict_eval_stop")
    st.caption("Shared quota/rate limits always pause the run, even with this checkbox off.")
    limit = st.number_input("Maximum new predictions in this run", min_value=1, max_value=200,
                            value=5, step=1, key="verdict_eval_new_limit")
    st.caption("Start with 5. A prediction can use several model calls. Saved matching predictions do not use this limit.")
    if mode == "cache_only":
        st.info("Only matching successful saved predictions are scored. Cache misses remain unattempted; they do not become UNKNOWN. This mode makes no API requests.")
    else:
        st.caption("This mode uses API quota. Run it after quota is available. Errors are not treated as UNKNOWN verdicts.")
    st.caption("Cache matching checks exact claim text, sources, processed/index metadata, configured models, pipeline code and dependency versions. Model aliases may change upstream; use fresh predictions after an upstream change.")

    if st.button("Score cached verdicts" if mode == "cache_only" else "Run verdict evaluation",
                 type="primary", key="verdict_eval_run"):
        with st.status("Evaluating final verdicts", expanded=True) as status:
            try:
                report = evaluate_verdicts(cache_mode=mode, stop_on_error=stop_on_error,
                                          max_new_predictions=int(limit), progress=status.write)
                st.session_state.verdict_report_path = report["report_path"]
                status.update(label="Verdict evaluation complete" if report["complete"] else "Partial verdict report saved",
                              state="complete" if report["complete"] else "error")
            except Exception as exc:
                status.update(label="Verdict evaluation could not run", state="error")
                st.error(str(exc))

    folder = DEFAULT_DATASET_DIR / "verdict_results"
    saved = sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []
    if saved:
        selected = st.selectbox("Saved verdict reports", options=saved, format_func=lambda path: path.name,
                                key="verdict_eval_saved")
        if st.button("Open saved verdict report", key="verdict_eval_open"):
            st.session_state.verdict_report_path = str(selected)
    selected_path = st.session_state.get("verdict_report_path")
    if not selected_path:
        return
    path = Path(selected_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("Expected a report object.")
    except (OSError, ValueError) as exc:
        st.error(f"Could not read the saved verdict report: {exc}")
        return
    st.download_button("Download verdict evaluation report", data=path.read_bytes(), file_name=path.name,
                       mime="application/json", key=f"verdict_eval_download_{path.stem}")
    if report.get("schema_version") != SCHEMA_VERSION:
        st.warning("This report uses a different verdict evaluation format.")
        return
    checksums = report.get("dataset_checksums") or {}
    if (report.get("dataset_id") != manifest["dataset_id"]
            or report.get("dataset_revision") != manifest.get("revision")
            or any(checksums.get(name) != manifest.get(name) for name in ("claims_sha256", "ground_truth_sha256"))):
        st.warning("This report belongs to a different dataset export. It remains downloadable; evaluate the current export separately.")
        return
    if not report.get("valid_inputs", True):
        st.error("Inputs changed or a storage error interrupted the run. Do not interpret its scores.")
        st.json(report.get("pause"))
        return
    if report.get("paused"):
        pause = report.get("pause") or {}
        st.warning(f"Run paused: {pause.get('reason', 'See details below')}")
        st.caption(pause.get("action", "Resume explicitly after resolving the reported problem."))
    if not report["complete"]:
        st.warning("Incomplete evaluation: metrics apply only to completed predictions. Review coverage, errors and unattempted queries before interpreting the scores.")
    st.dataframe(report["summary"], hide_index=True, use_container_width=True)

    claim_tab, subclaim_tab = st.tabs(["Complete claims", "Compound subclaims"])
    for tab, level in zip((claim_tab, subclaim_tab), LEVELS):
        with tab:
            metrics = report["metrics"][level]
            summary = metrics["summary"]
            accuracy, macro, weighted, coverage = st.columns(4)
            accuracy.metric("Accuracy", _percentage(summary["accuracy"]))
            macro.metric("Macro F1", _number(summary["macro_f1"]))
            weighted.metric("Weighted F1", _number(summary["weighted_f1"]))
            coverage.metric("Prediction coverage", _percentage(summary["coverage"]))
            st.caption(f"Completed: {summary['completed_queries']}/{summary['total_queries']} · Failed: {summary['failed_queries']} · Unattempted: {summary['not_run_queries']} · Cache hits: {summary['cache_hits']}")
            if level == "subclaim":
                st.caption("These are independent runs on reviewed atomic texts from compound claims, rather than parser-output alignment. Shared qualifiers should be retained in reviewed subclaim text.")
            st.markdown("**Per-verdict metrics**")
            st.dataframe(metrics["per_class"], hide_index=True, use_container_width=True,
                         column_config={name: st.column_config.NumberColumn(name.title(), format="%.3f")
                                        for name in ("precision", "recall", "f1")})
            st.caption("Scores use 0–1 values. A class absent from both gold and predictions has no measured score. Macro averages include active classes equally; weighted averages use ground-truth support. Undefined denominators for active classes score zero.")
            averages = [{"Average": name.title(), **{metric: summary[f"{name}_{metric}"] for metric in ("precision", "recall", "f1")}}
                        for name in ("macro", "weighted")]
            st.dataframe(averages, hide_index=True, use_container_width=True)
            st.markdown("**Confusion matrix**")
            st.caption("Rows: reviewed actual verdict. Columns: predicted verdict. Counts include completed predictions only.")
            matrix = metrics["confusion_matrix"]
            st.dataframe([{"Actual": label, **matrix[label]} for label in LABELS], hide_index=True, use_container_width=True)

    rows = [{"Query": q["query_id"], "Level": q["level"], "Claim": q["claim_text"],
             "Expected": q["expected"], "Predicted": q["predicted"], "Correct": q["correct"],
             "Status": q["status"], "Prediction source": q["prediction_source"], "Error": q["error"]}
            for q in report["queries"]]
    st.subheader("Per-query results")
    issues_only = st.checkbox("Show incorrect or incomplete queries only", value=False, key="verdict_eval_issues")
    st.dataframe([row for row in rows if not issues_only or row["Correct"] is not True],
                 hide_index=True, use_container_width=True)
    query_map = {q["query_id"]: q for q in report["queries"]}
    if query_map:
        selected_query = st.selectbox("Inspect a verdict prediction", options=list(query_map),
                                      key=f"verdict_eval_inspect_{report['report_id']}")
        st.json(query_map[selected_query])
    with st.expander("Recorded configuration and metric policy", expanded=False):
        st.json({"configuration": report["configuration"], "metric_policy": report["metric_policy"]})
    st.caption(f"Saved report: {path}")

# from __future__ import annotations

# import json
# from pathlib import Path

# import streamlit as st

# from evaluation.verdict_evaluation import (
#     DEFAULT_DATASET_DIR,
#     LABELS,
#     LEVELS,
#     SCHEMA_VERSION,
#     evaluate_verdicts,
# )


# def _percentage(value):
#     return "—" if value is None else f"{value:.1%}"


# def _number(value):
#     return "—" if value is None else f"{value:.3f}"


# def render_verdict_evaluation() -> None:
#     st.divider()
#     st.subheader("Final-verdict evaluation")

#     st.caption(
#         "Accuracy, per-verdict precision/recall/F1, "
#         "macro and weighted averages, and confusion matrices."
#     )

#     required = [
#         DEFAULT_DATASET_DIR / name
#         for name in (
#             "claims.jsonl",
#             "ground_truth.jsonl",
#             "manifest.json",
#         )
#     ]

#     if not all(path.is_file() for path in required):
#         st.info(
#             "Approve and export your evaluation dataset first."
#         )
#         return

#     try:
#         manifest = json.loads(
#             required[-1].read_text(encoding="utf-8")
#         )

#         st.caption(
#             f"Dataset: {manifest['dataset_id']} · "
#             f"{manifest['claim_count']} reviewed claims"
#         )

#     except (OSError, ValueError, KeyError, TypeError) as exc:
#         st.error(
#             f"Could not read the dataset manifest: {exc}"
#         )
#         return

#     modes = {
#         "Cache only — no API requests": "cache_only",
#         "Reuse matching cache; verify missing predictions": "reuse",
#         "Fresh predictions — verify everything again": "fresh",
#     }

#     selected_mode = st.selectbox(
#         "Prediction mode",
#         options=list(modes),
#         key="verdict_eval_mode",
#     )

#     mode = modes[selected_mode]

#     stop_on_error = st.checkbox(
#         "Stop on the first verification error",
#         value=True,
#         key="verdict_eval_stop",
#     )

#     if mode == "cache_only":
#         st.info(
#             "Only matching successful saved predictions "
#             "are scored. Cache misses remain unattempted; "
#             "they do not become UNKNOWN. "
#             "This mode makes no API requests."
#         )
#     else:
#         st.caption(
#             "This mode uses API quota. Run it after quota "
#             "is available. Errors are not treated as "
#             "UNKNOWN verdicts."
#         )

#     st.caption(
#         "Cache matching checks exact claim text, sources, "
#         "processed/index metadata, configured models, "
#         "pipeline code and dependency versions. Model "
#         "aliases may change upstream; use fresh predictions "
#         "after an upstream change."
#     )

#     button_label = (
#         "Score cached verdicts"
#         if mode == "cache_only"
#         else "Run verdict evaluation"
#     )

#     if st.button(
#         button_label,
#         type="primary",
#         key="verdict_eval_run",
#     ):
#         with st.status(
#             "Evaluating final verdicts",
#             expanded=True,
#         ) as status:
#             try:
#                 report = evaluate_verdicts(
#                     cache_mode=mode,
#                     stop_on_error=stop_on_error,
#                     progress=status.write,
#                 )

#                 st.session_state.verdict_report_path = (
#                     report["report_path"]
#                 )

#                 status.update(
#                     label=(
#                         "Verdict evaluation complete"
#                         if report["complete"]
#                         else "Partial verdict report saved"
#                     ),
#                     state=(
#                         "complete"
#                         if report["complete"]
#                         else "error"
#                     ),
#                 )

#             except Exception as exc:
#                 status.update(
#                     label="Verdict evaluation could not run",
#                     state="error",
#                 )
#                 st.error(str(exc))

#     folder = DEFAULT_DATASET_DIR / "verdict_results"

#     saved = (
#         sorted(folder.glob("*.json"), reverse=True)
#         if folder.exists()
#         else []
#     )

#     if saved:
#         selected = st.selectbox(
#             "Saved verdict reports",
#             options=saved,
#             format_func=lambda path: path.name,
#             key="verdict_eval_saved",
#         )

#         if st.button(
#             "Open saved verdict report",
#             key="verdict_eval_open",
#         ):
#             st.session_state.verdict_report_path = (
#                 str(selected)
#             )

#     selected_path = st.session_state.get(
#         "verdict_report_path"
#     )

#     if not selected_path:
#         return

#     path = Path(selected_path)

#     try:
#         report = json.loads(
#             path.read_text(encoding="utf-8")
#         )

#         if not isinstance(report, dict):
#             raise ValueError("Expected a report object.")

#     except (OSError, ValueError) as exc:
#         st.error(
#             f"Could not read the saved verdict report: {exc}"
#         )
#         return

#     st.download_button(
#         "Download verdict evaluation report",
#         data=path.read_bytes(),
#         file_name=path.name,
#         mime="application/json",
#         key=f"verdict_eval_download_{path.stem}",
#     )

#     if report.get("schema_version") != SCHEMA_VERSION:
#         st.warning(
#             "This report uses a different verdict "
#             "evaluation format."
#         )
#         return

#     checksums = report.get("dataset_checksums") or {}

#     if (
#         report.get("dataset_id") != manifest["dataset_id"]
#         or report.get("dataset_revision")
#         != manifest.get("revision")
#         or any(
#             checksums.get(name) != manifest.get(name)
#             for name in (
#                 "claims_sha256",
#                 "ground_truth_sha256",
#             )
#         )
#     ):
#         st.warning(
#             "This report belongs to a different dataset "
#             "export. It remains downloadable; evaluate "
#             "the current export separately."
#         )
#         return

#     if not report["complete"]:
#         st.warning(
#             "Incomplete evaluation: metrics apply only "
#             "to completed predictions. Review coverage, "
#             "errors and unattempted queries before "
#             "interpreting the scores."
#         )

#     st.dataframe(
#         report["summary"],
#         hide_index=True,
#         use_container_width=True,
#     )

#     claim_tab, subclaim_tab = st.tabs(
#         [
#             "Complete claims",
#             "Compound subclaims",
#         ]
#     )

#     for tab, level in zip(
#         (claim_tab, subclaim_tab),
#         LEVELS,
#     ):
#         with tab:
#             metrics = report["metrics"][level]
#             summary = metrics["summary"]

#             accuracy, macro, weighted, coverage = st.columns(4)

#             accuracy.metric(
#                 "Accuracy",
#                 _percentage(summary["accuracy"]),
#             )

#             macro.metric(
#                 "Macro F1",
#                 _number(summary["macro_f1"]),
#             )

#             weighted.metric(
#                 "Weighted F1",
#                 _number(summary["weighted_f1"]),
#             )

#             coverage.metric(
#                 "Prediction coverage",
#                 _percentage(summary["coverage"]),
#             )

#             st.caption(
#                 f"Completed: {summary['completed_queries']}/"
#                 f"{summary['total_queries']} · "
#                 f"Failed: {summary['failed_queries']} · "
#                 f"Unattempted: {summary['not_run_queries']} · "
#                 f"Cache hits: {summary['cache_hits']}"
#             )

#             if level == "subclaim":
#                 st.caption(
#                     "These are independent runs on reviewed "
#                     "atomic texts from compound claims, rather "
#                     "than parser-output alignment. Shared "
#                     "qualifiers should be retained in reviewed "
#                     "subclaim text."
#                 )

#             st.markdown("**Per-verdict metrics**")

#             st.dataframe(
#                 metrics["per_class"],
#                 hide_index=True,
#                 use_container_width=True,
#                 column_config={
#                     name: st.column_config.NumberColumn(
#                         name.title(),
#                         format="%.3f",
#                     )
#                     for name in (
#                         "precision",
#                         "recall",
#                         "f1",
#                     )
#                 },
#             )

#             st.caption(
#                 "Scores use 0–1 values. A class absent "
#                 "from both gold and predictions has no "
#                 "measured score. Macro averages include "
#                 "active classes equally; weighted averages "
#                 "use ground-truth support. Undefined "
#                 "denominators for active classes score zero."
#             )

#             averages = [
#                 {
#                     "Average": name.title(),
#                     **{
#                         metric: summary[f"{name}_{metric}"]
#                         for metric in (
#                             "precision",
#                             "recall",
#                             "f1",
#                         )
#                     },
#                 }
#                 for name in ("macro", "weighted")
#             ]

#             st.dataframe(
#                 averages,
#                 hide_index=True,
#                 use_container_width=True,
#             )

#             st.markdown("**Confusion matrix**")

#             st.caption(
#                 "Rows: reviewed actual verdict. "
#                 "Columns: predicted verdict. "
#                 "Counts include completed predictions only."
#             )

#             matrix = metrics["confusion_matrix"]

#             st.dataframe(
#                 [
#                     {
#                         "Actual": label,
#                         **matrix[label],
#                     }
#                     for label in LABELS
#                 ],
#                 hide_index=True,
#                 use_container_width=True,
#             )

#     rows = [
#         {
#             "Query": query["query_id"],
#             "Level": query["level"],
#             "Claim": query["claim_text"],
#             "Expected": query["expected"],
#             "Predicted": query["predicted"],
#             "Correct": query["correct"],
#             "Status": query["status"],
#             "Prediction source": query["prediction_source"],
#             "Error": query["error"],
#         }
#         for query in report["queries"]
#     ]

#     st.subheader("Per-query results")

#     issues_only = st.checkbox(
#         "Show incorrect or incomplete queries only",
#         value=False,
#         key="verdict_eval_issues",
#     )

#     st.dataframe(
#         [
#             row
#             for row in rows
#             if not issues_only
#             or row["Correct"] is not True
#         ],
#         hide_index=True,
#         use_container_width=True,
#     )

#     query_map = {
#         query["query_id"]: query
#         for query in report["queries"]
#     }

#     if query_map:
#         selected_query = st.selectbox(
#             "Inspect a verdict prediction",
#             options=list(query_map),
#             key=(
#                 f"verdict_eval_inspect_{report['report_id']}"
#             ),
#         )

#         st.json(query_map[selected_query])

#     with st.expander(
#         "Recorded configuration and metric policy",
#         expanded=False,
#     ):
#         st.json(
#             {
#                 "configuration": report["configuration"],
#                 "metric_policy": report["metric_policy"],
#             }
#         )

#     st.caption(f"Saved report: {path}")