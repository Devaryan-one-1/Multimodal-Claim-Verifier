# Manual acceptance checks

Run these yourself after configuring OIDC and starting both customer UI and worker. The assistant has not run them.

## Before inviting a pilot customer

1. Keep the original internal dashboard private. Serve only app/customer_main.py publicly, with HTTPS. Verify legacy pages are not reachable through its navigation or direct page paths.
2. Create two OIDC accounts, A and B. Grant each a distinct workspace. Confirm a signed-in uninvited account sees no source names, review history or API settings.
3. Upload a public PDF as A; verify it is absent for B. Test the supplied service-level denial tests for B using A's document/job IDs. Check download, deletion, review, submission and decision access.
4. Revoke A's membership while A has a tab open. Refresh and attempt another action; access must fail. Check token-expiry behavior. Separately test native Streamlit media/download-link access: strict revocation on every file request is a known follow-up requirement, not established by these service checks.
5. Confirm customer settings never expose provider keys/model configuration. Restrict server-shell access to administrators.
6. Submit a review, close the browser tab, reopen it and inspect the saved job. Confirm decisions, source hashes and exports persist across UI restart with the worker running.
7. Exercise quota failure: failed execution must appear as failed/paused, not as a successful UNKNOWN. Retry explicitly, with at most three attempts; previously completed atoms must not be regenerated. Observe provider billing separately.
8. Stop the supervisor normally during a job; confirm it terminates the child and marks the job paused. Test restart and explicit recovery on public fixtures. Do not recover while an old child is still running.
9. Verify one supported, contradicted, unknown and compound claim using manually reviewed source pages. Confirm periods/units/company scope, calculations and correct citations. Check conflicting sources and a zero baseline.
10. Switch between workspaces and accounts. Check widgets, selected citations, review lists and searches do not retain another workspace's source content.
11. Inspect CSV and readable HTML exports. Source values, calculations, source versions, decisions and run completion should agree with the UI. Test a claim starting with '=' to ensure CSV spreadsheet formulas are escaped.
12. Inspect desktop/narrow layouts, keyboard navigation, focus visibility, contrast and evidence highlights. Search native PDF text; scanned pages may have no searchable native text. Confirm highlighting is withheld for unsupported coordinates.
13. Use two reviewers in one shared workspace. Save a decision from A, then attempt to save B's stale version; B must be asked to refresh. Historical decision events are retained.
14. Queue deletion only after active workspace jobs complete. Confirm source/index artifacts disappear, saved review snapshots remain, and View Page reports source unavailability. This is not complete erasure of historical evidence snapshots/backups.
15. Validate persistent disk backups and recovery, disk quotas, file/media access, PDF parser resource limits and dependency security before using sensitive customer reports. OS/container sandboxing and a dedicated authorized download API are outside this implementation.

## Supplied service-level tests

```bash
python -m unittest discover -s tests -p test_customer_access.py
```

They use an isolated temporary SQLite database, make no model calls and do not touch the existing reports or vectors. They cover workspace denial, revocation, expired access, traversal, stale review decisions, CSV formula escaping and exclusion of failed predictions from metric denominators. They do not test OIDC login, real PDFs, provider availability or browser rendering.

## Held-out accuracy benchmark

Create a separate JSONL file from human-reviewed claims on previously unseen reports. Do not overwrite data/evaluation/claims.jsonl. Each prediction row uses:

```json
{"status":"complete","gold":"CONTRADICTED","predicted":"SUPPORTED","latency_seconds":12.4,"cost_usd":null}
```

This is a schema example, not an actual measured result. For service failures use status='failed'; they are counted in completion coverage but excluded from verdict metrics. Provide cost only when measured, never infer zero from missing usage.

```bash
python app/pilot/benchmark.py path/to/held_out_predictions.jsonl
```

The script reports four-class precision/recall/F1, macro F1, false-supported count, false-support rate among non-supported gold, false-discovery rate among supported predictions, abstention, completion, median/P95 latency and observed cost with measurement coverage. Inspect denominators, keep errors separate and preserve model/code/document versions with the dataset.
