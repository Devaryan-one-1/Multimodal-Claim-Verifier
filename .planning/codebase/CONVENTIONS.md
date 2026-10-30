---
last_mapped_commit: 66b401a2bd7fe96b73fb782fe099e7ab3f6b9c0b
last_mapped_at: 2026-10-30
---
# Coding Conventions

**Analysis Date:** 2026-10-30

## Naming Patterns

**Files:**
- Use `snake_case.py` in the appropriate functional directory, such as `app/verification/grounded_numeric.py` and `app/services/document_guards.py`.
- Use `test_*.py` for tests (`tests/test_customer_access.py`).

**Functions:**
- Use `snake_case`; internal helpers use a leading underscore (`_verify_one`, `_documents` in `app/pipelines/verification_pipeline.py`).
- Name boundary functions after the action: `process_document`, `verify_claims`, `submit_review`, `request_delete`.

**Variables:**
- Use lowercase snake case for local/state variables and uppercase for module configuration (`EMBED_MODEL`, `DIMENSIONS` in `app/retrieval/embeddings.py`).
- Preserve established result-field names such as `calculation_unavailable_reason`, `error_stage`, and `source_evidence_id` across UI/pipeline boundaries.

**Types:**
- Use PascalCase for dataclasses, Pydantic models, and exceptions: `Access`, `ModelConfig`, `GroundedResult`, `VerificationFailure`.
- Newer boundaries use union annotations and typed containers; much legacy/domain glue still passes dictionaries (`app/pilot/store.py`, `app/pipelines/verification_pipeline.py`).

## Code Style

**Formatting:**
- Python code generally uses four-space indentation and frequently `from __future__ import annotations`.
- Style is mixed: expanded multi-line code in domain modules and compact/semicolon statements in customer code/tests. No formatter configuration was detected.
- Match the surrounding module while keeping changes readable; avoid whole-file formatting during focused work.

**Linting:**
- No checked-in Ruff, flake8, Black, mypy, or equivalent configuration was detected.
- Do not assume static analysis is already part of the project validation pipeline.

## Import Organization

**Order:**
1. Module docstring and future imports where present.
2. Standard-library imports.
3. Third-party dependencies.
4. Application imports, with exceptions in some modified modules.

**Path Aliases:**
- Entry points and tests add `app/` to `sys.path`; domain modules import `services.*`, `verification.*`, `pilot.*`, etc. (`app/main.py`, `app/customer_main.py`, `tests/test_customer_access.py`).
- The application facade uses function-local imports for deferred loading (`app/services/application.py`). Preserve those boundaries when adding facade methods.

## Error Handling

**Patterns:**
- Raise typed boundary failures with stages, e.g. `VerificationFailure(message, stage="document_scope")` (`app/services/verification_errors.py`, `app/pipelines/verification_pipeline.py`).
- Keep execution status separate from factual verdict. An operational failure uses `status="error"`, an error kind/stage, and optional pause data; consumers must not count it as a completed UNKNOWN prediction.
- Return explicit unavailability reasons for missing numeric inputs; never substitute guessed operands or zero (`app/verification/financial_calculations.py`, `app/verification/grounded_numeric.py`).
- On document failure, persist error metadata and re-raise (`app/pipelines/document_pipeline.py`).
- Customer-facing job status avoids raw provider bodies; detailed failures must not leak model prompts, paths, or credentials (`app/pilot_task.py`).
- SQLite service operations use the `db()` context manager for commit/rollback/close (`app/pilot/store.py`).

## Logging

**Framework:** Python `logging` plus a JSON lifecycle-event helper in `app/services/observability.py`.

**Patterns:**
- Use `@operation("name")` for request-correlated workflow timing and `event("name", **allowlisted_metadata)` for lifecycle events.
- Use metadata such as document IDs, counts, stage, duration, and error class. Do not add raw claim/PDF/prompt/provider-credential values to events.
- Optional reporter callbacks expose progress via `update(...)` and `log(...)`; customer reporters intentionally suppress arbitrary backend log messages (`app/pilot_task.py`).
- Some legacy code prints progress directly; the central event logger does not control all third-party/legacy output.

## Comments

**When to Comment:**
- Explain financial or security invariants, such as not padding extracted tables, exact source quotes, negative growth baselines, or safe workspace paths (`app/services/document_guards.py`, `app/verification/claim_parser.py`, `app/pilot/store.py`).
- Use module/function docstrings to explain stable boundaries and non-obvious behavior.
- Large commented historical implementations exist in several modules. Treat active code as authoritative; avoid adding more archived implementations inline.

**JSDoc/TSDoc:** Not applicable; this repository's application code is Python.

## Function Design

**Size:**
- Small helpers coexist with large orchestration/UI/extraction modules; no enforced size limit was detected.
- Keep pure financial operations separated from model calls and Streamlit rendering (`app/verification/financial_calculations.py`).

**Parameters:**
- Use explicit keyword options for processing configuration and retain the optional `reporter` protocol (`app/pipelines/document_pipeline.py`).
- Pass `Access` into customer service methods and reauthorize against current membership before reading/writing protected records (`app/pilot/store.py`).

**Return Values:**
- Preserve dictionary schemas consumed across layers. For calculations, retain `value`, `formula`, `inputs`, and `note` (`app/verification/financial_calculations.py`).
- Preserve original and normalized numeric representations and provenance; round for display rather than before arithmetic.
- Pydantic schemas constrain generated responses; validate evidence IDs and verbatim quotes after model output (`app/pipelines/verification_pipeline.py`).

```python
# Existing arithmetic result contract (financial_calculations.py):
{"value": value, "formula": formula, "inputs": inputs, "note": note}
```

## Module Design

**Exports:**
- Use service facade functions for internal dashboard actions (`app/services/application.py`).
- Evaluation service modules explicitly re-export workflow functions (`app/services/evaluation_dataset.py`).
- Keep customer authorization/storage in `app/pilot/store.py`; avoid reading customer paths directly from browser input.
- Use `services.runtime.write()` for atomic JSON persistence, and existing path/ID validation for customer artifacts.

**Barrel Files:**
- No JavaScript-style barrel system. Some Python service modules are deliberate forwarding adapters.
- Backup/update/support copies are not the primary runtime modules; trace imports before editing them (`openrouter_support/`, `day10_update/`, `code_backups/`).

---

*Convention analysis: 2026-10-30*
