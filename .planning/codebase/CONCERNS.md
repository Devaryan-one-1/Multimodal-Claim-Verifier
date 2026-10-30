---
last_mapped_commit: 66b401a2bd7fe96b73fb782fe099e7ab3f6b9c0b
last_mapped_at: 2026-10-30
---
# Codebase Concerns

**Analysis Date:** 2026-10-30

Findings below distinguish source-confirmed behavior from runtime consequences to reproduce. Python could not launch in this session; no live model, browser, or customer-data checks were performed. This mapping does not change application behavior.

## Tech Debt

**Contradictory setup and review documents:**
- Issue: `docs/CUSTOMER_SETUP.md` and `docs/CUSTOMER_ACCEPTANCE.md` describe OIDC, while active `app/pilot/auth.py` uses Supabase email/password and codes, documented in `docs/SUPABASE_SETUP.md`.
- `.streamlit/config.toml` retains OIDC placeholders. `DEPLOYMENT_REVIEW.md` contains historical statements about missing authentication/tests that do not describe the current customer subsystem.
- Impact: setup, acceptance, and planning can follow the wrong identity flow or treat historical observations as current facts.
- Fix approach: reconcile setup/acceptance documents with active code, label the deployment review's scope/date clearly, and preserve the distinction between internal and customer entry points.

**Large modules and archived implementations:**
- Files: `app/reasoning/vision_client.py`, `app/extraction/ocr.py`, `app/evaluation/dataset.py`, `app/ui/upload.py`, `app/services/model_gateway.py`, `app/services/runtime.py`.
- Issue: large files and extensive commented historical implementations obscure active definitions. Backup/update/support trees contain additional copies.
- Impact: searches can surface inactive defaults or unsupported helpers.
- Fix approach: trace active callers, add focused regression tests, and separate historical material from runtime modules in a deliberate cleanup.

## Known Bugs

**Blank-cell cleanup can shift table column addresses:**
- Source-confirmed behavior: `_clean_table()` drops empty cells independently per row (`app/extraction/table_extractor.py:91`). The table guard accepts rows without restoring missing positions (`app/services/document_guards.py:25`).
- Scenario to reproduce: a row under `Metric | 2025 | 2024` with an empty 2025 cell and a populated 2024 cell can lose the blank column before positional source resolution.
- Risk: a normalized table can associate an amount with the wrong period; exact-looking row/column citations do not repair the earlier shift.
- Fix approach: preserve missing data positions; transform currency/spacer columns consistently for the entire table; cover leading/middle/trailing blanks and merged headers. Reprocess affected artifacts after a verified fix.

**Asymmetric quantity-kind comparison validation:**
- Source-confirmed behavior: `verify_numeric()` rejects non-percent claim literals for a percent result, but its amount/number comparison branch checks currency without an equivalent explicit percentage-kind rejection (`app/verification/grounded_numeric.py`, comparison section after calculation).
- Scenario to reproduce: a level claim containing a percent literal is compared with an amount/count operand of the same numeric magnitude.
- Risk: dimensional mismatch can survive arithmetic validation; the complete parser-to-verdict route needs a regression case before calling the scenario a reproduced bug.
- Fix approach: define compatible claim/operand/result dimensions for every operation and test percent-versus-amount in both directions, counts, currencies, per-share values, and percentage points.

**Gateway event-field mismatch loses observability fields:**
- Source-confirmed behavior: `app/services/model_gateway.py` records `gateway_provider`, `requested_model`, and `duration_seconds`; `record_model_call()` in `app/services/observability.py` reads `provider`, `model`, and `elapsed_ms`.
- Impact: central lifecycle model events can omit provider/model/timing even though gateway-specific logs contain them.
- Fix approach: normalize the event schema at one boundary and test emitted metadata without recording prompts or credentials.

## Security Considerations

**Keep internal entry points private:**
- Files: `app/main.py`, `app/ui/workspace.py`, `app/customer_main.py`.
- The internal dashboard operates on the shared internal corpus; the authenticated customer entry uses `pilot.auth` and local membership enforcement.
- Current mitigation: customer entry explicitly registers its authenticated page and jobs use workspace-scoped storage.
- Recommendation: expose the customer entry only; verify navigation and direct media/download paths with two accounts. Do not infer customer isolation from internal UI behavior.

**Download-link revocation and document retention require explicit policy:**
- Files: `docs/CUSTOMER_SETUP.md`, `app/pilot/exports.py`, `app/pilot_task.py`, `app/services/documents.py`.
- Current mitigation: service reads and exports authorize membership; worker deletion coordinates with queued work and removes source/index artifacts.
- Remaining boundary: native Streamlit media URLs are not a dedicated per-request authorized file API. Deletion retains historical review snapshots/decisions; it is not complete erasure of historical evidence and backups.
- Recommendation: align access/retention promises with those boundaries and test revocation and deletion across UI/export/history.

**Repository includes runtime artifacts:**
- Files: `.gitignore`, `data/`, `customer_data/`, generated `__pycache__/` paths.
- Inventory: 1,729 tracked paths under `data/`, two under `customer_data/`, and 72 Python cache paths at analysis time. Only tracked path counts were inspected; data contents were not classified.
- Impact: packaging the repository wholesale can include runtime state, documents, caches, or experiments. The ignore file primarily lists duplicate `.env` patterns.
- Recommendation: use an explicit release file list, separate runtime data from source, and review tracked artifacts before publishing. Removing tracked history is a separate operation, not part of onboarding.

