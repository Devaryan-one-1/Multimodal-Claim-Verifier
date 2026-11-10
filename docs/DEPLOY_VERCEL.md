# Personal Vercel trial

Prepared 9 November 2026. Nothing has been linked, pushed, deployed or activated.
Use this for a personal, non-commercial trial on Hobby. Commercial use needs an
eligible plan/host ([Hobby terms](https://vercel.com/docs/plans/hobby)).

## Prepared configuration

- Root `vercel.json` selects the container runtime in Dublin (`dub1`), near the
  existing Ireland Supabase project. Its worker starts with **zero instances**.
- The final Docker `release` target uses Supabase/Postgres vectors and excludes
  Chroma. `local-release` retains Chroma for the local Compose/migration drills.
  No saved index, migrated vector, PDF or encryption key needs rebuilding.
- The customer filesystem also removes runtime pip and strips setuid/setgid bits
  under `/usr`. Audit tooling is isolated in its own build-target environment.
  Fresh hosted build/native/security results are required for this hardening;
  prior passing runtime tests predate it. Existing image findings remain open.
- Cloud startup refuses local storage and requires production checks. Writable
  caches/logs use `/tmp`; durable documents, jobs, reviews and vectors stay in
  Supabase. Never treat `/tmp` as a backup.
- Vercel uploads are limited to **4 MB per PDF** in Streamlit and the store. The
  host request limit is 4.5 MB, so this leaves room for multipart overhead. Existing
  larger saved documents remain usable. Local Compose keeps its 10 MB limit.
- `.vercelignore` and `.dockerignore` allow only required source/configuration
  files. Private environment files, customer data and internal dashboard are
  excluded. Real keys belong in private host settings.

## 1. Finish the local release gate

If the PC lacks disk space, use [GitHub Docker checks](GITHUB_DOCKER_CHECKS.md).
That workflow runs the same fixture helper on hosted Linux with Trivy replacing
Docker Scout, without a Docker login. Only a completed passing hosted run clears
the build/test/scan gate; the workflow is prepared locally and has not run yet.

Run from your normal PowerShell, with Docker Desktop running:

```powershell
Set-Location -LiteralPath 'F:\Multi Modal Change Detector\multimodal-claim-verifier'
& .\deploy\test-docker.ps1
```

The checker builds/scans the Chroma-free cloud image, proves Chroma is absent,
runs native PDF/crypto/reranker checks and tests cloud adapter dispatch with the
database boundary mocked. A separate local image runs the existing native Chroma,
UI, persistence and cold backup/restore fixtures. It never mounts real customer
data or calls model providers. Security failures keep the overall result failed.
The local fixture image still contains Chroma and is not the customer release.

Keep `RESULT.json`, both image IDs, logs and the cloud image's runtime dependency
snapshot under the generated `.planning/reviews/.../docker-<timestamp>/` directory.
Docker engine access is denied in this managed agent session, so the newly
prepared images and their fresh audits have **not** been verified here.

## 2. Prepare private values; approve remote setup

Review [the environment reference](../config_examples/vercel.env.example).
Retain the already migrated Supabase schema/bucket/data and **existing encryption
master key**. Use the session pooler on port 5432 with TLS, not transaction pooling.
Do not rerun migration, SQL setup or embeddings for this deployment.

Before remote setup, choose a project name and supply a working Discord invite or
support email. The contact remains empty by your request; production preflight
will refuse startup until a real contact is configured.

Creating/linking the Vercel project, uploading secrets, changing Supabase Auth
URLs, pushing Git commits and deploying still require your separate approval.
After approval:

1. Import `Devaryan-one-1/Multimodal-Claim-Verifier` at the repository root. The
   root contains `Dockerfile` and `vercel.json`; do not add another project folder
   prefix. Review the exact tested source commit before publishing it.
2. Use the **Container** framework preset. Add the reference environment values
   privately in **Production** scope, replacing placeholders. Set `PORT=8501` in
   Vercel project settings explicitly; it controls container request routing.
   Set the function maximum duration to 300 seconds for the Hobby trial.
3. Keep `SUPABASE_ENABLE_SIGNUP=false` for existing test accounts initially.
   Keep production secrets out of Preview scope. The worker only exists in
   production, and previews must not use the real production database/keys.
4. Set `SUPABASE_SITE_URL` to the actual stable `https://<project>.vercel.app`
   address. In Supabase Auth, set the matching Site URL and allowed confirmation/
   reset redirect URL. This app uses codes entered in the app; no new callback
   endpoint is needed. Preserve local redirect entries if still used for testing.
5. Keep the checked-in worker count at zero for the first deployment. Confirm web
   health, login, workspace, existing documents and History before activation.
   A stopped-worker warning and saved queued jobs are expected at this stage.

## 3. Activate one worker after approval

Stop any local worker connected to the same Supabase database. Approve changing
`daemons.worker.instances.dub1` from `0` to `1`, then deploy that reviewed change.
The command already runs `python deploy/entrypoint.py worker`. This starts the
existing supervisor, lock, heartbeat and child-task protections. Do not launch
multiple workers or background a worker inside the web process.

For later worker upgrades, let current work finish, set the worker to zero and
wait for shutdown before activating the replacement. The exclusive database lock
prevents simultaneous ownership. Check for a fresh heartbeat after activation;
do not rely on the web health endpoint to prove the worker is processing.

Hobby currently includes 5 active CPU-hours and 420 GB-hours of daemon memory per
month. A 2 GiB worker consumes the memory allowance in about nine continuous days,
including idle time. Vercel stops it when either quota is exhausted. Turn it off
when personal testing ends; this configuration does not provide a month of free
24/7 processing. Monitor both worker and web usage in the dashboard.

## 4. Hosted acceptance before sharing

Use the existing Apple and fixture documents rather than re-uploading them:

1. Login to each disposable account. Check workspace selection, document list and
   History, and confirm account B cannot see A's sources, results or downloads.
2. Submit a known supported claim. Verify the worker heartbeat, live progress,
   cited table/chart page, source values, formula and exported CSV. Save a decision
   and reopen the exact review from History.
3. Check confirmation/reset email delivery against the hosted Site URL. Enter
   credentials/codes only in the app.
4. Check a failure/retry: it must remain operationally failed/paused, never become
   a completed UNKNOWN factual verdict, and show only the latest attempt.
5. Upload one small fixture PDF, then try a file over 4 MB and confirm rejection.
   Check mobile navigation at 375 px and document/PDF preview controls.
6. Leave a tab open for more than five minutes and reconnect/reload it. Reopen a
   saved review and test PDF images, page navigation and downloads again.
   Streamlit session state and media are held in memory; a reconnect may reach a
   different instance and lose login/session drafts even though saved records
   remain in Supabase. Verify this on the real host before inviting users. If
   continuity fails, use an always-on Docker host rather than announcing readiness.

Vercel WebSockets are supported, but close at the function duration limit; Hobby
allows at most 300 seconds. Passing offline tests cannot prove hosted routing,
media/reconnection behavior or platform resource compatibility.

## 5. Recovery and stop

To stop processing, set the worker count to zero. The worker handles SIGTERM and
pauses active work for an explicit retry. Saved cloud jobs remain in Supabase.
Rollback to the reviewed source/image; preserve the database, private bucket,
embedding settings and encryption master key.

The existing local backup/restore fixture is **not** a Supabase backup. Before
broader beta use, configure a Postgres plus private-object backup destination,
schedule and isolated restore drill. Preserve the verified pre-migration archive
and separate key backup until that cloud recovery check passes. See
[the beta list](BETA_TODO.md) for other pending live/provider acceptance.

Official references checked 9 November 2026: [container runtime and port](https://vercel.com/docs/functions/runtimes/container),
[daemon configuration](https://vercel.com/docs/daemons/config-reference),
[daemon quotas](https://vercel.com/docs/daemons/limits),
[function limits](https://vercel.com/docs/functions/limitations),
[WebSockets](https://vercel.com/docs/functions/websockets),
[regions](https://vercel.com/docs/regions).
