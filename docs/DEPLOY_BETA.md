# Single-host customer beta

7 November update: the operator's latest pre-migration Docker runtime/native UI/persistence/restore checks passed, while its audit still reports four Chroma findings. An optional [Supabase storage backend](SUPABASE_STORAGE.md) and inactive Vercel template are now prepared; they require a new image build and real cloud/hosted acceptance. The single-host instructions below continue to describe local persistent storage.

Prepared on 2 November, updated 3 November 2026. Hosting is not chosen and nothing has been published. Operator run `docker-20261103T191122262Z` successfully built the updated image, passed native PDF/PNG/encryption/vector and offline non-root model smoke checks, ran 43 container regressions without skips, started healthy web/worker services, preserved fixture data through container recreation and verified a cold backup/isolated restore. Its Python audit still fails on four Chroma 1.5.9 advisories (down from 72 entries across four packages). No findings are ignored or waived. See [DOCKER-RUNTIME-RESULT.json](../.planning/reviews/2026-11-02-beta-launch/DOCKER-RUNTIME-RESULT.json). This agent session still receives Docker pipe access denial. Real customer/browser, host/HTTPS, OS audit and Chroma applicability/remediation checks remain open.

The public entry point is `app/customer_main.py`. Keep `app/main.py` in the local internal environment. The release image omits that dashboard and its legacy pages. Run one web container and exactly one worker on one persistent host. Do not scale replicas or share this SQLite/Chroma volume between hosts.

Later customer acceptance changes for items 6, 7, 8 and 11 have local regression coverage but need the expanded Docker run; the earlier 43-test image result does not validate newly changed source. See [BETA_TESTING.md](BETA_TESTING.md) and [BACKUP_OPERATIONS.md](BACKUP_OPERATIONS.md). No schedule/storage/notification has been installed: the user selected preparation until the host/destination is chosen.

## 1. Private configuration

From the project root, copy `deploy/env.example` to `deploy/.env` and edit it locally. That file is ignored by Git and excluded from the build context. Restrict host access to it; Docker operators can inspect container environment variables, so shell/Docker access is privileged.

Use your existing Supabase project/publishable key, operator Google embedding key, and **the existing `CUSTOMER_KEY_ENCRYPTION_KEY`**. Never generate a replacement master key for saved connections. Customers supply OpenRouter keys in their authenticated accounts; do not add an operator OpenRouter key. Do not paste secrets into commands, tickets or screenshots.

Set a unique `BETA_IMAGE` tag for the build. For local testing use `BETA_PRODUCTION=0`, `BETA_HOST=localhost` and the loopback Site URL. Before public release set `BETA_PRODUCTION=1` and the actual public HTTPS `SUPABASE_SITE_URL`; configure the matching allowed Site/redirect URLs in Supabase using [SUPABASE_SETUP.md](SUPABASE_SETUP.md). Keep an invite-only beta until the real email and two-account checks pass.

The checked-in settings preserve `gemini-embedding-001` and 768 dimensions. Match the original settings when migrating an index; preflight refuses mismatches rather than rebuilding it. The internal 3M corpus stays outside this customer image and migration.

## 2. Build and audit when Docker is running

For a single-command isolated Windows check, run `& .\deploy\test-docker.ps1` from the project root in your normal PowerShell. It builds the release and Python audit targets, checks native encryption/customer imports and PDF/PNG/vector operations, runs connection and backup regressions inside the image, starts a separate fixture stack on a free loopback port, verifies health and persistence through recreation, then performs a cold backup/isolated restore. It also tests cached reranker inference as non-root with network disabled. It stops its fixture services afterward and retains their volumes/images for inspection. Logs and `RESULT.json` go under `.planning/reviews/2026-11-02-beta-launch/docker-<timestamp>/`. It uses generated test configuration; real customer data and credentials are not mounted. This is fixture acceptance, followed separately by the real configured customer/browser checks.

The updated helper records an audit failure and continues **fixture diagnostics only**, so unresolved advisories do not prevent collecting independent runtime evidence. The audit is refreshed even when runtime layers are cached. A failed audit still makes the final result/exit fail; no advisory is ignored or waived. Check `python_dependency_audit`, `audit_failure` and `runtime_checks_completed` in the result, not just whether the script reached the end. Logs now use consistent UTF-8 under both Windows PowerShell and newer PowerShell.

This agent session receives **permission denied** for both Docker engine pipes and the Docker context directory. No Docker access rules, ACLs or privileges were changed. The operator's saved run confirms Docker engine/build access in their normal terminal. Run the helper there because this managed session cannot access the engine.

The 3 November updates are Streamlit 1.66.1, Pillow 13.0.0, pypdf 6.21.4 and setuptools 84.1.0. Their updated build/native smoke checks passed, and these packages no longer appear in the latest audit findings. Streamlit 1.64.0 explicitly caps Pillow below 13, so its update was required for Pillow's published fixes. Chroma remains 1.5.9 to preserve the existing index/version behavior; no published patched release was identified. Its four reported advisories concern server/configuration/auth paths. The app uses embedded `PersistentClient` and starts no Chroma HTTP server, but this observation has **not** been turned into an audit waiver or a claim that all risks are resolved. See [AUDIT-FOLLOWUP.json](../.planning/reviews/2026-11-02-beta-launch/AUDIT-FOLLOWUP.json) for the first scan and [DOCKER-RUNTIME-RESULT.json](../.planning/reviews/2026-11-02-beta-launch/DOCKER-RUNTIME-RESULT.json) for the verified rerun.

