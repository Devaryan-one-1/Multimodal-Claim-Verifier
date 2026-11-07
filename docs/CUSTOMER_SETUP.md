# Customer review update

This adds a separate authenticated Streamlit entry point, workspace-scoped storage, durable jobs, review decisions, exports and a focused Reviews / Documents / History flow. It retains the current internal dashboard, extraction, retrieval, numeric verification and evaluation modules.

Optional remote customer storage is described in [SUPABASE_STORAGE.md](SUPABASE_STORAGE.md). Local storage remains the default; cloud migration and hosted acceptance must be run explicitly.

## Install

Stop active evaluations before modifying code. Copy the supplied `app/` files into the matching project paths (merge, do not delete your existing app). These customer files are additions; the public entry point is `app/customer_main.py`. Keep your existing Day 10 updates. Copy requirements-customer.txt, config_examples/ and docs/ to the project root.

```bash
pip install -r requirements-customer.txt
```

Current offline verification and user-reported checks are recorded in [BETA_TESTING.md](BETA_TESTING.md), with remaining work in [BETA_TODO.md](BETA_TODO.md). This is a controlled-pilot implementation; automated service checks alone do not establish production security or financial accuracy.

## Configure identity

The active customer app uses **Supabase email/password authentication**. Follow [SUPABASE_SETUP.md](SUPABASE_SETUP.md) for project URL/publishable-key configuration, confirmation/reset email-code templates and SMTP. Older OIDC/Google examples are not the active login setup. Keep confirmation enabled and use HTTPS for the deployed customer app.

The code uses the verified issuer and subject, checks token expiration and requires a database membership. A signed-in but uninvited account cannot access documents. It displays its account subject for an administrator to grant access. Accounts are owned by the identity provider; this app does not store passwords.

Supabase validates identities; the app stores no account passwords. Each full app verification refreshes the server session and preserves rotated tokens. Lost memberships, invalid sessions and successful password reset clear stale customer state. The five-minute fragment access deadline is independently enforced alongside service-level membership checks.

Authentication alone is not authorization. Every customer service operation checks current workspace membership. Workspace selection is restricted to memberships. IDs are validated, paths are derived on the server, source PDFs are hash-checked, and document records are scoped to the workspace. Customer PDF images and downloads are delivered inline after access checks rather than through shared Streamlit media URLs. Preparing or redisplaying a file requires current access; copies already delivered to a browser cannot be recalled. Restart the customer app after this update to clear any old media-cache URLs.

## Start and grant access

In terminal one, from the project root:

```bash
streamlit run app/customer_main.py
```

Sign in. An uninvited user sees their subject identifier. The server administrator grants a new private workspace:

```bash
python app/pilot_admin.py grant --issuer "YOUR_EXACT_ISSUER" --subject "USER_SUBJECT" --name "Pilot workspace"
```

Refresh the app. To invite a second person to the same workspace, supply the workspace ID printed by the first command:

```bash
python app/pilot_admin.py grant --issuer "YOUR_EXACT_ISSUER" --subject "SECOND_SUBJECT" --workspace "WORKSPACE_ID"
```

Members of one workspace intentionally share its documents/reviews and may delete documents. Use separate workspaces for separate customers. Fine-grained viewer/editor roles are not yet implemented. Revoke access with the matching `revoke` command and --workspace. No customer can grant themselves workspace membership from the UI.

In terminal two, from the project root:

```bash
python app/pilot_worker.py
```

For current customer billing, follow [CUSTOMER_BYOK.md](CUSTOMER_BYOK.md): each user saves their own encrypted OpenRouter key and model choices in the customer app. The worker does not fall back to an operator OpenRouter key. Keep the Google embedding key and `CUSTOMER_KEY_ENCRYPTION_KEY` in the server environment or existing project `.env`; the worker does not load these operator secrets from Streamlit secrets.

## Storage and isolation

`customer_data/control.sqlite3` stores memberships, source versions, jobs and decisions. Each workspace gets a private `customer_data/workspaces/<id>/data/` tree, including its own Chroma database and caches. The public process never changes its working directory. A fresh worker child runs legacy extraction/verification inside its assigned workspace; imports, globals, retrieval caches and relative paths therefore belong to that one job/workspace.

This is application-level isolation, not an OS sandbox for hostile PDFs or compromised dependencies. Run the service with restricted OS permissions; add container/resource isolation and dependency/security review before handling sensitive documents at scale.

