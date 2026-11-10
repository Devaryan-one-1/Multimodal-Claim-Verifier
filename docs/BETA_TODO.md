# Customer beta work list

10 November customer-image hardening is implemented after approval: pip is removed
from the customer filesystem after installation/model setup, system-tree setuid/
setgid bits are cleared, and an exact metadata snapshot is regenerated afterward.
The audit-only target uses its own virtual environment; local/migration images
retain their installer. Native guards reject leftover installer modules/commands,
privilege bits or a stale snapshot. Eight new hardening tests and the 12 deployment
tests pass locally. A new GitHub build/native/scan run is pending. The last verified
hosted run passed 465 container tests, with 204 reported image entries (zero
critical, 48 high, 75 medium, 66 low and 15 unscored OpenSSL entries). The latter
are five newly published advisories rated Low upstream. No findings are waived.

10 November diagnostic follow-up: latest hosted run passes 458 container tests and
runtime/restore checks. Image findings fell from 341 to 189, with zero critical
and 48 high entries (12 distinct high issues). The approved CI-only follow-up adds
full Trivy JSON and a read-only runtime inventory to identify component paths,
bundled code and affected features. Seven inventory tests, 12 deployment tests
and scanner failure/conversion/capture simulations pass locally. Fresh diagnostic
artifacts are pending a user push. No ignore/VEX or release-policy change is made.

10 November hosted result: all runtime checks and **458 selected container tests**
pass, including native UI, persistence and fixture backup/restore. Python dependency
audit passes. The image scan reports 341 findings: 335 Debian OS package entries
and six pip 25.0.1 entries. These are scanner findings, not proof that every issue
is exploitable. The user-approved packaging remediation switches to Python 3.12
on Debian Trixie, installs available OS updates and pins pip 26.3 before dependency
installation. Runtime audit snapshots now include pip, and native smoke checks
verify its inclusion. Fresh hosted build/scan results are pending; no security
finding was ignored or waived. Application behavior and saved indexes are unchanged.

10 November: a GitHub-hosted **Docker checks** workflow is prepared to avoid the
PC's C: disk exhaustion. It uses the same fixture runner with a public pinned
Trivy image scanner instead of Scout, without real secrets or deployment. It is
now pushed and run as recorded above. The earlier local cloud build, pip audit and native cloud smoke
passed; Scout failed to copy the image because C: was full, and the local fixture
build then failed on Docker's read-only storage. Follow
[GitHub steps](GITHUB_DOCKER_CHECKS.md); the release gate remains open.

9 November personal Vercel preparation: **570 offline tests pass, zero skips**.
Cloud/local image targets are separated,
the customer cloud requirements omit Chroma, root Vercel configuration leaves the
worker at zero, and a 4 MB cloud upload limit is enforced. No project, secrets,
Auth settings or deployment were changed remotely. New native images/security
scans, a real feedback contact, hosted reconnect/privacy checks and cloud
backup/restore remain open. See [deployment steps](DEPLOY_VERCEL.md) and the
[preparation evidence](../.planning/reviews/2026-11-09-vercel-preparation/REVIEW.md).

Approved parser-integrity follow-up: **558 offline tests pass** after correcting PG-01 direction flags, PG-02 distinct repeated-value assertion coverage and PG-03 original date/amount associations. Nineteen new regression methods cover the reproduced bugs and shared-period/from-to/respective/quarter/threshold/segment controls. The three findings are corrected in source and offline regressions; fresh real-model acceptance and a rebuilt final Docker image remain pending. Restart app/worker and submit a new review using the existing ready PDF. See [fix and verification](../.planning/reviews/2026-11-09-claim-pipeline/ASSERTION_BINDING_FIX.md). Older references below to the three open findings describe earlier assessments.

Deferred at the operator's request: make the customer Docker release Supabase/pgvector-only and exclude Chroma. Revisit under deployment/security items 10 and 12 before customer release; the current Chroma findings are not cleared by this deferral. Keep Chroma and existing indexes in the local/internal and migration environments. Implementation must split cloud versus local dependencies, remove Chroma-specific release imports/smoke checks, enforce the cloud backend for that image, rebuild a clean customer image and verify Chroma is absent (including transitive dependencies). Then rerun dependency/image scans and cloud retrieval/citation checks using existing migrated vectors. No Chroma removal, index rebuild or data deletion was performed for this deferred request.

