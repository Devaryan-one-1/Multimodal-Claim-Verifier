from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from services.evaluation_retrieval_evaluation import (
    DEFAULT_DATASET_DIR,
    METRIC_NAMES,
    MRR_DEPTH,
    REPORT_SCHEMA_VERSION,
    evaluate_retrieval,
)


def render_retrieval_evaluation() -> None:
    st.divider()
    st.subheader("Retrieval evaluation")
    st.caption(
        "Initial hybrid retrieval and reranked results · Complete claims and "
        "individual subclaims of compound claims · Reviewed source citations"
    )
    with st.expander("Metric definitions", expanded=False):
        st.write("Recall@k = distinct correct evidence items in the first k results ÷ all correct evidence items.")
        st.write("Hit@k = 1 when at least one correct evidence item appears in the first k results; otherwise 0.")
        st.write("MRR@20 = mean reciprocal rank of the first correct item within the top 20. No correct item in that window gives zero.")
        st.caption(
            "Ranks are chunk positions. Repeated chunks from one source count once in recall. "
            "Matching requires the document and original evidence ID, not just its page. "
            "Queries without positive gold are excluded. Unindexed gold remains in recall's denominator."
        )

    required = {name: DEFAULT_DATASET_DIR / name for name in
                ("claims.jsonl", "ground_truth.jsonl", "manifest.json")}
    if not all(path.exists() for path in required.values()):
        st.info("Approve and export the evaluation dataset first. Its reports must also have chunks and a vector index.")
        return
    try:
        manifest = json.loads(required["manifest.json"].read_text(encoding="utf-8"))
        st.caption(
            f"Dataset: {manifest['dataset_id']} · Claims: {manifest['claim_count']} · "
            f"Documents: {', '.join(manifest['document_ids'])}"
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"Could not read the dataset manifest: {exc}")
        return

    # Ignore an older widget value of 10; this metric requires a top-20 window.
    depth_key = "retrieval_eval_candidate_depth_v2"
    candidate_depth = int(st.number_input(
        "Candidates to retrieve and rerank", min_value=MRR_DEPTH, max_value=100,
        value=20, step=10, key=depth_key,
    ))
    use_claim_parser = st.checkbox(
        "Use claim parser for reranker metadata boosts", value=False,
        key="retrieval_eval_parser",
    )
    resume = st.checkbox(
        "Reuse saved successful stages / resume", value=True,
        key="retrieval_eval_resume_v1",
        help="Reuse matching retrieval, parsing and reranking checkpoints. Failed stages are retried. Uncheck to recompute rankings; valid query embeddings are still cached.",
    )
    stop_on_error = st.checkbox(
        "Stop on the first query error", value=True,
        key="retrieval_eval_stop_on_error",
    )
    st.caption(
        "MRR's cutoff stays at 20 even when more candidates are retrieved. "
        "Increasing the candidate pool can still change the reranked top 20."
    )
    st.info(
        "Only missing query embeddings and missing claim-parser results need API quota. "
        "Successful stages are saved immediately. Rerunning with the same settings can resume after a failure. "
        "Opening this panel makes no model requests."
    )
    if st.button("Run / resume retrieval evaluation", type="primary", key="run_retrieval_evaluation"):
        with st.status("Evaluating retrieval", expanded=True) as status:
            try:
                report = evaluate_retrieval(
                    candidate_depth=candidate_depth, use_claim_parser=use_claim_parser,
                    stop_on_error=stop_on_error, resume=resume, progress=status.write,
                )
                st.session_state.retrieval_report_path = report["report_path"]
                status.update(
                    label="Retrieval evaluation complete" if report["complete"] else "Partial evaluation saved",
                    state="complete" if report["complete"] else "error",
                )
            except Exception as exc:
                status.update(label="Evaluation could not run", state="error")
                st.error(str(exc))

    result_dir = DEFAULT_DATASET_DIR / "retrieval_results"
    paths = sorted(result_dir.glob("*.json"), reverse=True) if result_dir.exists() else []
    if paths:
        selected = st.selectbox("Saved retrieval reports", options=paths,
                                format_func=lambda path: path.name, key="retrieval_saved_report_v2")
        if st.button("Open saved report", key="retrieval_open_report_v2"):
            st.session_state.retrieval_report_path = str(selected)

    saved_path = st.session_state.get("retrieval_report_path")
    if not saved_path:
        return
    path = Path(saved_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.error(f"Could not read the saved report: {exc}")
        return

    st.download_button("Download retrieval evaluation report", data=path.read_bytes(),
                       file_name=path.name, mime="application/json",
                       key=f"retrieval_download_{report.get('report_id', path.stem)}")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        st.info("This report uses the earlier metric format. Run a new evaluation after quota is available to obtain Hit@5, Hit@10 and explicitly labeled MRR@20.")
        return
    checksums = report.get("dataset_checksums") or {}
    if (report.get("dataset_id") != manifest["dataset_id"]
            or report.get("dataset_revision") != manifest.get("revision")
            or checksums.get("claims_sha256") != manifest.get("claims_sha256")
            or checksums.get("ground_truth_sha256") != manifest.get("ground_truth_sha256")):
        st.warning("This is a historical report for a different dataset export. It remains downloadable; run a new evaluation for the current export.")
        return

    if not report.get("complete"):
        st.warning("Incomplete evaluation: metrics cover completed queries only. Failed, blocked and unattempted queries are counted separately, with no invented zero scores.")
    if report.get("abort_reason"):
        st.error(report["abort_reason"])
    checkpointing = report.get("checkpointing")
    if checkpointing:
        with st.expander("Saved stages and resume details", expanded=False):
            st.json(checkpointing)
            st.caption("Reused rankings are rescored against the current reviewed gold evidence. Query-vector caches and ranking checkpoints are separate. Older reports are not automatically imported as checkpoints.")
    st.subheader("Summary")
    st.caption("Macro means over completed eligible queries, separately by stage and level. Mean Hit@k is the share of queries with any correct evidence in the top k.")
    st.dataframe(report["summary"], hide_index=True, use_container_width=True)
    with st.expander("Compare stages on the same completed queries", expanded=False):
        st.caption("Includes only queries where both initial retrieval and reranking completed. Use this table for a paired stage comparison when the run is partial.")
        st.dataframe(report["paired_summary"], hide_index=True, use_container_width=True)
    with st.expander("Recorded retrieval configuration", expanded=False):
        st.json(report["configuration"])

    rows = []
    for query in report["queries"]:
        for stage, result in query["stages"].items():
            metrics = result.get("metrics") or {}
            rows.append({
                "Query": query["query_id"], "Level": query["level"], "Stage": stage,
                "Status": result["status"], "Claim": query["query_text"],
                "Result source": result.get("result_source"),
                "Gold items": len(query["gold_evidence_ids"]),
                "Indexed gold items": query.get("indexed_gold_count"),
                **{metric: metrics.get(metric) for metric in METRIC_NAMES},
                "First correct rank within 20": metrics.get("first_correct_rank"),
                "Unresolved retrieved sources": metrics.get("unresolved_source_id_count"),
                "Unindexed gold IDs": ", ".join(query.get("unindexed_gold_evidence_ids", [])),
                "Error": result.get("error"),
            })
    st.subheader("Per-query results")
    st.dataframe(rows, hide_index=True, use_container_width=True)
    query_map = {query["query_id"]: query for query in report["queries"]}
    if query_map:
        selected_query = st.selectbox("Inspect a query and its rankings", options=list(query_map),
                                      key=f"retrieval_inspect_{report['report_id']}")
        query = query_map[selected_query]
        st.write(query["query_text"])
        st.caption("Gold source IDs: " + ", ".join(query["gold_evidence_ids"]))
        for stage in ("initial", "reranked"):
            with st.expander(f"{stage.title()} ranking", expanded=False):
                result = query["stages"][stage]
                gold = set(query["gold_evidence_ids"])
                ranked_rows = [dict(hit, correct=(hit["source_identity_resolved"]
                    and hit["document_id"] == query["document_id"]
                    and hit["source_evidence_id"] in gold)) for hit in result["ranked_hits"]]
                if ranked_rows:
                    st.dataframe(ranked_rows, hide_index=True, use_container_width=True)
                else:
                    st.caption(f"No ranking available. Status: {result['status']}")
                if result.get("error"):
                    st.error(result["error"])
        with st.expander("Query JSON", expanded=False):
            st.json(query)
    st.caption(f"Saved report: {path}")

# from __future__ import annotations

# import json
# from pathlib import Path

# import streamlit as st

# from evaluation.retrieval_evaluation import (
#     DEFAULT_DATASET_DIR,
#     METRIC_NAMES,
#     MRR_DEPTH,
#     REPORT_SCHEMA_VERSION,
#     evaluate_retrieval,
# )


# def render_retrieval_evaluation() -> None:
#     st.divider()
#     st.subheader("Retrieval evaluation")
#     st.caption(
#         "Initial hybrid retrieval and reranked results · Complete claims and "
#         "individual subclaims of compound claims · Reviewed source citations"
#     )
#     with st.expander("Metric definitions", expanded=False):
#         st.write("Recall@k = distinct correct evidence items in the first k results ÷ all correct evidence items.")
#         st.write("Hit@k = 1 when at least one correct evidence item appears in the first k results; otherwise 0.")
#         st.write("MRR@20 = mean reciprocal rank of the first correct item within the top 20. No correct item in that window gives zero.")
#         st.caption(
#             "Ranks are chunk positions. Repeated chunks from one source count once in recall. "
#             "Matching requires the document and original evidence ID, not just its page. "
#             "Queries without positive gold are excluded. Unindexed gold remains in recall's denominator."
#         )

#     required = {name: DEFAULT_DATASET_DIR / name for name in
#                 ("claims.jsonl", "ground_truth.jsonl", "manifest.json")}
#     if not all(path.exists() for path in required.values()):
#         st.info("Approve and export the evaluation dataset first. Its reports must also have chunks and a vector index.")
#         return
#     try:
#         manifest = json.loads(required["manifest.json"].read_text(encoding="utf-8"))
#         st.caption(
#             f"Dataset: {manifest['dataset_id']} · Claims: {manifest['claim_count']} · "
#             f"Documents: {', '.join(manifest['document_ids'])}"
#         )
#     except (OSError, ValueError, KeyError, TypeError) as exc:
#         st.error(f"Could not read the dataset manifest: {exc}")
#         return

#     # Ignore an older widget value of 10; this metric requires a top-20 window.
#     depth_key = "retrieval_eval_candidate_depth_v2"
#     candidate_depth = int(st.number_input(
#         "Candidates to retrieve and rerank", min_value=MRR_DEPTH, max_value=100,
#         value=20, step=10, key=depth_key,
#     ))
#     use_claim_parser = st.checkbox(
#         "Use claim parser for reranker metadata boosts", value=True,
#         key="retrieval_eval_parser",
#     )
#     stop_on_error = st.checkbox(
#         "Stop on the first query error", value=True,
#         key="retrieval_eval_stop_on_error",
#     )
#     st.caption(
#         "MRR's cutoff stays at 20 even when more candidates are retrieved. "
#         "Increasing the candidate pool can still change the reranked top 20."
#     )
#     st.info(
#         "Running evaluation uses API quota for query embeddings and, when enabled, "
#         "claim parsing. Run it after quota is available. No requests are made by merely opening this panel."
#     )
#     if st.button("Run retrieval evaluation", type="primary", key="run_retrieval_evaluation"):
#         with st.status("Evaluating retrieval", expanded=True) as status:
#             try:
#                 report = evaluate_retrieval(
#                     candidate_depth=candidate_depth, use_claim_parser=use_claim_parser,
#                     stop_on_error=stop_on_error, progress=status.write,
#                 )
#                 st.session_state.retrieval_report_path = report["report_path"]
#                 status.update(
#                     label="Retrieval evaluation complete" if report["complete"] else "Partial evaluation saved",
#                     state="complete" if report["complete"] else "error",
#                 )
#             except Exception as exc:
#                 status.update(label="Evaluation could not run", state="error")
#                 st.error(str(exc))

#     result_dir = DEFAULT_DATASET_DIR / "retrieval_results"
#     paths = sorted(result_dir.glob("*.json"), reverse=True) if result_dir.exists() else []
#     if paths:
#         selected = st.selectbox("Saved retrieval reports", options=paths,
#                                 format_func=lambda path: path.name, key="retrieval_saved_report_v2")
#         if st.button("Open saved report", key="retrieval_open_report_v2"):
#             st.session_state.retrieval_report_path = str(selected)

#     saved_path = st.session_state.get("retrieval_report_path")
#     if not saved_path:
#         return
#     path = Path(saved_path)
#     try:
#         report = json.loads(path.read_text(encoding="utf-8"))
#     except (OSError, ValueError) as exc:
#         st.error(f"Could not read the saved report: {exc}")
#         return

#     st.download_button("Download retrieval evaluation report", data=path.read_bytes(),
#                        file_name=path.name, mime="application/json",
#                        key=f"retrieval_download_{report.get('report_id', path.stem)}")
#     if report.get("schema_version") != REPORT_SCHEMA_VERSION:
#         st.info("This report uses the earlier metric format. Run a new evaluation after quota is available to obtain Hit@5, Hit@10 and explicitly labeled MRR@20.")
#         return
#     checksums = report.get("dataset_checksums") or {}
#     if (report.get("dataset_id") != manifest["dataset_id"]
#             or report.get("dataset_revision") != manifest.get("revision")
#             or checksums.get("claims_sha256") != manifest.get("claims_sha256")
#             or checksums.get("ground_truth_sha256") != manifest.get("ground_truth_sha256")):
#         st.warning("This is a historical report for a different dataset export. It remains downloadable; run a new evaluation for the current export.")
#         return

#     if not report.get("complete"):
#         st.warning("Incomplete evaluation: metrics cover completed queries only. Failed, blocked and unattempted queries are counted separately, with no invented zero scores.")
#     st.subheader("Summary")
#     st.caption("Macro means over completed eligible queries, separately by stage and level. Mean Hit@k is the share of queries with any correct evidence in the top k.")
#     st.dataframe(report["summary"], hide_index=True, use_container_width=True)
#     with st.expander("Compare stages on the same completed queries", expanded=False):
#         st.caption("Includes only queries where both initial retrieval and reranking completed. Use this table for a paired stage comparison when the run is partial.")
#         st.dataframe(report["paired_summary"], hide_index=True, use_container_width=True)
#     with st.expander("Recorded retrieval configuration", expanded=False):
#         st.json(report["configuration"])

#     rows = []
#     for query in report["queries"]:
#         for stage, result in query["stages"].items():
#             metrics = result.get("metrics") or {}
#             rows.append({
#                 "Query": query["query_id"], "Level": query["level"], "Stage": stage,
#                 "Status": result["status"], "Claim": query["query_text"],
#                 "Gold items": len(query["gold_evidence_ids"]),
#                 "Indexed gold items": query.get("indexed_gold_count"),
#                 **{metric: metrics.get(metric) for metric in METRIC_NAMES},
#                 "First correct rank within 20": metrics.get("first_correct_rank"),
#                 "Unresolved retrieved sources": metrics.get("unresolved_source_id_count"),
#                 "Unindexed gold IDs": ", ".join(query.get("unindexed_gold_evidence_ids", [])),
#                 "Error": result.get("error"),
#             })
#     st.subheader("Per-query results")
#     st.dataframe(rows, hide_index=True, use_container_width=True)
#     query_map = {query["query_id"]: query for query in report["queries"]}
#     if query_map:
#         selected_query = st.selectbox("Inspect a query and its rankings", options=list(query_map),
#                                       key=f"retrieval_inspect_{report['report_id']}")
#         query = query_map[selected_query]
#         st.write(query["query_text"])
#         st.caption("Gold source IDs: " + ", ".join(query["gold_evidence_ids"]))
#         for stage in ("initial", "reranked"):
#             with st.expander(f"{stage.title()} ranking", expanded=False):
#                 result = query["stages"][stage]
#                 gold = set(query["gold_evidence_ids"])
#                 ranked_rows = [dict(hit, correct=(hit["source_identity_resolved"]
#                     and hit["document_id"] == query["document_id"]
#                     and hit["source_evidence_id"] in gold)) for hit in result["ranked_hits"]]
#                 if ranked_rows:
#                     st.dataframe(ranked_rows, hide_index=True, use_container_width=True)
#                 else:
#                     st.caption(f"No ranking available. Status: {result['status']}")
#                 if result.get("error"):
#                     st.error(result["error"])
#         with st.expander("Query JSON", expanded=False):
#             st.json(query)
#     st.caption(f"Saved report: {path}")

# # from __future__ import annotation

# # import json
# # from pathlib import Path

# # import streamlit as st

# # from evaluation.retrieval_evaluation import (
# #     DEFAULT_DATASET_DIR,
# #     evaluate_retrieval,
# # )


# # def render_retrieval_evaluation() -> None:
# #     st.divider()
# #     st.subheader("Retrieval evaluation")

# #     st.caption(
# #         "Compare initial hybrid retrieval with reranked results. "
# #         "Report complete-claim and compound-subclaim metrics "
# #         "against approved source citations."
# #     )

# #     required_files = [
# #         DEFAULT_DATASET_DIR / "claims.jsonl",
# #         DEFAULT_DATASET_DIR / "ground_truth.jsonl",
# #         DEFAULT_DATASET_DIR / "manifest.json",
# #     ]

# #     if not all(path.exists() for path in required_files):
# #         st.info(
# #             "Approve and export the evaluation dataset first. "
# #             "Retrieval also requires chunks and a vector index "
# #             "for its documents."
# #         )
# #         return

# #     try:
# #         manifest = json.loads(
# #             required_files[2].read_text(encoding="utf-8")
# #         )
# #     except (OSError, json.JSONDecodeError) as exc:
# #         st.error(f"Could not read dataset manifest: {exc}")
# #         return

# #     st.caption(
# #         f"Dataset: {manifest['dataset_id']} · "
# #         f"Claims: {manifest['claim_count']} · "
# #         f"Documents: "
# #         f"{', '.join(manifest['document_ids'])}"
# #     )

# #     candidate_depth = int(
# #         st.number_input(
# #             "Candidate / metric depth",
# #             min_value=10,
# #             max_value=100,
# #             value=20,
# #             step=10,
# #             key="retrieval_eval_depth",
# #         )
# #     )

# #     use_claim_parser = st.checkbox(
# #         "Use claim parser for reranker metadata boosts",
# #         value=True,
# #         key="retrieval_eval_parser",
# #     )

# #     stop_on_error = st.checkbox(
# #         "Stop on the first query error",
# #         value=True,
# #         key="retrieval_eval_stop_on_error",
# #     )

# #     st.caption(
# #         f"MRR is measured within the top {candidate_depth}. "
# #         "Recall@5 and Recall@10 use chunk rank positions. "
# #         "Repeated chunks from one gold source receive credit once."
# #     )

# #     if st.button(
# #         "Run retrieval evaluation",
# #         type="primary",
# #         key="run_retrieval_evaluation",
# #     ):
# #         with st.status(
# #             "Evaluating retrieval",
# #             expanded=True,
# #         ) as status:
# #             try:
# #                 report = evaluate_retrieval(
# #                     candidate_depth=candidate_depth,
# #                     use_claim_parser=use_claim_parser,
# #                     stop_on_error=stop_on_error,
# #                     progress=status.write,
# #                 )

# #                 st.session_state.retrieval_report_path = (
# #                     report["report_path"]
# #                 )

# #                 status.update(
# #                     label=(
# #                         "Retrieval evaluation complete"
# #                         if report["complete"]
# #                         else "Partial evaluation saved"
# #                     ),
# #                     state=(
# #                         "complete"
# #                         if report["complete"]
# #                         else "error"
# #                     ),
# #                 )

# #             except Exception as exc:
# #                 status.update(
# #                     label="Evaluation could not run",
# #                     state="error",
# #                 )
# #                 st.error(str(exc))

# #     saved_path = st.session_state.get(
# #         "retrieval_report_path"
# #     )

# #     if not saved_path:
# #         return

# #     path = Path(saved_path)

# #     if not path.exists():
# #         return

# #     try:
# #         report = json.loads(
# #             path.read_text(encoding="utf-8")
# #         )
# #     except (OSError, json.JSONDecodeError) as exc:
# #         st.error(f"Could not read saved report: {exc}")
# #         return

# #     if (
# #         report["dataset_id"] != manifest["dataset_id"]
# #         or report.get("dataset_revision")
# #         != manifest.get("revision")
# #     ):
# #         st.info(
# #             "The displayed dataset has changed. "
# #             "Run a new retrieval evaluation for this export."
# #         )
# #         return

# #     if not report["complete"]:
# #         st.warning(
# #             "This evaluation is incomplete. Metrics below apply "
# #             "only to completed queries. Failed, blocked and "
# #             "unattempted queries are counted separately."
# #         )

# #     st.dataframe(
# #         report["summary"],
# #         hide_index=True,
# #         use_container_width=True,
# #     )

# #     st.subheader("Per-query results")

# #     rows = []

# #     for query in report["queries"]:
# #         for stage, result in query["stages"].items():
# #             metrics = result["metrics"] or {}

# #             rows.append(
# #                 {
# #                     "Query": query["query_id"],
# #                     "Level": query["level"],
# #                     "Stage": stage,
# #                     "Status": result["status"],
# #                     "Claim": query["query_text"],
# #                     "Gold items": len(
# #                         query["gold_evidence_ids"]
# #                     ),
# #                     "Indexed gold items": (
# #                         query["indexed_gold_count"]
# #                     ),
# #                     "Recall@5": metrics.get("Recall@5"),
# #                     "Recall@10": metrics.get("Recall@10"),
# #                     "MRR": metrics.get("MRR"),
# #                     "First correct rank": metrics.get(
# #                         "first_correct_rank"
# #                     ),
# #                     "Unindexed gold IDs": ", ".join(
# #                         query["unindexed_gold_evidence_ids"]
# #                     ),
# #                     "Error": result["error"],
# #                 }
# #             )

# #     st.dataframe(
# #         rows,
# #         hide_index=True,
# #         use_container_width=True,
# #     )

# #     query_map = {
# #         query["query_id"]: query
# #         for query in report["queries"]
# #     }

# #     selected_query = st.selectbox(
# #         "Inspect a query and its rankings",
# #         options=list(query_map),
# #         key=f"retrieval_inspect_{report['report_id']}",
# #     )

# #     st.json(query_map[selected_query])

# #     st.download_button(
# #         "Download retrieval evaluation report",
# #         data=path.read_bytes(),
# #         file_name=path.name,
# #         mime="application/json",
# #         key=f"retrieval_download_{report['report_id']}",
# #     )

# #     st.caption(f"Saved report: {path}")
# # # from __future__ import annotation

# # # import json
# # # from pathlib import Path

# # # import streamlit as st

# # # from evaluation.retrieval_evaluation import (
# # #     DEFAULT_DATASET_DIR,
# # #     evaluate_retrieval,
# # # )


# # # def render_retrieval_evaluation() -> None:
# # #     st.divider()
# # #     st.subheader("Retrieval evaluation")

# # #     st.caption(
# # #         "Compare initial hybrid retrieval with reranked results. "
# # #         "Report complete-claim and compound-subclaim metrics "
# # #         "against approved source citations."
# # #     )

# # #     required_files = [
# # #         DEFAULT_DATASET_DIR / "claims.jsonl",
# # #         DEFAULT_DATASET_DIR / "ground_truth.jsonl",
# # #         DEFAULT_DATASET_DIR / "manifest.json",
# # #     ]

# # #     if not all(path.exists() for path in required_files):
# # #         st.info(
# # #             "Approve and export the evaluation dataset first. "
# # #             "Retrieval also requires chunks and a vector index "
# # #             "for its documents."
# # #         )
# # #         return

# # #     try:
# # #         manifest = json.loads(
# # #             required_files[2].read_text(encoding="utf-8")
# # #         )
# # #     except (OSError, json.JSONDecodeError) as exc:
# # #         st.error(f"Could not read dataset manifest: {exc}")
# # #         return

# # #     st.caption(
# # #         f"Dataset: {manifest['dataset_id']} · "
# # #         f"Claims: {manifest['claim_count']} · "
# # #         f"Documents: "
# # #         f"{', '.join(manifest['document_ids'])}"
# # #     )

# # #     candidate_depth = int(
# # #         st.number_input(
# # #             "Candidate / metric depth",
# # #             min_value=10,
# # #             max_value=100,
# # #             value=20,
# # #             step=10,
# # #             key="retrieval_eval_depth",
# # #         )
# # #     )

# # #     use_claim_parser = st.checkbox(
# # #         "Use claim parser for reranker metadata boosts",
# # #         value=True,
# # #         key="retrieval_eval_parser",
# # #     )

# # #     stop_on_error = st.checkbox(
# # #         "Stop on the first query error",
# # #         value=True,
# # #         key="retrieval_eval_stop_on_error",
# # #     )

# # #     st.caption(
# # #         f"MRR is measured within the top {candidate_depth}. "
# # #         "Recall@5 and Recall@10 use chunk rank positions. "
# # #         "Repeated chunks from one gold source receive credit once."
# # #     )

# # #     if st.button(
# # #         "Run retrieval evaluation",
# # #         type="primary",
# # #         key="run_retrieval_evaluation",
# # #     ):
# # #         with st.status(
# # #             "Evaluating retrieval",
# # #             expanded=True,
# # #         ) as status:
# # #             try:
# # #                 report = evaluate_retrieval(
# # #                     candidate_depth=candidate_depth,
# # #                     use_claim_parser=use_claim_parser,
# # #                     stop_on_error=stop_on_error,
# # #                     progress=status.write,
# # #                 )

# # #                 st.session_state.retrieval_report_path = (
# # #                     report["report_path"]
# # #                 )

# # #                 status.update(
# # #                     label=(
# # #                         "Retrieval evaluation complete"
# # #                         if report["complete"]
# # #                         else "Partial evaluation saved"
# # #                     ),
# # #                     state=(
# # #                         "complete"
# # #                         if report["complete"]
# # #                         else "error"
# # #                     ),
# # #                 )

# # #             except Exception as exc:
# # #                 status.update(
# # #                     label="Evaluation could not run",
# # #                     state="error",
# # #                 )
# # #                 st.error(str(exc))

# # #     saved_path = st.session_state.get(
# # #         "retrieval_report_path"
# # #     )

# # #     if not saved_path:
# # #         return

# # #     path = Path(saved_path)

# # #     if not path.exists():
# # #         return

# # #     try:
# # #         report = json.loads(
# # #             path.read_text(encoding="utf-8")
# # #         )
# # #     except (OSError, json.JSONDecodeError) as exc:
# # #         st.error(f"Could not read saved report: {exc}")
# # #         return

# # #     if (
# # #         report["dataset_id"] != manifest["dataset_id"]
# # #         or report.get("dataset_revision")
# # #         != manifest.get("revision")
# # #     ):
# # #         st.info(
# # #             "The displayed dataset has changed. "
# # #             "Run a new retrieval evaluation for this export."
# # #         )
# # #         return

# # #     if not report["complete"]:
# # #         st.warning(
# # #             "This evaluation is incomplete. Metrics below apply "
# # #             "only to completed queries. Failed, blocked and "
# # #             "unattempted queries are counted separately."
# # #         )

# # #     st.dataframe(
# # #         report["summary"],
# # #         hide_index=True,
# # #         use_container_width=True,
# # #     )

# # #     st.subheader("Per-query results")

# # #     rows = []

# # #     for query in report["queries"]:
# # #         for stage, result in query["stages"].items():
# # #             metrics = result["metrics"] or {}

# # #             rows.append(
# # #                 {
# # #                     "Query": query["query_id"],
# # #                     "Level": query["level"],
# # #                     "Stage": stage,
# # #                     "Status": result["status"],
# # #                     "Claim": query["query_text"],
# # #                     "Gold items": len(
# # #                         query["gold_evidence_ids"]
# # #                     ),
# # #                     "Indexed gold items": (
# # #                         query["indexed_gold_count"]
# # #                     ),
# # #                     "Recall@5": metrics.get("Recall@5"),
# # #                     "Recall@10": metrics.get("Recall@10"),
# # #                     "MRR": metrics.get("MRR"),
# # #                     "First correct rank": metrics.get(
# # #                         "first_correct_rank"
# # #                     ),
# # #                     "Unindexed gold IDs": ", ".join(
# # #                         query["unindexed_gold_evidence_ids"]
# # #                     ),
# # #                     "Error": result["error"],
# # #                 }
# # #             )

# # #     st.dataframe(
# # #         rows,
# # #         hide_index=True,
# # #         use_container_width=True,
# # #     )

# # #     query_map = {
# # #         query["query_id"]: query
# # #         for query in report["queries"]
# # #     }

# # #     selected_query = st.selectbox(
# # #         "Inspect a query and its rankings",
# # #         options=list(query_map),
# # #         key=f"retrieval_inspect_{report['report_id']}",
# # #     )

# # #     st.json(query_map[selected_query])

# # #     st.download_button(
# # #         "Download retrieval evaluation report",
# # #         data=path.read_bytes(),
# # #         file_name=path.name,
# # #         mime="application/json",
# # #         key=f"retrieval_download_{report['report_id']}",
# # #     )

# # #     st.caption(f"Saved report: {path}")