One supervisor serializes jobs across workspaces to limit concurrency against shared model quotas. An OS lock prevents a second supervisor on the same filesystem. Do not run the internal evaluation app against the same provider quota during a customer pilot without accounting for the combined usage.

The old `data/` folder is untouched. Existing 3M embeddings, approved evaluation datasets and reports remain internal. They are not automatically copied into customer workspaces. Customers upload their own sources and build private indexes. This avoids accidentally exposing research/evaluation data to a new account. A curated public example can be configured with CUSTOMER_EXAMPLE_PDF; it is explicitly copied and processed into the requesting workspace.

Do not deploy the old `app/main.py`, `ui_preview.py` or `hallucination_preview.py` as public endpoints. Keep the legacy internal evaluation UI on a private machine/network. The customer entry point registers only its authenticated page with st.navigation, excluding legacy auto-discovered pages.

## Jobs and recovery

Closing a tab does not cancel a queued/running job. Reopen Reviews/History; progress updates automatically while the worker is available. Successful subclaims are checkpointed; retries skip them. Source hashes, model configuration and code fingerprint must stay compatible. Code/model changes can block resume; restore the configuration or create a new review. Maximum attempts per job: three. Retries are explicit; the worker does not repeatedly retry failed jobs automatically, although existing provider clients retain their bounded request retries.

Document-processing retries reuse existing caches/index behavior, but do not provide a fully checkpointed state for every extraction stage. Completed document jobs are not automatically rerun. This is a single-host SQLite queue and needs persistent local disk plus a supervised worker service. It is not suitable for ephemeral serverless execution or multiple independent replicas.

On normal shutdown the supervisor terminates its child. After an abrupt crash, stop any remaining worker/task processes before recovering interrupted jobs:

```bash
python app/pilot_admin.py recover-interrupted --workers-stopped
```

Then restart the worker and explicitly resume paused jobs. A manual recovery flag is an operator assertion, not automatic proof that processes have stopped. Pending/running jobs are never silently labeled UNKNOWN.

## Product changes

- Compact review header, deliberate active source and optional additional sources.
- Scope help matches the backend: unnamed claims require one document.
- Durable review list, exception-first filters, reviewed/incorrect-evidence decisions with audit timestamps and stale-write rejection.
- Calculations keep the existing original/normalized values, metadata, precision and contradiction expansion.
- Explicit operational-error UI, actionable unknown explanations, no confidence percentage in the primary display.
- Evidence alongside the PDF, page selection, text search and bounded zoom; supported boxes highlighted.
- Relevant table rows and full-table expansion. The first five rows are a fallback when lexical metric matching fails; advanced year-column slicing is not yet implemented.
- CSV with spreadsheet-formula escaping, escaped readable HTML report (browser Print can save PDF), and advanced JSON export.
- Document context correction is saved as annotation. It does not override verified numbers, source scope or unspecified claim periods.
- Upload auto-queues, duplicate content reuses the existing workspace document, configurable limits remain 10 MB by default.
- Document deletion removes source/index artifacts but retains historical review snapshots and decisions. Define retention/erasure rules before accepting documents that require complete deletion across history and backups.

## Remaining work and release gates

Run the supplied manual two-account checks and unit tests on your machine before a pilot. Validate private inline images/downloads, real Supabase email/session flows, HTTPS, rate/resource limits, durable backups, dependencies and malformed-PDF handling on the intended host. Current offline results and user-reported interface checks are in BETA_TESTING.md; they do not establish all browser, accessibility or deployment gates.

Accuracy is not established by implemented features. Keep your existing reviewed dataset and create a separate human-labelled benchmark on unseen reports. Include wrong years, units/currencies, restatements, segments, missing/conflicting evidence and OCR/chart failures. Measure false support, abstention, completion, latency and cost; never count service failures as correct UNKNOWN outcomes. Provider cost is explicitly unavailable when not supplied; no fabricated cost is shown.

The acquisition recommendations (interviews, pilots, pricing and marketing demonstrations) require actual customer work. This update does not send outreach, claim customer traction, create fabricated benchmark labels or publish a site. Fine-grained roles, full stage-level processing checkpoints and horizontal scaling remain follow-up work. A streaming download API is optional future work for larger files; the current small-upload customer flow uses authorized inline delivery.
