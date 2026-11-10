# Beta information, feedback and monitoring

The customer app shows a beta notice below its title. **Settings → Beta information & feedback** explains supported PDFs, known parser/chart/scanning limitations, private workspace storage, Google/OpenRouter data flow, customer model credits, retention and how to report a problem.

Retention selected by the operator: keep data until deletion is requested; no automatic expiry. Deleting a report queues removal of its source and index. Saved review snapshots (including captured evidence), decisions and history remain until separately removed. Support must handle workspace/account and retained-review removal requests. Backups and external providers have separate retention; do not promise immediate erasure of those copies.

## Feedback contact

The operator chose Discord and requested that the link remain empty until supplied. No invite, server or messages were created. Add a real contact privately to the deployment environment when ready:

```dotenv
BETA_FEEDBACK_URL=https://discord.gg/YOUR_REAL_INVITE
BETA_SUPPORT_EMAIL=
```

An HTTPS feedback form also works. Alternatively leave the URL empty and set a real `BETA_SUPPORT_EMAIL`. Settings shows a native feedback button when a valid contact is configured. With neither configured, local mode tells users to contact their beta inviter; production preflight refuses startup until a contact is present. Do not use the example invite above as a real deployment value.

Use a beta-feedback channel and a private support route for account/data requests. Ask for the request reference, diagnostic code and expected behavior. Never request passwords, email codes, API keys or private PDFs in public Discord channels. Review the invite's expiry/usage settings before sharing it; [Discord's invite guide](https://support.discord.com/hc/en-us/articles/208866998-Invites-101) explains those settings.

## Operator checks during beta

Run from the project environment (or inside the running web container with `python scripts/beta_status.py`):

```powershell
python scripts/beta_status.py
```

This reads aggregate job state and worker heartbeat only. It does not initialize a database, modify jobs, call models or expose document names, claim text, credentials or customer identities. Local SQLite opens read-only; cloud monitoring uses a read-only Postgres transaction.

Exit 0 means no observed warning. Exit 1 means storage could not be read or an attention condition exists: worker heartbeat absent/15 seconds old, queue wait over five minutes, a running job without progress beyond the configured timeout plus 30 seconds, or failed/paused jobs updated in the last 24 hours. Counts are historical state summaries; a warning does not automatically retry work or assert its cause. Inspect the customer diagnostics and worker logs before acting.

Check this report after starting services, after each early beta upload, and periodically while testers are active. Check the host's existing web/worker health status too. Scheduling and delivery of remote alerts depend on the chosen host and are not installed by this change.

Google embedding usage is deliberately reported as **not measured**. Open [Google AI Studio](https://aistudio.google.com/) for the project behind the operator's Google key and check Usage/Rate limits; customer OpenRouter credits do not cover this service. Keep the current free tier unless billing is intentionally enabled. Reuse ready indexes; do not re-upload/rebuild to retry a review. See [embedding budget guidance](EMBEDDING_BUDGET.md) and [Google's rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits). Quota exhaustion must remain a failed/paused execution, excluded from factual accuracy.

## Current Docker security checker

`deploy/test-docker.ps1` pulls the current base image, builds the exact release, audits its installed Python snapshot and scans the resulting local image with Docker Scout (including base OS packages). Both scans must pass. Scanner errors, missing tooling or reported vulnerabilities keep the release result failed; no advisory is ignored or suppressed by the checker. Fixture diagnostics continue after audit failures to preserve independent runtime evidence.

Docker Scout must be installed and able to access its advisory service in the operator's Docker environment. The image report is saved as `image-audit.sarif.json`; `RESULT.json` records separate Python/image scan states. This package still requires real hosted acceptance and cloud backup/restore before release.