PowerShell, project root:

```powershell
$env:BETA_ENV_FILE = '.env'
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml build web
docker build --target security-audit -t claim-verifier:dependency-audit .
```

Stop if any command fails. The build checks imports, `pip check` and a public dummy-text prediction with the pinned CPU reranker. It bakes that model into the image for offline runtime loading. The separate audit stage scans the exact runtime package snapshot captured before installing audit tooling. Fix reported vulnerabilities; an unavailable scanner is an unpassed gate. Also scan the OS/base image using the eventual host's image scanner and apply its relevant fixes.

`requirements-production.txt` pins direct dependencies from the working local environment; its Linux/Python 3.12 compatibility remains unproved until the build succeeds. Transitive resolution and base-image tags are not a complete source lock. After a successful build, record the image ID and `/app/deploy/installed-requirements.txt`, and retain/export that exact image for release and rollback. Deploy the tested image without rebuilding it between acceptance and launch.

## 3. Optional migration of the verified backup

Your verified archive is `F:\Multi Modal Change Detector\backups\beta-release-20261102T185422Z`. Its isolated restore is `F:\Multi Modal Change Detector\backups\restore-drill-20261102T185422Z`. Both contain 1,782 verified files; the restored control database was logically identical. The matching master key was protected and preserved separately under the workspace backup secrets directory. Keep it separate when copying the backup off-host.

To migrate customer state, transfer the archive securely to the host and mount it read-only. With services stopped and the preserved key configured in `deploy/.env`:

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps --volume '/absolute/host/backup:/incoming:ro' backup restore /incoming /var/lib/verifier/recovered --customer-only
```

Replace the host path. The restore checks hashes, SQLite integrity and the key fingerprint, requires a new destination, and excludes the internal corpus with `--customer-only`. Then set `CUSTOMER_DATA_ROOT=/var/lib/verifier/recovered/customer_data` in the private environment file before startup. A fresh installation uses the default `/var/lib/verifier/customer_data`. Do not point a fresh installation at an unverified or incomplete copy.

## 4. Local container acceptance

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d web worker
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail 80 web worker
```

Open `http://localhost:8501`. Only loopback is bound by default. Both services must become healthy; the UI must report the worker available. Preflight checks storage permission/free space, key decryption and saved index compatibility without provider calls.

Use [BETA_TESTING.md](BETA_TESTING.md) and [the current source cases](../.planning/reviews/2026-11-02-beta-launch/REVIEW.md). Confirm login, your own key/model, a ready document and cited review; test persistence after container recreation without deleting volumes. Check graceful worker stop pauses interrupted work for explicit retry. Following a hard kill, stop the worker first, then run:

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml stop worker
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps --entrypoint python worker app/pilot_admin.py recover-interrupted --workers-stopped
docker compose --env-file deploy/.env -f deploy/compose.yaml start worker
```

The host must alert on unhealthy web/worker status, disk exhaustion and backup failures. `restart: unless-stopped` restarts exited containers; a health check alone does not restart an unhealthy process. Resource caps are starting limits that still need a representative processing check on the chosen host.

## 5. HTTPS on the chosen host

Use the host's HTTPS proxy, or the optional Caddy profile with a hostname whose DNS reaches that host. Configure the actual Site URL first, open only necessary HTTPS/proxy ports and keep port 8501 private. For Caddy:

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile https up -d web worker https
```

Verify the real certificate, WebSocket/live progress, login/recovery redirects and browser media checks on that URL. Caddy is prepared but has not been started here. No host or domain purchase is required by this preparation; decide that separately.

## 6. Consistent backups and rollback

On the eventual Linux host, `sh deploy/backup.sh /absolute/path/deploy/.env` stops web/worker, creates a timestamped SQLite-aware backup, verifies it, saves the matching key separately and restarts only services that were running. It fails rather than copying a live running job. A stopped stack can use the manual command below:

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml stop web worker
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps backup backup /backups/operator-chosen-new-name --services-stopped --secret-output /backup-secrets/operator-chosen-new-name.key
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps backup verify /backups/operator-chosen-new-name
docker compose --env-file deploy/.env -f deploy/compose.yaml start web worker
```

Schedule the shell wrapper with the host scheduler and alert on its exit status. Scheduling is not installed here because no host is chosen. Export the data backup and key separately to protected off-host storage and select a retention period. Docker volumes and the verified workspace backup are on-host copies, not protection against losing that disk. The wrapper never deletes old backups; monitor growth.

For a restore drill, keep production stopped and restore into a new directory such as `/var/lib/verifier/drill-YYYYMMDD`; never overwrite the live root. Verify the restored app using an isolated stack/volume, without sending real jobs automatically. For rollback, stop services and use the preserved image plus its matching pre-update data backup and encryption key. Never use `docker compose down -v` on retained data.

## Release record

Record the tested image ID, dependency/OS audit results, backup and separate-key locations, successful isolated restore, host/TLS URL, two-account media test, email recovery test, processing limits and acceptance reviewer. Items 6, 9, 13, 14 and deferred 15 remain in [BETA_TODO.md](BETA_TODO.md); preparing this package does not close them.
