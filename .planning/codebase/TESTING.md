---
last_mapped_commit: 66b401a2bd7fe96b73fb782fe099e7ab3f6b9c0b
last_mapped_at: 2026-10-30
---
# Testing Patterns

**Analysis Date:** 2026-10-30

## Test Framework

**Runner:**
- Python standard-library `unittest` (`tests/test_customer_access.py`).
- No repository test-runner configuration, pytest setup, coverage configuration, or CI workflow was detected.

**Assertion Library:**
- `unittest.TestCase` methods including `assertRaises`, `assertEqual`, `assertTrue`, and `assertIsNone`.

**Run Commands:**
```bash
# From the repository root, with a working Python environment:
python -m unittest discover -s tests -p test_customer_access.py
python -m unittest discover -s tests -p 'test_*.py'
```
- No configured watch or coverage command was detected.

## Test File Organization

**Location:**
- A separate `tests/` directory contains `test_customer_access.py`.
- Root diagnostic/repair scripts and `data/test_results/` are not a configured automated test suite.

**Naming:**
- `CustomerAccessTests(unittest.TestCase)` with ten `test_*` methods.

**Structure:**
```text
tests/
  test_customer_access.py
```

## Test Structure

**Suite Organization:**
```python
# Existing test structure, abbreviated from tests/test_customer_access.py
class CustomerAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": self.temp.name})
        self.env.start()
        store.initialize()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
```

**Patterns:**
- Add `app/` to `sys.path` before importing service modules.
- Use a temporary real SQLite database rather than the application's customer database.
- Seed two principals/workspaces and one source/review to exercise authorization boundaries.
- Denial tests assert exception types; positive state checks inspect persisted decisions and metric counts.

## Mocking

**Framework:** `unittest.mock.patch`.

**Patterns:**
```python
patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": temporary_directory})
```
- Existing tests patch environment configuration, not SQLite service behavior.
- No provider SDK, browser, or network mocks were detected in this suite.

**What to Mock:**
- Follow the existing environment-isolation pattern for customer persistence tests.
- Future model/extraction tests should stub provider boundaries and use synthetic/public fixtures rather than live model quota or customer documents. This is guidance, not existing coverage.

**What NOT to Mock:**
- Preserve real SQL membership/workspace checks in access tests; those checks are the behavior being verified.
- Exercise pure arithmetic and input-validation logic directly in future tests.

## Fixtures and Factories

**Test Data:**
- Inline, synthetic records with 32-character hex workspace/resource IDs (`tests/test_customer_access.py`).
- `store.Access` instances carry principal, workspace, and expiry; one expired instance verifies denial.
- SQL fixtures create members, a ready document record, and a completed review.
- Current access tests do not process actual PDFs, build vectors, or use the historical evaluation corpus.

**Location:**
- Fixtures are built in `setUp()`; no shared `conftest.py` or fixture factory directory was detected.

## Coverage

**Requirements:** No enforced threshold or coverage report was detected.

**View Coverage:** No configured coverage tool/command is present; do not report a coverage percentage from this map.

**Existing service checks cover:**
- Foreign-workspace review access, document download, deletion, and source submission.
- Membership revocation and expired sessions.
- Resource ID and path traversal rejection.
- Stale review-decision rejection.
- CSV spreadsheet-formula neutralization.
- Excluding failed predictions from factual metric denominators.

## Test Types

**Unit Tests:**
- Pure export-cell safety and benchmark metric checks (`app/pilot/exports.py`, `app/pilot/benchmark.py`).

**Integration Tests:**
- Service-level access tests against an isolated SQLite database (`app/pilot/store.py`).

**E2E Tests:**
- No automated browser suite was detected.
- `docs/CUSTOMER_ACCEPTANCE.md` describes manual two-account checks, revocation, recovery, quota handling, source grounding, export review, and visual/accessibility inspection.
- That acceptance document still refers to OIDC. Active sign-in is Supabase; reconcile with `docs/SUPABASE_SETUP.md` before running identity scenarios.

## Common Patterns

**Async Testing:**
- No async test framework is used. The customer application uses a separate subprocess worker, which this suite does not exercise.

**Error Testing:**
```python
# Existing service denial pattern
with self.assertRaises(PermissionError):
    store.get_job(self.b, "d" * 32)
```

**Evaluation:**
- Offline scoring accepts human-reviewed prediction rows and reports completion separately from factual accuracy (`app/pilot/benchmark.py`).
- Internal evaluation workflows are experiments, not substitutes for regression tests (`app/evaluation/`, `README.md`).
- The README explicitly distinguishes historical retrieval metrics from pending/partial verdict and hallucination results; preserve that distinction.

## Verification During Mapping

- The suite and active code were inspected; no tests passed or failed during this map because the Python runtime could not launch.
- Attempting `../venv/Scripts/python.exe` failed because its configured base interpreter was inaccessible to the session account. No `python`, `python3`, or `py` command was found on PATH.
- Runtime/AST validation and live provider/browser checks therefore remain unperformed.
- The current suite does not cover active hybrid retrieval, numeric grounding, or extraction. Source-derived concerns in those domains are listed in `CONCERNS.md` without claiming successful reproductions.

---

*Testing analysis: 2026-10-30*
