from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from services.evaluation_hallucination import (
    ROOT, add_drafts, counts, create_run, export_reviewed, grounding_report,
    list_runs, load_run, local_grounding_checks, run_tests,
    save_grounding_review, update_record,
)


def _flash(message):
    st.session_state["hallucination_flash"] = message
    st.rerun()


def _review(run):
    if not run["records"]:
        st.info("Build drafts first. AI labels remain provisional until you inspect the source.")
        return
    mapping = {r["id"]: r for r in run["records"]}
    key = st.selectbox(
        "Review a hallucination-test draft", list(mapping),
        format_func=lambda k: f"{k} · {mapping[k]['status']} · {mapping[k]['candidate']['category']}",
        key=f"h_review_select_{run['dataset_id']}",
    )
    record = mapping[key]
    candidate = record["candidate"]
    st.caption(candidate["reviewer_instructions"])
    doc = candidate["document_id"]
    path = Path("data/documents") / doc / "original.pdf"
    if path.is_file():
        st.download_button("Download source PDF for review", path.read_bytes(),
                           file_name=f"{doc}.pdf", mime="application/pdf",
                           key=f"h_pdf_{run['dataset_id']}_{key}")
    with st.expander("Draft source quotes and page references"):
        source = json.loads((Path("data/processed") / doc / "evidence.json").read_text(encoding="utf-8"))
        by_id = {str(e["id"]): e for e in source["evidence"]}
        for atom in candidate["subclaims"]:
            st.write(atom["claim_text"])
            for quote in atom["citations"]:
                raw_id = quote["evidence_id"].removeprefix(doc + "::")
                item = by_id.get(raw_id) or by_id.get(quote["evidence_id"])
                st.caption(f"{quote['evidence_id']} · PDF page {item.get('page') if item else 'unresolved'}")
                st.text(quote["quote"])
    prefix = f"{run['dataset_id']}_{key}_{record['version']}"
    with st.form(f"h_review_{prefix}"):
        edited = st.text_area("Editable test candidate", json.dumps(candidate, indent=2, ensure_ascii=False),
                              height=430)
        reviewer = st.text_input("Reviewer", record.get("reviewed_by") or "")
        notes = st.text_area("Review notes", record.get("review_notes") or "", height=90)
        source_checked = st.checkbox("I checked the cited source pages, numbers, units and periods.",
                                     value=record.get("source_checked", False))
        absence_checked = st.checkbox(
            "For every UNKNOWN atom, I reviewed the complete document, including forecasts/footnotes, for missing evidence.",
            value=record.get("unknown_scope_checked", False),
        )
        save = st.form_submit_button("Save as draft")
        approve = st.form_submit_button("Approve", type="primary")
        reject = st.form_submit_button("Reject")
    if save or approve or reject:
        try:
            update_record(
                run["dataset_id"], key, json.loads(edited), expected_version=record["version"],
                status="approved" if approve else "rejected" if reject else "draft",
                reviewer=reviewer, notes=notes, source_checked=source_checked,
                unknown_scope_checked=absence_checked,
            )
            _flash(f"{key}: {'approved' if approve else 'rejected' if reject else 'saved as draft'}.")
        except Exception as exc:
            st.error(str(exc))


