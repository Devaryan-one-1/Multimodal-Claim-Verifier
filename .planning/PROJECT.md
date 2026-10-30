# Multimodal Financial Claim Verifier

## What This Is

A Python/Streamlit application that checks financial assertions against uploaded PDF reports using text, tables, OCR, charts, retrieval, and deterministic calculations. It includes an internal research/evaluation dashboard and a separate authenticated customer workspace with queued background processing and review exports.

The current GSD scope is to document and organize the existing application. The user selected this scope on 2026-10-30; no new product features or implementation improvements are requested for this initialization.

## Core Value

Financial claim reviews should remain traceable to source evidence, calculations, and execution status so a reviewer can understand the basis and limitations of a result.

## Business Context

- **Users**: Internal researchers/evaluators and customer workspace reviewers, as represented by the existing entry points.
- **Revenue model**: Not specified; no commercial assumptions are part of this scope.
- **Success metric for this work**: A complete, linked description of the existing system and GSD onboarding state without unsupported claims of runtime correctness.

## Requirements

### Validated

These are existing capabilities inferred from the source-backed codebase map. In this brownfield baseline, "Validated" identifies existing implementation, not a new successful runtime, accuracy, or deployment test.

- ✓ PDF validation, text/layout/table extraction, optional OCR/chart analysis, evidence chunking, and indexing — existing in `app/pipelines/document_pipeline.py` and `app/extraction/`.
- ✓ Gemini document/query embeddings, persistent Chroma storage, BM25/vector rank fusion, and cross-encoder reranking — existing in `app/retrieval/`.
- ✓ Atomic claim parsing, source/company scoping, grounded numeric calculations, evidence fusion, and source-quote validation — existing in `app/pipelines/verification_pipeline.py`, `app/verification/`, and `app/reasoning/`.
- ✓ Internal document review and evaluation workflows — existing in `app/main.py`, `app/ui/`, and `app/evaluation/`.
- ✓ Supabase email/password identity, local workspace membership checks, and protected customer service operations — existing in `app/customer_main.py`, `app/pilot/auth.py`, and `app/pilot/store.py`.
- ✓ Durable customer jobs, workspace child processes, checkpoints, review decisions, and CSV/HTML/JSON exports — existing in `app/pilot_worker.py`, `app/pilot_task.py`, and `app/pilot/`.
- ✓ Service-level customer access, export safety, and benchmark tests — existing in `tests/test_customer_access.py`; execution was unavailable during mapping.
- ✓ Seven source-backed codebase reference documents — committed under `.planning/codebase/` as `00dd3e4`.

### Active

- [ ] Document the application's purpose, current capabilities, operating boundaries, and source-inspection limits in the project baseline.
- [ ] Organize GSD requirements, roadmap, and state around documentation of the existing system.
- [ ] Create a linked onboarding summary that points to the codebase map, existing setup/acceptance guides, known concerns, and the next appropriate GSD action.
- [ ] Record future improvement candidates as deferred context without turning them into an implementation commitment.

### Out of Scope

- New product features, UI redesign, or architecture migration — the user chose documentation of the existing app for now.
- Fixing source-code defects or rewriting authentication/storage/worker behavior — findings remain documented for a future explicitly scoped effort.
- Customer pilot launch, hosting changes, public deployment, or release approval — this initialization does not establish deployment readiness.
- New live model experiments, reprocessing PDFs, rebuilding embeddings, changing provider configuration, or modifying customer data — unnecessary for the selected documentation scope.
- Claiming current verification accuracy, calibrated confidence, security certification, or successful runtime acceptance — no evidence for those conclusions was gathered in this onboarding session.

## Context

### Repository and Existing Work

- Repository root: `F:/Multi Modal Change Detector/multimodal-claim-verifier/`.
- The parent workspace also contains a separate virtual environment and other assets; it is not the application Git root.
- `.planning/codebase/` contains `STACK.md`, `INTEGRATIONS.md`, `ARCHITECTURE.md`, `STRUCTURE.md`, `CONVENTIONS.md`, `TESTING.md`, and `CONCERNS.md`.
- Mapping commit: `00dd3e43b7a5af6a32331b89598dac195b0af35d`; map source baseline: `66b401a2bd7fe96b73fb782fe099e7ab3f6b9c0b`.
- Existing local source/configuration changes and untracked customer/deployment documents were present before initialization. They belong to the user's ongoing work and are not part of the GSD documentation commit.

