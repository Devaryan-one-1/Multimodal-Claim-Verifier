# Day 10 update

## Install after the current evaluation finishes

1. Save/download the current evaluation report and stop Streamlit.
2. Extract this archive into the project folder so `day10_update/` sits beside `app/` and `data/`.
3. Activate your existing virtual environment and run:

```bash
python day10_update/install_day10.py
streamlit run app/main.py
```

The installer only edits source/documentation. It does not import or run the application, call APIs, execute tests, generate embeddings or modify datasets. It reads UI syntax to preserve current imports and inserts guarded changes into the installed pipeline/gateway. Existing dashboard tabs and model settings are retained.

All expected code sections are checked before writing. If you see **Compatibility stop**, paste that message and the named current file; no source changes occur at that stage. Do not manually force an old replacement over your latest code.

Backups are stored under `day10_backups/<timestamp>/`. `CHANGED_FILES.txt` lists the affected files; `NEW_FILES.txt` identifies newly created files. The project README is backed up before replacement. To roll back, stop Streamlit, restore the backed-up files to their original relative paths and remove only the new files listed in NEW_FILES.txt. Keep the backup until your manual checks pass.

## What changes

- Adds logging, document errors/guards and dashboard application services.
- Patches current `main.py` to initialize logging while retaining your startup settings.
- Patches current document and verification pipelines with guarded event/error handling.
- Hooks the current gateway recorder without replacing its routing, quotas or tracing implementation.
- Redirects the relevant existing UI imports through application/evaluation service modules; all current UI bodies and tabs are retained.
- Replaces project README with architecture, setup, error behavior, limitations and actual historical retrieval metrics.

No new dependency is added. The PDF validator uses the PyMuPDF package already used by the pipeline. Main remains a small bootstrap; evaluation orchestration stays under `app/evaluation/`.

## Existing results and cache reuse

PDFs, evidence IDs, vectors, reviewed drafts and saved reports are not edited. However, your evaluators fingerprint source code. Applying this change may cause old prediction caches to be considered incompatible, leading to new API requests during future evaluations. Finish the active run before installation. Do not clear caches or regenerate the main dataset merely because a new code version needs fresh predictions.

## Manual checks — run these on your machine

No application, syntax or runtime tests were run by the assistant.

1. Open the dashboard. Confirm every existing evaluation tab and model setting is present. Check the ready 3M document still loads without reprocessing.
2. Check `data/logs/` for `app-<process-id>.jsonl`. On Windows PowerShell, view the newest file with:

```powershell
Get-ChildItem data/logs/app-*.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 30
```

3. Verify one known claim from your reviewed dataset. Check verdict, original/normalized values, signed difference, citations and View Page. Confirm correlated claim/retrieval/calculation/verdict events appear. Actual gateway calls should also have `llm_called` events; a cache hit need not trigger a provider call.
4. Check one known unsupported claim. Confirm UNKNOWN cites insufficiency, not fabricated values. A top-k search can still return irrelevant hits; this is not the same as empty retrieval. To exercise the truly empty-hit branch, use an isolated developer fixture rather than deleting your real index.
5. Upload a small invalid `.pdf` under a new document identity. It should fail before replacing stored PDF bytes. Check the UI gives an error and the event log records a document-processing failure.
6. Use separate small fixtures for a scanned page and a text-only PDF with no tables. Confirm coverage warnings, successful OCR if enabled/available, and no empty evidence index marked ready. OCR/model quota errors may stop processing; they are not factual verdicts.
7. Verify a reviewed missing-number or ambiguous-unit claim. It should show Calculation unavailable and a reason. Confirm a known contradiction still uses source values even when they differ from the claimed number.
8. On a small evaluation run, confirm provider failures remain errors/pauses and do not become scored UNKNOWN answers. A prediction-budget pause can be resumed; quota failures need provider availability first.
9. Recheck the historical report and approved dataset exports still exist. Run fresh benchmark measurements only after the behavior above is satisfactory; retain the historical baseline separately.

Malformed table containers and individual table records are guarded; this is not a complete semantic table-quality validator. Period/unit/cell checks remain in grounded_numeric.py. Reading a stored report without model calls is safe; starting new predictions consumes your provider quota.