def _drafts(selected_document_ids):
    documents = {}
    for path in sorted(Path("data/processed").glob("*/document.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("status") == "ready":
                documents[path.parent.name] = d.get("company_name") or d.get("filename") or path.parent.name
        except (OSError, ValueError):
            continue
    ids = st.multiselect("Sources for the separate hallucination set", list(documents),
                         default=[d for d in selected_document_ids or [] if d in documents],
                         format_func=lambda d: documents[d], key="h_source_documents")
    if st.button("Create separate 20-claim test set", disabled=not ids, key="h_create"):
        try:
            run = create_run(ids)
            st.session_state["h_selected_run"] = run["dataset_id"]
            _flash("Separate test set created. Your approved main dataset is unchanged.")
        except Exception as exc:
            st.error(str(exc))
    runs = list_runs()
    if not runs:
        return
    if st.session_state.get("h_selected_run") not in runs:
        st.session_state["h_selected_run"] = runs[0]
    chosen = st.selectbox("Saved hallucination draft sets", runs, key="h_selected_run")
    run = load_run(chosen)
    st.caption("Target: four cases each for missing forecasts, invented metrics, invented segments, wrong period/scope, and mixed compounds.")
    st.dataframe(counts(run), hide_index=True, use_container_width=True)
    batches = st.number_input("Maximum new draft requests per click", min_value=1, max_value=5,
                              value=1, key="h_draft_budget")
    st.caption("Each request drafts at most ONE compact case. Start with 1 request per click; use up to 5 after it works. The target remains 20 claims.")
    if st.button("Build / resume test drafts", key="h_build"):
        try:
            before = len(run["records"])
            with st.status("Drafting hallucination cases", expanded=True) as status:
                run = add_drafts(chosen, max_batches=int(batches), progress=status.write)
                status.update(label="Draft progress saved", state="error" if run.get("pause") else "complete")
            added = len(run["records"]) - before
            if added or not run.get("pause"):
                st.session_state["hallucination_flash"] = f"Saved {added} new draft(s). Review them below."
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if run.get("pause"):
        st.warning((run["pause"] or {}).get("reason", "Generation paused."))
        if (run["pause"] or {}).get("action"):
            st.caption(run["pause"]["action"])
    if run.get("generation_errors"):
        with st.expander("Draft generation details"):
            st.json(run["generation_errors"][-10:])
    _review(run)
    st.caption("Export requires exactly 20 approved claims, four per category, including counterevidence and compound cases.")
    if st.button("Export 20 reviewed hallucination claims", key="h_export"):
        try:
            export_reviewed(chosen)
            _flash("Exported the separate hallucination dataset.")
        except Exception as exc:
            st.error(str(exc))


def _run():
    if not (ROOT / "manifest.json").is_file():
        st.info("Approve and export the 20 hallucination claims first.")
        return
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    st.caption(f"Separate suite: {manifest['dataset_id']} · {manifest['claim_count']} claims")
    mode = st.selectbox(
        "Hallucination prediction mode", ["Reuse matching predictions", "Cache only", "Fresh predictions"],
        key="h_prediction_mode",
    )
    modes = {"Reuse matching predictions": "reuse", "Cache only": "cache_only", "Fresh predictions": "fresh"}
    limit = st.number_input("Maximum new hallucination predictions", min_value=1, max_value=100,
                            value=5, key="h_prediction_budget")
    st.caption("Compound atoms also receive independent evaluations. Quota limits pause new work automatically.")
    if st.button("Run hallucination tests", type="primary", key="h_test"):
        try:
            with st.status("Running hallucination tests", expanded=True) as status:
                report = run_tests(cache_mode=modes[mode], max_new_predictions=int(limit), progress=status.write)
                st.session_state["h_report_path"] = report["report_path"]
                status.update(label="Hallucination predictions saved",
                              state="complete" if report["complete"] else "error")
        except Exception as exc:
            st.error(str(exc))
    for name in ("claims.jsonl", "ground_truth.jsonl", "manifest.json"):
        path = ROOT / name
        if path.is_file():
            st.download_button(f"Download separate {name}", path.read_bytes(),
                               file_name=f"hallucination_{name}", key=f"h_export_download_{name}")
    st.caption(f"These exports are stored under {ROOT}; the main evaluation files are preserved.")


def _results():
    folder = ROOT / "verdict_results"
    saved = sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []
    if not saved:
        st.info("Run hallucination predictions to begin grounding review.")
        return
    selected = st.selectbox("Saved hallucination prediction reports", saved,
                            format_func=lambda p: p.name, key="h_saved_reports")
    if st.button("Open hallucination report", key="h_open_report"):
        st.session_state["h_report_path"] = str(selected)
    path = Path(st.session_state.get("h_report_path") or selected)
    report = json.loads(path.read_text(encoding="utf-8"))
    st.download_button("Download hallucination prediction report", path.read_bytes(),
                       file_name=path.name, mime="application/json", key="h_prediction_download")
    if not report.get("valid_inputs", True):
        st.error("Run inputs changed. Resolve that issue before interpreting or reviewing predictions.")
        st.json(report.get("pause"))
        return
    if report.get("paused"):
        st.warning("Run paused: " + str((report.get("pause") or {}).get("reason") or "See saved report."))
    st.dataframe(report["summary"], hide_index=True, use_container_width=True)
    try:
        audit = grounding_report(report)
    except Exception as exc:
        st.error(str(exc))
        return
    st.caption("A pass requires the correct label, traceable source/calculation checks, and a human grounding assessment.")
    st.dataframe(audit["summary"], hide_index=True, use_container_width=True)
    st.caption("Final pass rate stays empty until every query at that level has a completed prediction and grounding review. Partial scores exclude service failures; coverage remains visible.")
    completed = {q["query_id"]: q for q in report["queries"] if q["status"] == "ok"}
    if completed:
        key = st.selectbox("Inspect and review grounding", list(completed), key=f"h_ground_query_{report['report_id']}")
        query = completed[key]
        review_row = next(row for row in audit["queries"] if row["query_id"] == key)
        review = review_row.get("review") or {}
        st.write(query["claim_text"])
        st.write(f"Expected: {query['expected']} · Predicted: {query['predicted']}")
        for issue in review_row["automatic_issues"]:
            st.error(issue)
        with st.expander("Prediction, explanation, cited pages and calculation addresses", expanded=True):
            st.json(query["results"])
        suffix = f"{report['report_id']}_{key}"
        with st.form(f"h_ground_review_{suffix}"):
            reviewer = st.text_input("Grounding reviewer", review.get("reviewer", ""))
            numbers = st.checkbox("All numbers are source-grounded or reproduced calculations; quoted claim values are clearly identified.",
                                  value=review.get("numbers_ok", False))
            quotes = st.checkbox("All quotations, source IDs and page references are accurate; none were manufactured.",
                                 value=review.get("quotes_ok", False))
            reasoning = st.checkbox("Every factual statement in the explanation is justified, and insufficiency is stated within the inspected scope.",
                                    value=review.get("reasoning_ok", False))
            notes = st.text_area("Grounding notes", review.get("notes", ""), height=90)
            submitted = st.form_submit_button("Save grounding assessment")
        if submitted:
            try:
                save_grounding_review(report, key, reviewer=reviewer, numbers_ok=numbers,
                                      quotes_ok=quotes, reasoning_ok=reasoning, notes=notes)
                _flash("Grounding assessment saved. Unchecked criteria record a failure.")
            except Exception as exc:
                st.error(str(exc))
    audit_path = ROOT / "grounding_results" / f"{report['report_id']}.json"
    st.download_button("Download hallucination grounding report", audit_path.read_bytes(),
                       file_name=f"hallucination_grounding_{report['report_id']}.json",
                       mime="application/json", key="h_grounding_download")
    with st.expander("All hallucination outcomes"):
        st.dataframe([{k: v for k, v in row.items() if k not in {"review", "automatic_issues"}}
                      for row in audit["queries"]], hide_index=True, use_container_width=True)


def render_hallucination_panel(selected_document_ids=None):
    st.divider()
    st.subheader("Hallucination resistance")
    st.caption("Separate 20-claim adversarial suite. AI drafts require source review; service failures never become UNKNOWN labels.")
    message = st.session_state.pop("hallucination_flash", None)
    if message:
        st.success(message)
    drafts, testing, results = st.tabs(["Draft and review", "Run tests", "Grounding review and results"])
    with drafts:
        _drafts(selected_document_ids)
    with testing:
        _run()
    with results:
        _results()

# from __future__ import annotations

# import json
# from pathlib import Path

# import streamlit as st

# from evaluation.hallucination import (
#     ROOT, add_drafts, counts, create_run, export_reviewed, grounding_report,
#     list_runs, load_run, local_grounding_checks, run_tests,
#     save_grounding_review, update_record,
# )


# def _flash(message):
#     st.session_state["hallucination_flash"] = message
#     st.rerun()


# def _review(run):
#     if not run["records"]:
#         st.info("Build drafts first. AI labels remain provisional until you inspect the source.")
#         return
#     mapping = {r["id"]: r for r in run["records"]}
#     key = st.selectbox(
#         "Review a hallucination-test draft", list(mapping),
#         format_func=lambda k: f"{k} · {mapping[k]['status']} · {mapping[k]['candidate']['category']}",
#         key=f"h_review_select_{run['dataset_id']}",
#     )
#     record = mapping[key]
#     candidate = record["candidate"]
#     st.caption(candidate["reviewer_instructions"])
#     doc = candidate["document_id"]
#     path = Path("data/documents") / doc / "original.pdf"
#     if path.is_file():
#         st.download_button("Download source PDF for review", path.read_bytes(),
#                            file_name=f"{doc}.pdf", mime="application/pdf",
#                            key=f"h_pdf_{run['dataset_id']}_{key}")
#     with st.expander("Draft source quotes and page references"):
#         source = json.loads((Path("data/processed") / doc / "evidence.json").read_text(encoding="utf-8"))
#         by_id = {str(e["id"]): e for e in source["evidence"]}
#         for atom in candidate["subclaims"]:
#             st.write(atom["claim_text"])
#             for quote in atom["citations"]:
#                 raw_id = quote["evidence_id"].removeprefix(doc + "::")
#                 item = by_id.get(raw_id) or by_id.get(quote["evidence_id"])
#                 st.caption(f"{quote['evidence_id']} · PDF page {item.get('page') if item else 'unresolved'}")
#                 st.text(quote["quote"])
#     prefix = f"{run['dataset_id']}_{key}_{record['version']}"
#     with st.form(f"h_review_{prefix}"):
#         edited = st.text_area("Editable test candidate", json.dumps(candidate, indent=2, ensure_ascii=False),
#                               height=430)
#         reviewer = st.text_input("Reviewer", record.get("reviewed_by") or "")
#         notes = st.text_area("Review notes", record.get("review_notes") or "", height=90)
#         source_checked = st.checkbox("I checked the cited source pages, numbers, units and periods.",
#                                      value=record.get("source_checked", False))
#         absence_checked = st.checkbox(
#             "For every UNKNOWN atom, I reviewed the complete document, including forecasts/footnotes, for missing evidence.",
#             value=record.get("unknown_scope_checked", False),
#         )
#         save = st.form_submit_button("Save as draft")
#         approve = st.form_submit_button("Approve", type="primary")
#         reject = st.form_submit_button("Reject")
#     if save or approve or reject:
#         try:
#             update_record(
#                 run["dataset_id"], key, json.loads(edited), expected_version=record["version"],
#                 status="approved" if approve else "rejected" if reject else "draft",
#                 reviewer=reviewer, notes=notes, source_checked=source_checked,
#                 unknown_scope_checked=absence_checked,
#             )
#             _flash(f"{key}: {'approved' if approve else 'rejected' if reject else 'saved as draft'}.")
#         except Exception as exc:
#             st.error(str(exc))


# def _drafts(selected_document_ids):
#     documents = {}
#     for path in sorted(Path("data/processed").glob("*/document.json")):
#         try:
#             d = json.loads(path.read_text(encoding="utf-8"))
#             if d.get("status") == "ready":
#                 documents[path.parent.name] = d.get("company_name") or d.get("filename") or path.parent.name
#         except (OSError, ValueError):
#             continue
#     ids = st.multiselect("Sources for the separate hallucination set", list(documents),
#                          default=[d for d in selected_document_ids or [] if d in documents],
#                          format_func=lambda d: documents[d], key="h_source_documents")
#     if st.button("Create separate 20-claim test set", disabled=not ids, key="h_create"):
#         try:
#             run = create_run(ids)
#             st.session_state["h_selected_run"] = run["dataset_id"]
#             _flash("Separate test set created. Your approved main dataset is unchanged.")
#         except Exception as exc:
#             st.error(str(exc))
#     runs = list_runs()
#     if not runs:
#         return
#     if st.session_state.get("h_selected_run") not in runs:
#         st.session_state["h_selected_run"] = runs[0]
#     chosen = st.selectbox("Saved hallucination draft sets", runs, key="h_selected_run")
#     run = load_run(chosen)
#     st.caption("Target: four cases each for missing forecasts, invented metrics, invented segments, wrong period/scope, and mixed compounds.")
#     st.dataframe(counts(run), hide_index=True, use_container_width=True)
#     batches = st.number_input("Maximum new draft requests per click", min_value=1, max_value=5,
#                               value=1, key="h_draft_budget")
#     st.caption("Each request drafts up to four cases. This uses the current text model and API quota.")
#     if st.button("Build / resume test drafts", key="h_build"):
#         try:
#             with st.status("Drafting hallucination cases", expanded=True) as status:
#                 run = add_drafts(chosen, max_batches=int(batches), progress=status.write)
#                 status.update(label="Draft progress saved", state="error" if run.get("pause") else "complete")
#             _flash("Draft progress saved. Review the generated candidates below.")
#         except Exception as exc:
#             st.error(str(exc))
#     if run.get("pause"):
#         st.warning((run["pause"] or {}).get("reason", "Generation paused."))
#     if run.get("generation_errors"):
#         with st.expander("Draft generation details"):
#             st.json(run["generation_errors"][-10:])
#     _review(run)
#     st.caption("Export requires exactly 20 approved claims, four per category, including counterevidence and compound cases.")
#     if st.button("Export 20 reviewed hallucination claims", key="h_export"):
#         try:
#             export_reviewed(chosen)
#             _flash("Exported the separate hallucination dataset.")
#         except Exception as exc:
#             st.error(str(exc))


# def _run():
#     if not (ROOT / "manifest.json").is_file():
#         st.info("Approve and export the 20 hallucination claims first.")
#         return
#     manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
#     st.caption(f"Separate suite: {manifest['dataset_id']} · {manifest['claim_count']} claims")
#     mode = st.selectbox(
#         "Hallucination prediction mode", ["Reuse matching predictions", "Cache only", "Fresh predictions"],
#         key="h_prediction_mode",
#     )
#     modes = {"Reuse matching predictions": "reuse", "Cache only": "cache_only", "Fresh predictions": "fresh"}
#     limit = st.number_input("Maximum new hallucination predictions", min_value=1, max_value=100,
#                             value=5, key="h_prediction_budget")
#     st.caption("Compound atoms also receive independent evaluations. Quota limits pause new work automatically.")
#     if st.button("Run hallucination tests", type="primary", key="h_test"):
#         try:
#             with st.status("Running hallucination tests", expanded=True) as status:
#                 report = run_tests(cache_mode=modes[mode], max_new_predictions=int(limit), progress=status.write)
#                 st.session_state["h_report_path"] = report["report_path"]
#                 status.update(label="Hallucination predictions saved",
#                               state="complete" if report["complete"] else "error")
#         except Exception as exc:
#             st.error(str(exc))
#     for name in ("claims.jsonl", "ground_truth.jsonl", "manifest.json"):
#         path = ROOT / name
#         if path.is_file():
#             st.download_button(f"Download separate {name}", path.read_bytes(),
#                                file_name=f"hallucination_{name}", key=f"h_export_download_{name}")
#     st.caption(f"These exports are stored under {ROOT}; the main evaluation files are preserved.")


# def _results():
#     folder = ROOT / "verdict_results"
#     saved = sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []
#     if not saved:
#         st.info("Run hallucination predictions to begin grounding review.")
#         return
#     selected = st.selectbox("Saved hallucination prediction reports", saved,
#                             format_func=lambda p: p.name, key="h_saved_reports")
#     if st.button("Open hallucination report", key="h_open_report"):
#         st.session_state["h_report_path"] = str(selected)
#     path = Path(st.session_state.get("h_report_path") or selected)
#     report = json.loads(path.read_text(encoding="utf-8"))
#     st.download_button("Download hallucination prediction report", path.read_bytes(),
#                        file_name=path.name, mime="application/json", key="h_prediction_download")
#     if not report.get("valid_inputs", True):
#         st.error("Run inputs changed. Resolve that issue before interpreting or reviewing predictions.")
#         st.json(report.get("pause"))
#         return
#     if report.get("paused"):
#         st.warning("Run paused: " + str((report.get("pause") or {}).get("reason") or "See saved report."))
#     st.dataframe(report["summary"], hide_index=True, use_container_width=True)
#     try:
#         audit = grounding_report(report)
#     except Exception as exc:
#         st.error(str(exc))
#         return
#     st.caption("A pass requires the correct label, traceable source/calculation checks, and a human grounding assessment.")
#     st.dataframe(audit["summary"], hide_index=True, use_container_width=True)
#     st.caption("Final pass rate stays empty until every query at that level has a completed prediction and grounding review. Partial scores exclude service failures; coverage remains visible.")
#     completed = {q["query_id"]: q for q in report["queries"] if q["status"] == "ok"}
#     if completed:
#         key = st.selectbox("Inspect and review grounding", list(completed), key=f"h_ground_query_{report['report_id']}")
#         query = completed[key]
#         review_row = next(row for row in audit["queries"] if row["query_id"] == key)
#         review = review_row.get("review") or {}
#         st.write(query["claim_text"])
#         st.write(f"Expected: {query['expected']} · Predicted: {query['predicted']}")
#         for issue in review_row["automatic_issues"]:
#             st.error(issue)
#         with st.expander("Prediction, explanation, cited pages and calculation addresses", expanded=True):
#             st.json(query["results"])
#         suffix = f"{report['report_id']}_{key}"
#         with st.form(f"h_ground_review_{suffix}"):
#             reviewer = st.text_input("Grounding reviewer", review.get("reviewer", ""))
#             numbers = st.checkbox("All numbers are source-grounded or reproduced calculations; quoted claim values are clearly identified.",
#                                   value=review.get("numbers_ok", False))
#             quotes = st.checkbox("All quotations, source IDs and page references are accurate; none were manufactured.",
#                                  value=review.get("quotes_ok", False))
#             reasoning = st.checkbox("Every factual statement in the explanation is justified, and insufficiency is stated within the inspected scope.",
#                                     value=review.get("reasoning_ok", False))
#             notes = st.text_area("Grounding notes", review.get("notes", ""), height=90)
#             submitted = st.form_submit_button("Save grounding assessment")
#         if submitted:
#             try:
#                 save_grounding_review(report, key, reviewer=reviewer, numbers_ok=numbers,
#                                       quotes_ok=quotes, reasoning_ok=reasoning, notes=notes)
#                 _flash("Grounding assessment saved. Unchecked criteria record a failure.")
#             except Exception as exc:
#                 st.error(str(exc))
#     audit_path = ROOT / "grounding_results" / f"{report['report_id']}.json"
#     st.download_button("Download hallucination grounding report", audit_path.read_bytes(),
#                        file_name=f"hallucination_grounding_{report['report_id']}.json",
#                        mime="application/json", key="h_grounding_download")
#     with st.expander("All hallucination outcomes"):
#         st.dataframe([{k: v for k, v in row.items() if k not in {"review", "automatic_issues"}}
#                       for row in audit["queries"]], hide_index=True, use_container_width=True)


# def render_hallucination_panel(selected_document_ids=None):
#     st.divider()
#     st.subheader("Hallucination resistance")
#     st.caption("Separate 20-claim adversarial suite. AI drafts require source review; service failures never become UNKNOWN labels.")
#     message = st.session_state.pop("hallucination_flash", None)
#     if message:
#         st.success(message)
#     drafts, testing, results = st.tabs(["Draft and review", "Run tests", "Grounding review and results"])
#     with drafts:
#         _drafts(selected_document_ids)
#     with testing:
#         _run()
#     with results:
#         _results()