### Architecture and Operation

- `streamlit run app/main.py` starts the internal dashboard, including evaluation workflows.
- `streamlit run app/customer_main.py` starts the customer interface; `python app/pilot_worker.py` starts its separate supervisor.
- `app/pilot_admin.py` manages workspace membership and interrupted-job recovery.
- `app/pilot_task.py` runs processing/verification/deletion inside a workspace-specific working directory.
- Domain code uses relative `data/` paths. Internal artifacts live under the repository's `data/`; customer artifacts live under a configured customer root with a SQLite control database and per-workspace files.
- Customer authentication uses Supabase identity plus local database membership. Some older setup/acceptance instructions and Streamlit placeholders still describe OIDC; use the codebase map and `docs/SUPABASE_SETUP.md` to interpret the active implementation.
- Gemini embeddings remain independent of Gemini/OpenRouter reasoning selection; model/dimension compatibility matters for existing indexes.
- The current queue is a single-host, persistent-disk design with one supervisor and serial job children.

### Documentation Sources

- `README.md`: purpose, internal setup, workflows, historical evaluation context, and limitations.
- `docs/CUSTOMER_SETUP.md`: customer storage/worker/administration design; identity instructions require reconciliation with active Supabase code.
- `docs/SUPABASE_SETUP.md`: active authentication setup context, present as a user-owned untracked document at initialization.
- `docs/CUSTOMER_ACCEPTANCE.md`: manual service/browser/multi-account scenarios; some identity terminology is stale.
- `DEPLOYMENT_REVIEW.md`: historical review; several statements concern an earlier source state and must not be treated as a fresh audit.

### Known Limits and Verification Status

- `CONCERNS.md` records source-derived concerns, including table blank-cell position handling, asymmetric quantity-kind checks, model-event field mismatch, installation reproducibility, runtime artifact tracking, and missing regression coverage.
- These concerns are reference material, not approved implementation requirements for this scope.
- The configured sibling Python environment could not launch under the session account. Automated tests, AST/runtime checks, browser acceptance, and live provider checks were not completed during mapping.
- Historical retrieval metrics and pending/partial verdict experiments are distinct; this baseline introduces no new performance result.
- Environment files, credential values, and customer document contents were not read for the codebase map.

## Constraints

- **Scope**: Document and organize existing behavior only — the user selected option 1 "for now".
- **Evidence**: Cite source paths and distinguish implementation observations from executed tests — documentation must not imply validation that did not occur.
- **Preservation**: Keep the user's existing changes, source documents, vectors, customer data, and provider configuration intact — they are not needed to establish the baseline.
- **Compatibility**: Describe the existing Python/Streamlit, SQLite/local files, Chroma, Gemini/OpenRouter, and Supabase design — no stack replacement is requested.
- **Secrets**: Document configuration names and locations, never credential values or private document content.
- **Future work**: Any feature, bug-fix, deployment, or experimental milestone needs a separately agreed scope.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Initialize GSD around documentation of the existing application | User selected this scope on 2026-10-30 | Accepted scope |
| Use the nested application repository as the planning root | It contains the active application and Git history | Established |
| Use the complete codebase map as the implementation reference | Seven documents were checked and committed before initialization | Established |
| Infer existing capabilities from source while recording runtime validation limits | Python execution was unavailable; source presence is not proof of correctness | Explicit baseline rule |
| Keep improvement findings deferred | Documentation onboarding does not authorize implementation changes | Pending future scope |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Move invalidated requirements to Out of Scope with a reason.
2. Move verified requirements to Validated with the phase and evidence reference.
3. Add newly agreed requirements to Active.
4. Record decisions and update the current description when needed.

**After each milestone:**
1. Review all sections, including whether the core value still drives the work.
2. Revisit deferred scope and record the user's next goal before proposing implementation phases.
3. Update context, verification evidence, and known limitations.

---

*Last updated: 2026-10-30 after documentation-only brownfield initialization.*
