---
last_mapped_commit: 66b401a2bd7fe96b73fb782fe099e7ab3f6b9c0b
last_mapped_at: 2026-10-30
---
# External Integrations

**Analysis Date:** 2026-10-30

## APIs & External Services

**Text and vision reasoning:**
- Gemini via `google.genai`, selected by `LLM_PROVIDER=gemini` (`app/services/model_gateway.py`).
- Authentication names: `GOOGLE_API_KEY` or `GEMINI_API_KEY`; model names: `GEMINI_MODEL`, `GEMINI_VISION_MODEL`, `GEMINI_EVALUATION_MODEL`.
- OpenRouter via `httpx` at `https://openrouter.ai/api/v1`, selected by `LLM_PROVIDER=openrouter` (`app/services/model_gateway.py`).
- Authentication name: `OPENROUTER_API_KEY`; model names: `OPENROUTER_TEXT_MODEL`, `OPENROUTER_VISION_MODEL`, optional `OPENROUTER_DRAFT_MODEL`.
- Model catalog refresh is explicit; structured generation validates schemas and records call metadata (`app/services/model_gateway.py`).
- Fallbacks are controlled by `LLM_ALLOW_FALLBACKS`, `LLM_TEXT_FALLBACK_MODELS`, and `LLM_VISION_FALLBACK_MODELS`; request bounds use `LLM_TIMEOUT_SECONDS` and `LLM_MAX_OUTPUT_TOKENS`.

**Embeddings:**
- Gemini document/query embedding calls remain independent of the reasoning provider (`app/retrieval/embeddings.py`).
- Defaults in source: model `gemini-embedding-001`, 768 dimensions, pacing at 80 texts/minute per process.
- Overrides: `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIMENSIONS`, `GEMINI_EMBEDDING_TEXTS_PER_MINUTE`.
- Collection names hash the model/dimension identity; preserve compatible document and query vectors when changing configuration.

**OCR:**
- Google Cloud Vision and Gemini paths exist in `app/extraction/ocr.py`.
- Cloud Vision accepts Application Default Credentials, `GOOGLE_APPLICATION_CREDENTIALS`, or inline service-account configuration named `GOOGLE_CLOUD_VISION_CREDENTIALS_JSON`.
- Credential files and environment values were not inspected.

## Data Storage

**Databases:**
- SQLite at the configured customer root's `control.sqlite3` stores workspaces, memberships, documents, jobs, decisions, decision events, and worker heartbeat (`app/pilot/store.py`).
- Connections enable foreign keys; initialization enables WAL (`app/pilot/store.py`).
- Chroma persistent collections live at relative `data/chroma` (`app/retrieval/embeddings.py`).
- Supabase is used for identity, not the application document/job database (`app/pilot/auth.py`, `docs/SUPABASE_SETUP.md`).

**File Storage:**
- Internal pipelines persist source PDFs, extracted evidence, index metadata, and evaluation artifacts under `data/` (`README.md`, `app/pipelines/document_pipeline.py`).
- Customer data defaults to `customer_data/`, overridden by `CUSTOMER_DATA_ROOT` (`app/pilot/store.py`).
- Workspace-specific artifacts live beneath `customer_data/workspaces/<workspace-id>/data/`; job children run with that workspace as their working directory (`app/pilot_worker.py`).
- No object-storage service integration was detected in the mapped runtime paths.

**Caching:**
- Query embeddings use disk JSON caches under `data/cache/query_embeddings`, with configuration identity and vector integrity checks (`app/retrieval/embeddings.py`).
- BM25 structures are cached in process and invalidated using chunk-file timestamps/size (`app/retrieval/vector_store.py`).
- Vision/model cache helpers and extraction caches persist alongside processed documents (`app/services/model_gateway.py`, `app/reasoning/vision_client.py`, `app/extraction/ocr.py`).

## Authentication & Identity

**Auth Provider:**
- Supabase email/password, confirmation codes, and password recovery (`app/pilot/auth.py`).
- Configuration names: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SITE_URL`, `SUPABASE_ENABLE_SIGNUP`.
- Active code creates a fresh SDK client, verifies the returned user, and keeps auth tokens in the browser's Streamlit session (`app/pilot/auth.py`).
- The issuer plus user subject hashes to a local principal. SQLite membership grants access to a workspace (`app/pilot/store.py`).
- Admin membership operations are CLI actions in `app/pilot_admin.py`; authentication alone does not provision a workspace.
- OIDC instructions in `docs/CUSTOMER_SETUP.md` and placeholders in `.streamlit/config.toml` need reconciliation with the active Supabase implementation and `docs/SUPABASE_SETUP.md`.

## Monitoring & Observability

**Error Tracking:**
- No hosted error-tracking service detected in mapped application code.
- Operational errors have explicit exception/status handling rather than becoming factual UNKNOWN verdicts (`app/services/model_gateway.py`, `app/services/verification_errors.py`).

**Logs:**
- JSON lifecycle events go to stderr and per-process rotating files at `data/logs/app-<pid>.jsonl` (`app/services/observability.py`).
- Override the directory with `CLAIM_VERIFIER_LOG_DIR`; the worker sets a workspace-specific path (`app/pilot_worker.py`).
- Event fields are allowlisted; the event logger avoids raw claims, prompts, PDFs, credentials, and exception text (`app/services/observability.py`).
- Worker heartbeat is stored in SQLite (`app/pilot_worker.py`, `app/pilot/store.py`).

## CI/CD & Deployment

**Hosting:**
- Streamlit UI with a separate Python worker on persistent local storage (`docs/CUSTOMER_SETUP.md`).
- No checked-in cloud deployment manifest or container setup was detected.

**CI Pipeline:**
- No `.github` workflow directory or equivalent CI configuration was detected.
- Manual acceptance scenarios live in `docs/CUSTOMER_ACCEPTANCE.md`; access-control unit tests are in `tests/test_customer_access.py`.

## Environment Configuration

**Required env vars:**
- At least one Gemini key alias for embeddings and any Gemini model paths (`app/services/runtime.py`).
- Provider/model configuration for the selected reasoning path (`app/services/model_gateway.py`).
- Supabase URL and publishable key for customer sign-in (`app/pilot/auth.py`).
- Optional customer limits use `CUSTOMER_MAX_UPLOAD_MB`, `CUSTOMER_MAX_PAGES`, `CUSTOMER_MAX_DOCUMENTS`, `CUSTOMER_JOBS_PER_DAY`, and `CUSTOMER_JOB_TIMEOUT_SECONDS` (`app/pilot/store.py`, `app/pilot_worker.py`).

**Secrets location:**
- Project `.env`, process environment, or Streamlit secrets, depending on entry point. Only file existence and source-code configuration names were inspected.
- Never copy credentials or customer document contents into `.planning/`.

## Webhooks & Callbacks

**Incoming:**
- No custom HTTP webhook server detected. Customer auth handles email codes inside Streamlit (`app/pilot/auth.py`).

**Outgoing:**
- Model, embedding, OCR, and Supabase Auth requests are SDK/HTTP client calls from the server.
- Supabase email redirect configuration uses `SUPABASE_SITE_URL`; confirmation/recovery uses codes entered in the app (`app/pilot/auth.py`, `docs/SUPABASE_SETUP.md`).

---

*Integration audit: 2026-10-30*