**PDF execution is not OS-sandboxed:**
- Files: `app/pilot/store.py`, `app/pilot_worker.py`, `docs/CUSTOMER_SETUP.md`.
- Mitigations: byte/page limits, resource IDs/path containment, workspace child, and job timeout.
- Remaining boundary: native parser/render/model dependencies execute with worker OS permissions; compressed byte limits alone do not bound decoded pixels, memory, or total OCR cost.
- Recommendation: define rendering/resource budgets and deployment isolation before handling untrusted reports at scale.

## Performance Bottlenecks

**Process-wide query embedding lock includes network work:**
- Files: `app/retrieval/embeddings.py` (`embed_query`, `_query_lock`).
- Problem: one slow cache miss can block unrelated cache lookups/query embeddings in the same process.
- Improvement path: separate validated cache reads from network serialization, use per-key single-flight, and retain explicit rate-limit coordination.

**Lazy reranker initialization:**
- Files: `app/retrieval/reranker.py` (`_get_model`).
- Problem: first search loads `cross-encoder/ms-marco-MiniLM-L-6-v2`; no revision pin or initialization lock was found.
- Improvement path: verify/package a specific model revision and measure initialization/readiness separately from query latency.

**Large-table embedding truncation:**
- Files: `app/retrieval/embeddings.py` (`texts = ...[:12000]`), `app/extraction/chunker.py`.
- Risk: long chunks can omit later rows from semantic embedding input, even when lexical source chunks retain them.
- Improvement path: evaluate long-table claims and introduce row groups with shared headers and stable original row addresses if the measured retrieval gap warrants it.

## Fragile Areas

**Financial source meaning and citation validation:**
- Files: `app/verification/grounded_numeric.py`, `app/pipelines/verification_pipeline.py`, `app/extraction/table_extractor.py`.
- Why fragile: source addresses, temporal scope, unit context, company identification, and quote traceability interact. A verbatim quote proves presence, not necessarily entailment.
- Safe modification: use synthetic/public cases with known financial scope, preserve original values/addresses, and test source-to-verdict behavior as well as arithmetic.
- Test coverage: no automated grounding/extraction regression tests were detected in `tests/`.

**Relative paths, task identity, and resume:**
- Files: `app/pilot_task.py`, `app/pilot_worker.py`, `app/services/runtime.py`.
- Why fragile: jobs depend on workspace working directory and fingerprint all application Python files. Source or model changes can prevent paused work from resuming.
- Safe modification: finish or explicitly pause/reconcile work before rollout, retain source/model versions, and test kill/recovery at checkpoint boundaries.
- Test coverage: current access tests do not execute the worker.

## Scaling Limits

**Single-host serial queue:**
- Files: `app/pilot_worker.py`, `app/pilot/store.py`.
- Current bounds in source: one active supervisor/child, ten pending workspace requests, defaults of 100 jobs/day/workspace, 30 documents/workspace, 10 MB uploads, 500 PDF pages, and a 3,600-second task timeout.
- Several limits are environment configurable; these are code defaults, not measured capacity or confirmed deployment settings.
- Scaling path: define target concurrency and shared quota/storage coordination before introducing multiple hosts/replicas. Process-local locks/pacing do not coordinate separate applications.

## Dependencies at Risk

**Installation reproducibility and direct dependency declarations:**
- Files: `requirements.txt`, `requirements-customer.txt`, `openrouter_support/requirements-openrouter.txt`, `app/services/model_gateway.py`.
- Risk: broad ranges without a lock/runtime pin; `httpx` and `jsonschema` are direct runtime imports but declared only in the support requirements.
- Impact: clean installation depends on transitive packages or additional manual installation; the local Python 3.14 environment was not runnable in this session.
- Fix approach: consolidate runtime dependencies, choose a tested interpreter/build, and validate clean install plus offline smoke tests. No vulnerability audit or provider-availability assessment was performed.

## Missing Critical Features

**Automated release validation:**
- Files: `tests/test_customer_access.py`, `docs/CUSTOMER_ACCEPTANCE.md`; no CI workflow/configuration was detected.
- Gap: access-control checks exist, but automated worker, auth flow, extraction, numeric grounding, and browser checks are not established.
- Blocks: repeatable evidence that a candidate release works across supported environments and failure modes.

**Measured current verification quality:**
- Files: `README.md`, `app/evaluation/`, `app/pilot/benchmark.py`.
- Gap: README distinguishes historical retrieval metrics from pending/partial verdict, hallucination, and ablation results.
- Blocks: claiming a current full-pipeline accuracy or calibrated confidence result. Preserve completion/error denominators and human grounding review in new evaluations.

## Test Coverage Gaps

- **High:** table position preservation and bidirectional quantity-dimension checks (`app/extraction/table_extractor.py`, `app/verification/grounded_numeric.py`).
- **High:** real Supabase signup/confirmation/recovery/session isolation (`app/pilot/auth.py`); existing service tests do not cover identity verification.
- **High:** worker termination, source/model/code changes, retry, deletion, and checkpoint publication (`app/pilot_worker.py`, `app/pilot_task.py`).
- **High:** no-evidence versus operational-error behavior, quote/source scope, and compound claims (`app/pipelines/verification_pipeline.py`).
- **Medium:** vector/BM25 fusion, cache identity, reranker behavior, long-table retrieval, and provider/schema failures (`app/retrieval/`, `app/services/model_gateway.py`).
- **Medium:** browser navigation, media-link access/revocation, keyboard/narrow-screen behavior, and evidence coordinates (`app/customer_main.py`, `app/pilot/ui.py`, `app/ui/document_viewer.py`).

---

*Concerns audit: 2026-10-30*
