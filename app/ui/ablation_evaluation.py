from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from evaluation.ablation_evaluation import (
    DEFAULT_DATASET_DIR, LABELS, OVERRIDE_FILENAME, SCHEMA_VERSION,
    evaluate_ablation, inspect_ablation_sources, save_modality_overrides,
)
from retrieval.ablation_search import VARIANTS


def render_ablation_evaluation():
    st.divider()
    st.subheader("Ablation study")
    st.caption("Compare the same reviewed claims with text, tables, charts and deterministic numerical verification.")
    st.dataframe([{"Variant": key, "Evidence": item["name"],
                   "Arithmetic": "Python verification" if item["numerical"] else "LLM reasoning — unverified"}
                  for key, item in VARIANTS.items()], hide_index=True, use_container_width=True)
    paths = [DEFAULT_DATASET_DIR / name for name in ("claims.jsonl", "ground_truth.jsonl", "manifest.json")]
    if not all(path.is_file() for path in paths):
        st.info("Approve and export the evaluation dataset first.")
        return
    try:
        manifest = json.loads(paths[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.error(f"Could not read the dataset manifest: {exc}")
        return
    st.caption(f"Dataset: {manifest.get('dataset_id')} · All {manifest.get('claim_count')} exported claims")

    with st.expander("Review modality coverage and OCR classification", expanded=False):
        st.write("Classification follows evidence content. Table/chart regions and duplicate text are excluded from prose retrieval. Unclassified OCR is excluded until reviewed.")
        if st.button("Inspect source coverage — no API calls", key="ablation_inspect"):
            try:
                st.session_state.ablation_inventory = inspect_ablation_sources()
            except Exception as exc:
                st.error(str(exc))
        inspected = st.session_state.get("ablation_inventory")
        if inspected:
            st.dataframe(inspected["coverage"], hide_index=True, use_container_width=True)
            if not any(row["modality"] == "chart" and row["indexed_chunks"] for row in inspected["coverage"]):
                st.warning("No eligible chart chunks are present. This source set cannot establish the contribution of charts.")
            inventory = inspected["inventory"]
            st.download_button("Download evidence classification inventory",
                               json.dumps(inventory, ensure_ascii=False, indent=2),
                               file_name="ablation_source_inventory.json", mime="application/json",
                               key="ablation_inventory_download")
            excluded_only = st.checkbox("Show excluded evidence only", value=True, key="ablation_excluded")
            st.dataframe([row for row in inventory if not excluded_only or row["ablation_modality"] == "exclude"],
                         hide_index=True, use_container_width=True)
        override_path = DEFAULT_DATASET_DIR / OVERRIDE_FILENAME
        try:
            saved_overrides = json.loads(override_path.read_text(encoding="utf-8")) if override_path.is_file() else {}
        except (OSError, ValueError) as exc:
            st.error(f"Could not read content overrides: {exc}")
            saved_overrides = {}
        with st.form("ablation_modality_form"):
            edited = st.text_area("Content classification overrides (JSON)",
                                  value=json.dumps(saved_overrides, ensure_ascii=False, indent=2), height=180,
                                  help='Use inventory override keys: {"document_id::source_id": "text"}. Values: text, table, chart, exclude.')
            st.caption("Inspect the actual source page before assigning OCR to prose. Mixed prose/table/chart OCR must be classified as a whole or excluded; this study does not re-extract or split it.")
            if st.form_submit_button("Save content classifications"):
                try:
                    save_modality_overrides(json.loads(edited))
                    st.session_state.ablation_inventory = inspect_ablation_sources()
                    st.success("Saved. A changed classification creates a different study cache signature.")
                except Exception as exc:
                    st.error(str(exc))

    modes = {"Cache only — no API requests": "cache_only",
             "Run / resume — compute missing stages": "reuse",
             "Fresh study — ignore saved study stages": "fresh"}
    mode_label = st.selectbox("Ablation prediction mode", list(modes), key="ablation_mode")
    selected = st.multiselect("Variants to run", list(VARIANTS), default=list(VARIANTS), key="ablation_variants")
    stop = st.checkbox("Stop on the first study error", value=True, key="ablation_stop")
    st.caption("Candidate depth: 20 · Rerank keep: 10 · One shared parser · Same selected reasoning model · Automatic fallback disabled")
    if modes[mode_label] == "cache_only":
        st.info("This mode reads matching study caches only. Inspect source coverage while API quota is unavailable.")
    else:
        st.caption("A full study can require several API calls per claim across four variants, especially for compound claims. Progress is saved after each successful stage. Query vectors reuse the existing Gemini cache.")
    if st.button("Score cached ablation predictions" if modes[mode_label] == "cache_only" else "Run / resume ablation study",
                 key="ablation_run", type="primary", disabled=not selected):
        with st.status("Preparing ablation study", expanded=True) as status:
            try:
                report = evaluate_ablation(variants=tuple(selected), cache_mode=modes[mode_label],
                                           stop_on_error=stop, progress=status.write)
                st.session_state.ablation_report_path = report["report_path"]
                status.update(label="Study complete" if report["complete"] else "Partial study saved",
                              state="complete" if report["complete"] else "error")
            except Exception as exc:
                status.update(label="Study could not run", state="error")
                st.error(str(exc))

    folder = DEFAULT_DATASET_DIR / "ablation_results"
    saved = sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []
    if saved:
        chosen = st.selectbox("Saved ablation reports", saved, format_func=lambda path: path.name, key="ablation_saved")
        if st.button("Open saved ablation report", key="ablation_open"):
            st.session_state.ablation_report_path = str(chosen)
    chosen_path = st.session_state.get("ablation_report_path")
    if not chosen_path:
        return
    path = Path(chosen_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported ablation report format")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        st.error(f"Could not open the study report: {exc}")
        return
    st.download_button("Download ablation report", path.read_bytes(), file_name=path.name,
                       mime="application/json", key="ablation_report_download_" + path.stem)
    if (report.get("dataset_id") != manifest.get("dataset_id")
            or report.get("dataset_revision") != manifest.get("revision")
            or any(report.get("dataset_checksums", {}).get(key) != manifest.get(key)
                   for key in ("claims_sha256", "ground_truth_sha256"))):
        st.warning("Historical report from a different dataset export. Download it or run the current export separately.")
        return
    if report.get("abort_reason"):
        st.error("Study scores were invalidated: " + report["abort_reason"])
    elif not report.get("complete"):
        st.warning("Incomplete study. Scores are preliminary; resume with the same model, source classifications and settings.")
    st.markdown("**Coverage by variant**")
    st.dataframe(report["summary"], hide_index=True, use_container_width=True)
    matched = len(report["paired_claim_ids"])
    st.markdown("**Comparison on the same claims**")
    st.caption(f"{matched}/{manifest.get('claim_count')} claims have successful predictions for all four variants. Errors are excluded, never relabeled UNKNOWN.")
    st.dataframe(report["paired_summary"], hide_index=True, use_container_width=True)
    if matched:
        chart = pd.DataFrame([{"Variant": row["variant"], "Accuracy (%)": 100 * row["accuracy"]}
                              for row in report["paired_summary"]])
        st.bar_chart(chart, x="Variant", y="Accuracy (%)")
        st.download_button("Download matched comparison CSV", chart.to_csv(index=False),
                           file_name="ablation_accuracy.csv", mime="text/csv", key="ablation_chart_download")
        st.dataframe(report["deltas"], hide_index=True, use_container_width=True)
    else:
        st.info("The comparison chart appears once at least one identical claim is completed by all four variants.")
    st.caption("Higher scores are not guaranteed. C → D measures both deterministic arithmetic and the safeguard that requires a valid calculation for numerical verdicts.")

    variant = st.selectbox("Inspect variant metrics", list(VARIANTS), key="ablation_metric_variant")
    matched_only = st.checkbox("Use only claims completed by all four variants", value=True, key="ablation_metrics_matched")
    metrics = report["paired_metrics" if matched_only else "metrics"][variant]
    st.markdown("**Per-verdict precision, recall and F1**")
    st.dataframe(metrics["per_class"], hide_index=True, use_container_width=True)
    st.caption("Values are 0–1. Macro averages use all four labels; undefined class denominators score zero. With no completed predictions, scores are unavailable.")
    st.markdown("**Confusion matrix**")
    st.caption("Rows: reviewed verdict. Columns: predicted verdict.")
    st.dataframe([{"Actual": label, **metrics["confusion_matrix"][label]} for label in LABELS],
                 hide_index=True, use_container_width=True)
    with st.expander("Per-claim predictions and errors", expanded=False):
        st.dataframe([{key: row.get(key) for key in ("claim_id", "variant", "claim_text", "expected",
                                                    "predicted", "correct", "status", "prediction_source", "error")}
                      for row in report["queries"]], hide_index=True, use_container_width=True)
        choices = {row["claim_id"] + " / " + row["variant"]: row for row in report["queries"]}
        inspect = st.selectbox("Inspect prediction", list(choices), key="ablation_inspect_prediction_" + report["report_id"])
        st.json(choices[inspect])
    with st.expander("Study configuration, source coverage and model calls", expanded=False):
        st.json({key: report.get(key) for key in ("configuration", "metric_policy", "source_coverage",
                                                 "checkpoint_counts", "model_calls")})