Confirmed newer operator run `docker-20261109T204349001Z`: **all isolated runtime/native/UI/regression/health/persistence/cold-restore checks passed**, including the corrected schema packaging. Python dependency audit still finds four Chroma 1.5.9 advisories. Docker Scout again exited with a sign-in requirement and produced no image/OS report. Overall release result remains failed solely at the two security gates (`failure=null`, `runtime_checks_completed=true`). This local fixture restore does not replace the pending real cloud backup/restore and hosted acceptance.

Latest operator run `docker-20261109T203629227Z`: current release build, native encryption/PDF/vector/model smoke and real Streamlit fixture checks passed. The run stopped on two schema-contract tests because the SQL file was excluded from the image; explicit SQL-only Docker COPY/allowlist entries now correct that packaging omission, pending native rerun. Python audit confirms four Chroma findings. Docker Scout could not scan because the operator is not signed in; no image/OS scan result exists. Fixture service/persistence/restore checks were not reached in this run. Sign in to Docker Desktop and rerun after the packaging fix; keep security findings open.

9 November beta information/security follow-up: **539 offline tests pass**. Settings now has Beta information & feedback, with supported-input limitations, provider/data flow, customer billing, operator-approved keep-until-deletion retention, retained review snapshots and private-support guidance. The operator selected Discord but asked to leave the URL empty until supplied; production preflight requires a valid contact before launch. `scripts/beta_status.py` reports read-only worker/queue/failure summaries; Google usage remains a manual AI Studio check. Docker checker now pulls the base image and includes a required Docker Scout image/OS scan alongside its Python audit. A fresh Docker run could not execute here: the engine became available after the operator started Desktop, but Windows denied access; this shell also blocks PowerShell script execution. Current primary advisory records still flag the pinned Chroma package; no security waiver or successful fresh image audit is recorded. See [beta operations](BETA_INFORMATION.md) and the [follow-up record](../.planning/reviews/2026-11-09-beta-launch/REVIEW.md). The three correctness findings in the separate [project audit](../.planning/reviews/2026-11-09-project-gaps/REVIEW.md) remain open.

9 November claim-pipeline fixes: after the audit and user approval, added parser-only reasoning control for the selected Nemotron endpoint, metric/quantity binding and compound quantity/qualifier coverage, safe parser-validation details, asset-share ratios and operational numeric error handling. The user's next run finished within budget but failed period validation. Added explicit-input shared-date repair, local from/to ratio splitting and precise period-rule diagnostics; **522 offline tests pass**, including seven compact model-response shapes and real table arithmetic. Restart the customer app/worker and submit a new review against the existing ready PDF; live completion/citations remain to be confirmed. Saved reviews were not rewritten and no documents/indexes were rebuilt. See [period follow-up](../.planning/reviews/2026-11-09-claim-pipeline/PERIOD_FOLLOWUP.md), [fix record](../.planning/reviews/2026-11-09-claim-pipeline/FIX_SUMMARY.md) and the historical [pipeline audit](../.planning/reviews/2026-11-09-claim-pipeline/REPORT.md).

Keep these item numbers stable. Items 1, 2, 4, 5 and 9 are user-reported working; 7 and 8 now have explicit user-confirmed live acceptance too. Preparation and scoped fixes for 3, 10, 11 and 12 are recorded below. Distinguish offline verification, user-reported behavior and individually logged browser assertions.

Update: the user confirmed account separation including same-window identity switching, confirmation/reset email delivery and old/new password behavior. Items 3 and 6 are in the current combined live check using the existing Apple report; use [CUSTOMER_QUICK_CHECK.md](CUSTOMER_QUICK_CHECK.md). Remaining rows retain their recorded status.

