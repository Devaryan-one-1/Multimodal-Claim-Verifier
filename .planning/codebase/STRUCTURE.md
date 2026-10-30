---
last_mapped_commit: 66b401a2bd7fe96b73fb782fe099e7ab3f6b9c0b
last_mapped_at: 2026-10-30
---
# Codebase Structure

**Analysis Date:** 2026-10-30

## Directory Layout

```text
multimodal-claim-verifier/
  app/
    main.py                 Internal Streamlit entry
    customer_main.py        Authenticated customer entry
    pilot_worker.py         Queue supervisor
    pilot_task.py           Workspace job child
    pilot_admin.py          Administrative CLI
    ui/                     Internal presentation components
    pilot/                  Customer UI, auth, storage, exports, scoring
    services/               Application boundaries and infrastructure
    pipelines/              Document, verification, ablation orchestration
    extraction/             PDF/layout/table/OCR/chart extraction
    retrieval/              Embeddings, lexical/vector retrieval, reranking
    verification/           Assertion parsing, numeric grounding, calculations
    reasoning/              Evidence fusion, LLM/vision clients, schemas
    evaluation/             Dataset and experiment workflows
    pages/                  Legacy internal document-viewer page
  tests/                    Customer access and export unit tests
  docs/                     Customer setup and manual acceptance guides
  config_examples/          Deployment/auth configuration examples
  .streamlit/               Streamlit configuration
  .planning/codebase/       GSD source-backed map
  data/                     Internal sources, indexes, caches, experiments, logs
  customer_data/            Customer control database/workspaces
  openrouter_support/       Support installer, extra requirements, module copies
  code_backups/             Historical implementation backups
  day10_backups/            Historical refactor backup
  day10_update/             Update payload and installation instructions
  notebooks/               Present, no files inspected for this map
  README.md
  DEPLOYMENT_REVIEW.md
  requirements.txt
  requirements-customer.txt
```

The Git repository is this directory. Its parent `F:/Multi Modal Change Detector/` also contains a separate `venv/`, data, and unrelated artifacts; do not map that environment as application source.

## Directory Purposes

**Application (`app/`):**
- Active runtime source. Prefer these modules over backup/support payload copies.
- Important boundaries: `app/services/application.py`, `app/pipelines/document_pipeline.py`, `app/pipelines/verification_pipeline.py`.

**Customer subsystem (`app/pilot/`):**
- `auth.py`: Supabase session validation and workspace access selection.
- `store.py`: SQLite schema, service authorization, safe resource paths, uploads, queue, review decisions.
- `ui.py`: authenticated review workflow and document interactions.
- `exports.py`: authorized CSV/HTML/JSON output.
- `benchmark.py`: offline scoring utility.

**Internal presentation (`app/ui/`):**
- `workspace.py`: dashboard composition.
- `upload.py`, `claim_input.py`: input state and processing flow.
- `evidence.py`, `document_viewer.py`, `calculation.py`, `verdict.py`: result presentation.
- Evaluation views delegate through `app/services/evaluation_*.py` to `app/evaluation/`.

**Domain directories:**
- `app/extraction/`: source decoding and evidence construction.
- `app/retrieval/`: embedding/index/search/reranker code; `vector_store.py` is the active verification search dependency.
- `app/verification/`: parsed assertions and deterministic arithmetic.
- `app/reasoning/`: typed responses, evidence fusion, vision extraction.
- `app/evaluation/`: datasets, coverage, retrieval/verdict/hallucination/ablation experiments.

## Key File Locations

**Entry Points:**
- `app/main.py`: internal UI.
- `app/customer_main.py`: customer UI.
- `app/pilot_worker.py`: supervisor; `app/pilot_task.py`: private task runner.
- `app/pilot_admin.py`: workspace membership administration/recovery.

**Configuration:**
- `requirements.txt`: primary dependency ranges.
- `requirements-customer.txt`: customer additions.
- `openrouter_support/requirements-openrouter.txt`: additional gateway dependencies.
- `.streamlit/config.toml`: theme/server settings and legacy auth placeholders.
- `.env`: present, contents not inspected.
- `docs/SUPABASE_SETUP.md`: active identity setup; older customer setup instructions require reconciliation.

**Core Logic:**
- `app/services/runtime.py`: root/environment loading, IO, retries, hashing.
- `app/services/model_gateway.py`: reasoning/vision providers.
- `app/retrieval/embeddings.py`: Gemini vectors and index identity.
- `app/verification/grounded_numeric.py`: operand/source/period/unit validation.
- `app/verification/financial_calculations.py`: deterministic growth/change/margin/CAGR operations.
- `app/reasoning/evidence_fusion.py`: deduplication, conflicts, numeric-source priority.

**Testing:**
- `tests/test_customer_access.py`: existing automated suite.
- `docs/CUSTOMER_ACCEPTANCE.md`: manual multi-user/retry/source/export scenarios.
- `vision_pipeline_diagnostic.py`, `repair_document_readiness.py`, `t.py`: diagnostics/maintenance, not substitutes for isolated unit tests.

## Naming Conventions

**Files:**
- Python modules use `snake_case.py`; test modules use `test_*.py`.
- Service names reflect workflow scope, e.g. `evaluation_retrieval_evaluation.py`.
- Codebase documents use uppercase names under `.planning/codebase/`.

**Directories:**
- Functional domain names under `app/`; generated storage is under relative `data/` or the configured customer root.

## Where to Add New Code

**New Feature:**
- Put processing orchestration in `app/pipelines/`, domain logic in its matching functional directory, and stable internal UI entry functions in `app/services/`.
- Add customer interactions to `app/pilot/ui.py` and authorized persistence/queue operations to `app/pilot/store.py`.
- Add tests in `tests/` using the existing unittest style; add acceptance steps to `docs/CUSTOMER_ACCEPTANCE.md` when relevant.

**New Component/Module:**
- Internal presentation belongs in `app/ui/`; avoid introducing Streamlit dependencies into numeric/extraction logic.
- Provider routing belongs in `app/services/model_gateway.py`; Gemini embeddings remain in `app/retrieval/embeddings.py`.

**Utilities:**
- Use `app/services/runtime.py` for atomic JSON IO/hashing and `app/services/observability.py` for lifecycle events.
- Keep customer file access behind `app/pilot/store.py` authorization/path helpers.

## Special Directories

**`data/`:**
- Contains `documents/`, `processed/`, `pages/`, `charts/`, `chroma/`, `cache/`, `claim_cache/`, `evaluation/`, `logs/`, `test_assets/`, and `test_results/`.
- Mixes runtime and experiment artifacts; contents were not read during mapping.

**`customer_data/`:**
- Customer storage root; contains durable control state and per-workspace files as defined by `app/pilot/store.py`.
- Do not reuse internal `data/` for a new customer workspace.

**Historical/support trees:**
- `code_backups/`, `day10_backups/`, `day10_update/`, and `openrouter_support/` contain copies/install payloads. Verify imports against `app/` before changing runtime behavior.

**Generated Python caches:**
- `__pycache__/` files exist; at least the customer-test cache is tracked in the current repository inventory.
- `.gitignore` currently focuses on `.env` patterns; artifact hygiene needs attention (see `CONCERNS.md`).

---

*Structure analysis: 2026-10-30*
