# Customer review update

This adds a separate authenticated Streamlit entry point, workspace-scoped storage, durable jobs, review decisions, exports and a focused Reviews / Documents / History flow. It retains the current internal dashboard, extraction, retrieval, numeric verification and evaluation modules.

## Install

Stop active evaluations before modifying code. Copy the supplied `app/` files into the matching project paths (merge, do not delete your existing app). These customer files are additions; the public entry point is `app/customer_main.py`. Keep your existing Day 10 updates. Copy requirements-customer.txt, config_examples/ and docs/ to the project root.

```bash
pip install -r requirements-customer.txt
```

No application, tests, syntax checks, model calls or security tests were run by the assistant. This is an implementation for a controlled pilot, not a claim that production security or financial accuracy has been established.

## Configure identity

Use an OIDC identity provider such as your organisation's Google, Microsoft, Auth0 or Okta connection. Register an application with its exact callback URL. Copy `config_examples/customer_secrets.toml.example` to `.streamlit/secrets.toml` and fill the actual client ID, client secret, discovery URL, issuer and cookie secret. Merge with existing secrets; do not overwrite unrelated settings. Use HTTPS and the public callback URL when deploying. Do not send credentials through chat.

The code uses the verified issuer and subject, checks token expiration and requires a database membership. A signed-in but uninvited account cannot access documents. It displays its account subject for an administrator to grant access. Accounts are owned by the identity provider; this app does not store passwords.

Official references:
- https://docs.streamlit.io/develop/api-reference/user/st.login
- https://docs.streamlit.io/develop/api-reference/user/st.user
- https://docs.streamlit.io/develop/api-reference/navigation/st.navigation

Authentication alone is not authorization. Every customer service operation checks current workspace membership. Workspace selection is restricted to memberships. IDs are validated, paths are derived on the server, source PDFs are hash-checked, and document records are scoped to the workspace. Download bytes and export content are obtained only after authorization. Native Streamlit media/download URLs must be handled as sensitive links; this is not a separate authenticated file API with per-request revocation. For strict download-session enforcement, add a protected file-serving endpoint before broader production use.

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

Set model API keys and the same model/embedding configuration in the server environment or existing project `.env`; the worker does not load provider keys from Streamlit secrets. Credentials are never accepted from customer browser widgets.

## Storage and isolation

`customer_data/control.sqlite3` stores memberships, source versions, jobs and decisions. Each workspace gets a private `customer_data/workspaces/<id>/data/` tree, including its own Chroma database and caches. The public process never changes its working directory. A fresh worker child runs legacy extraction/verification inside its assigned workspace; imports, globals, retrieval caches and relative paths therefore belong to that one job/workspace.

This is application-level isolation, not an OS sandbox for hostile PDFs or compromised dependencies. Run the service with restricted OS permissions; add container/resource isolation and dependency/security review before handling sensitive documents at scale.

One supervisor serializes jobs across workspaces to limit concurrency against shared model quotas. An OS lock prevents a second supervisor on the same filesystem. Do not run the internal evaluation app against the same provider quota during a customer pilot without accounting for the combined usage.

The old `data/` folder is untouched. Existing 3M embeddings, approved evaluation datasets and reports remain internal. They are not automatically copied into customer workspaces. Customers upload their own sources and build private indexes. This avoids accidentally exposing research/evaluation data to a new account. A curated public example can be configured with CUSTOMER_EXAMPLE_PDF; it is explicitly copied and processed into the requesting workspace.

Do not deploy the old `app/main.py`, `ui_preview.py` or `hallucination_preview.py` as public endpoints. Keep the legacy internal evaluation UI on a private machine/network. The customer entry point registers only its authenticated page with st.navigation, excluding legacy auto-discovered pages.

## Jobs and recovery

Closing a tab does not cancel a queued/running job. Reopen Reviews/History and refresh to see progress. Successful subclaims are checkpointed; retries skip them. Source hashes, model configuration and code fingerprint must stay compatible. Code/model changes can block resume; restore the configuration or create a new review. Maximum attempts per job: three. Retries are explicit; the worker does not repeatedly retry failed jobs automatically, although existing provider clients retain their bounded request retries.

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

Run the supplied manual two-account checks and unit tests on your machine before a pilot. Streamlit media-link access, identity-provider setup, HTTPS, rate/resource limits, durable storage backups, dependency vulnerabilities and malformed-PDF handling need deployment/security validation. No visual/accessibility or runtime testing was performed here.

Accuracy is not established by implemented features. Keep your existing reviewed dataset and create a separate human-labelled benchmark on unseen reports. Include wrong years, units/currencies, restatements, segments, missing/conflicting evidence and OCR/chart failures. Measure false support, abstention, completion, latency and cost; never count service failures as correct UNKNOWN outcomes. Provider cost is explicitly unavailable when not supplied; no fabricated cost is shown.

The acquisition recommendations (interviews, pilots, pricing and marketing demonstrations) require actual customer work. This update does not send outreach, claim customer traction, create fabricated benchmark labels or publish a site. Fine-grained roles, full stage-level processing checkpoints, a dedicated authenticated file API and horizontal scaling remain follow-up work.