| # | Work | Status / completion condition |
|---|---|---|
| 1 | Live loading | Implemented, offline checked and user-reported working. Two-second updates, actual counts, ready sources/results and connection changes refresh automatically. An unavailable service shows waiting cards and heartbeat age. |
| 2 | Draft preservation | Implemented, offline checked and user-reported working. Claims, review notes and model choices retain a separate draft copy within the current session/workspace after leaving the field. Clear/discard actions are explicit. Drafts do not survive logout or a new session. |
| 3 | Results and citations | Agreed live claim test set passes: supported/contradicted tables, blank-cell UNKNOWN, chart counts/growth, completed future-year UNKNOWN, two retained compound atoms S/C, and Apple nine-month compensation SUPPORTED. Real saved records confirm the final cases. Apple's page-8 alternate table citation is source-checked and valid; page 14 need not be forced. Numerical CSV and earlier prose/inventory checks pass. Full suite 416/416, zero skips. Hosted/native acceptance remains under deployment. See [evidence](../.planning/reviews/2026-11-05-beta-315/REVIEW.md). |
| 4 | Failures and retry | Actionable diagnostics and latest-attempt display are implemented; source-validation failures now have their own safe messages. All 24 diagnostics/retry regressions pass; full suite 416/416, zero skips. Actual quota/truncation/source-validation attempts remain paused without factual atoms; failed queries are excluded from accuracy offline. Fresh requests recover after the approved fixes. Explicit same-job retry/latest-attempt browser confirmation remains open. See [final check](BETA_315_CHECK.md). |
| 5 | Keys and models | Implemented, offline checked and user-reported working. Model edits survive validation errors; saved models, request models and allowlisted observed request details are visible. Existing key-removal/ownership checks remain covered. |
| 6 | History and exports | User confirmed History reopening works after the selection fix. Both selectors show UTC timestamps, claim previews and short references. Three new regressions pass; full offline suite: 401 run, 394 passed, seven existing skips. The user-supplied numerical CSV passes 14 read-only checks against the saved SUPPORTED result, calculation, page-6 citation, decision and note. The expanded native Streamlit/Docker fixture remains pending under deployment. See [History fix](../.planning/reviews/2026-11-05-history-selection/REVIEW.md). |
| 7 | Account separation | Implemented, regression checked and user-confirmed: B cannot see A's documents, reviews or prepared downloads in separate sessions or after switching from A to B in the same window. Scoped/revoked/stale-callback guards pass locally. New native fixture run and hosted privacy checks remain tracked under deployment. |
| 8 | Account recovery | Implemented, regression checked and user-confirmed: confirmation/reset emails arrive, old password fails after reset and new password signs in. Invalid/replayed recovery and session protections pass locally. No real password/account changes made by the agent. |
| 9 | Layout and keyboard use | Implemented using GSD and user-reported working: 375px iPhone X content minimum; phone bottom Reviews/Documents/History; top-right Settings on phone/desktop; full-width phone actions below forms; PDF below the claim on phones, with zoom/pan confined to the preview. Ten new authorization/navigation/private-preview checks pass; full suite 283 run, 276 passed, 7 existing encryption skips. Detailed keyboard/theme/exact-viewport and new native Streamlit/Docker checks remain open. See [CUSTOMER_LAYOUT.md](CUSTOMER_LAYOUT.md). |
| 10 | Deployment | Supabase migration committed by operator report: 4 documents, 177 artifacts and 1,350 vectors, without embedding calls/source changes; live storage isolation/locking smoke passed. Personal Vercel files now prepared in Dublin with zero workers, a Chroma-free cloud image and 4 MB upload cap. New Docker builds/scans remain pending because this session is denied engine access. Remote setup, Production secrets/Auth URLs, separately approved activation and hosted reconnect/PDF/privacy acceptance remain open. Earlier native runtime/restore results predate this packaging change. See [Vercel steps](DEPLOY_VERCEL.md). |
| 11 | Backups and restore | Real 1,782-file cold restore and Docker fixture restore passed. Local archive/key transfer and schedule templates are prepared, not installed. Supabase cloud mode needs Postgres plus private-object export and an isolated restore; the local backup helper refuses cloud mode so a cache copy cannot be mistaken for a backup. Preserve the pre-migration archive/master key. Actual remote backup destination, schedule, retention and notification setup remain open. |
| 12 | Deployment safety checks | Prior native build passed imports/fixtures but failed on four Chroma findings and an unavailable Scout scan. Chroma is now excluded from the cloud release dependency branch; fresh dependency/OS scans and native confirmation are required, with no waivers. Local fixtures retain Chroma in a separate image. Cloud-only startup/upload guards pass offline. Actual-host reconnect/media/privacy and representative processing/load checks remain pending. |
| 13 | Google embedding budget | User confirms the Google project is on Free. Monitor embedding usage/quota during beta; paid cap/budget setup is deferred until billing is enabled. [Conditional setup steps](EMBEDDING_BUDGET.md) remain available. Existing pacing is a throughput limit; customer OpenRouter keys do not cover Google embeddings. No billing/quota/environment setting was changed. |
| 14 | Beta information and feedback | Implemented and regression checked: Settings disclosure, approved retention/deletion wording, provider/credit information and configurable feedback button. Discord link intentionally empty at the operator's request; add it before production (preflight enforces a valid contact). Read-only worker/job status tool prepared; Google quota checks are manual and remote alerts remain host-dependent. See [beta information](BETA_INFORMATION.md). |
| 15 | Table embeddings and old table artifacts | Agreed table/chart verdict checks pass by user report and saved-result inspection. Shared View Page fix passes user acceptance and the 7 November native Streamlit fixture. Broader real-chart coverage and new-backend hosted acceptance remain separate. The optional Supabase migration reuses saved vectors, without rebuilding PDFs/indexes. See [acceptance/fix record](../.planning/reviews/2026-11-05-beta-315/REVIEW.md). |

8 November UI follow-up: approved small-fix items 1, 2, 4 and 5 are implemented: durable workspace selection and visible workspace label, distinct PDF/citation references, task-specific failure advice and safe request token details. All 498 offline tests pass; the user reports the interface update works. Individual live assertions and an independent browser pass were not logged. Small-fix items 3 (empty-screen redesign), 6 (key-removal confirmation) and complex-claim token-budget tuning remain deferred. These small-fix numbers are separate from the stable beta rows above. See [review and acceptance steps](../.planning/reviews/2026-11-08-customer-selection/REVIEW.md).

Use [BETA_TESTING.md](BETA_TESTING.md) for detailed hands-on steps and [CUSTOMER_BYOK.md](CUSTOMER_BYOK.md) for key ownership and billing behavior. Mark browser checks pass/fail/not run with evidence; do not infer passes from unit-test counts.

For the 2026-11-02 work on 3, 10, 11 and 12, see [DEPLOY_BETA.md](DEPLOY_BETA.md) and the [evidence/release review](../.planning/reviews/2026-11-02-beta-launch/REVIEW.md). GSD skill used: `gsd-debug` (inline); PDF source inspection also used the separate PDF skill. Host preparation was selected by the user; nothing is publicly deployed.

For the 2026-11-03 work on 6, 7, 8 and 11, see the [customer acceptance record](../.planning/reviews/2026-11-03-customer-acceptance/REVIEW.md) and [BACKUP_OPERATIONS.md](BACKUP_OPERATIONS.md). GSD skill used: `gsd-debug` inline. Native browser automation could not initialize, so user-reported live checks and operator-run Docker results are recorded separately from local regressions.

On 2026-11-02 the service warning was checked against the actual control database: the latest heartbeat was about 25 minutes old, with two queued requests and no job marked running. The operator must run one `python app/pilot_worker.py` process in the configured project environment alongside the customer app. Starting the dashboard alone does not start its worker. The UI should show service recovery automatically once heartbeats resume.

A subsequent read-only check confirmed recovery: the heartbeat was 1.8 seconds old, one request was running and one remained queued. This establishes worker availability at that check; it does not establish the requests' final verdicts or the browser acceptance checks.

The user confirmed that starting the worker was the solution and processing now works. The service-availability incident is resolved; keep the customer app and one worker running together. Other browser acceptance items remain pending until individually checked.
