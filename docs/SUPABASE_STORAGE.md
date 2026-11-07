# Supabase customer storage

This is an optional customer backend. Local mode stays the default. The internal dashboard and 3M index are excluded from migration. No cloud resources have been created and no real data has been transferred by this implementation.

Cloud mode uses the existing Supabase authentication project plus:

- Private `verifier` Postgres tables for workspaces, jobs, reviews, decisions and already-encrypted OpenRouter connections.
- The private `verifier-private` bucket for PDFs, extracted artifacts, page/table/chart images and resumable model/query caches.
- 768-dimensional pgvector records for the existing embeddings. Initial search uses exact cosine distance plus the existing BM25/reranking pipeline.
- Disposable worker files under `/tmp/claim-verifier-cache`; durable state is remote. The UI keeps at most four verified PDFs in server memory and checks membership before and after serving them.

The private database schema is not exposed through the public Data API. Application tables have RLS enabled with no browser-role grants/policies. A restrictive Storage policy denies browser-role access to this bucket even if another permissive bucket policy exists. The server connects as the database owner and retains the application's membership checks. The Storage server secret is privileged; never put it in browser code, public environment variables, screenshots or Git. New secret keys are sent only as API keys, without treating them as bearer JWTs.

## 1. Prepare the project

In your existing Supabase project's SQL editor, run [storage_schema.sql](../supabase/storage_schema.sql). It creates new customer tables and a private bucket without changing authentication accounts. The vector extension must be available in the `extensions` schema, as on the standard Supabase setup. Do not expose the `verifier` schema in the Data API settings or add public bucket policies.

Privately add the values in [supabase_storage.env.example](../config_examples/supabase_storage.env.example) to your server configuration. Copy the **Session pooler** URI from Supabase's Connect panel (port **5432**, not transaction-pooler port 6543). URL-encode the database password. Use your existing project URL/publishable key, Google embedding key and **the existing preserved encryption master key**. Add a server-only Supabase secret key for private object storage; do not replace the publishable key used by login.

Keep `CUSTOMER_STORAGE=local` while backing up/migrating. The migration command connects to the target explicitly. The new driver is installed by the production Docker build; for your existing project environment:

```powershell
python -m pip install "psycopg[binary]==3.3.8"
```

## 2. Inspect and migrate

From the project root, the default command is read-only and makes **no network/provider calls**:

```powershell
python scripts/migrate_supabase.py
```

Before applying: stop both apps and the worker, make a fresh verified cold backup using [BACKUP_OPERATIONS.md](BACKUP_OPERATIONS.md), and keep its master key separate. The target `verifier` schema must be empty. Recover any locally interrupted running jobs with the existing administrator tool while workers are stopped. Wait at least 20 seconds after stopping the worker.

```powershell
python scripts/migrate_supabase.py --apply --services-stopped
```

Optional `--source "ABSOLUTE_PATH_TO_CUSTOMER_DATA"` selects a verified restored copy instead of the default local folder. The command copies all registered customer workspaces/history, validates the preserved key against saved connections, exports Chroma vectors **from disposable index copies**, checks ready-document chunk hashes and verifies every uploaded object's length/SHA. Existing model vectors are reused; no embedding or reasoning API is called. Windows artifact path fields are made portable in the cloud copy. Completed saved review records are copied unchanged. Local source files are never deleted or opened through a writable Chroma client.

The database copy is transactional and refuses to overwrite a populated target. Failed attempts can leave private, content-addressed objects in the bucket; rerunning against an empty database reuses their names. Do not delete the target schema/bucket after starting customer use. Registered-source deletion includes old private object versions, while saved review snapshots remain.

## 3. Verify cloud mode before hosting

Set `CUSTOMER_STORAGE=supabase` privately, preserving the remaining values. Run the opt-in cloud smoke:

```powershell
python scripts/test_supabase_storage.py --allow-cloud-write
```

It creates two disposable fixture workspaces, checks actual Postgres/pgvector/private Storage access and cross-workspace denial, then cleans up its own fixtures. It does not use customer documents or call model providers. A failure or incomplete cleanup is an unpassed check. This is storage acceptance; actual login, PDF rendering and verification need the separate browser check below.

Start the customer app and exactly one worker in separate terminals:

```powershell
streamlit run app/customer_main.py
```

```powershell
python app/pilot_worker.py
```

Check both accounts' history/documents, original PDF/citation pages, an existing completed review, one fresh review, explicit retry and key removal. No report should need re-uploading or re-embedding to migrate. Database/storage failures remain operational failures, excluded from factual verdicts. A new cloud worker uses a Postgres session lock; after an exclusive restart it pauses leftover running jobs for explicit retry. Old job tokens cannot change vectors.

## 4. Vercel preparation

[deploy/vercel.example.json](../deploy/vercel.example.json) is a **template**, not an active deployment. It selects the container runtime and declares one worker daemon with **zero instances** until acceptance. After the cloud smoke and release audit pass, copy it to `vercel.json`, choose a supported region close to your Supabase project, and change that region's worker count to `1`. Set the same private Production environment for the web app/worker and `PORT=8501`. Existing Docker starts only the customer app; `app/main.py` is excluded.

Set `SUPABASE_SITE_URL` and Supabase's allowed Site/redirect URLs to the actual HTTPS Vercel URL. Public hosting, Git push and worker activation require an explicit launch decision. Vercel daemon compute/quota is separate from Supabase's free storage allowance.

Vercel Functions impose connection-duration limits and can recycle Streamlit instances. Test login, draft handling, PDF media and live updates **across a connection-duration boundary/reconnect**, not just initial page load. Job/history data is durable in Supabase, but this migration does not redesign Streamlit's in-memory session/auth/draft handling. If those checks fail, keep this backend on a persistent container host rather than declaring Vercel ready.

The older successful Docker fixture run predates this migration. Re-run `deploy/test-docker.ps1` for the new driver, copied-index export and cloud regressions. Real Supabase acceptance, hosted browser acceptance, the dependency/base-image audit and cloud backup/restore remain release checks. The four reported Chroma advisories are not waived by adding cloud mode.

## Backups and rollback

The existing SQLite backup wrapper is for **local mode** and refuses cloud mode. In cloud mode, the local directory is a cache and is not a cloud backup. Keep the pre-migration cold backup and master key; plan a Postgres export plus private-bucket export and a verified restore before customer release. Supabase Free does not include automatic database backups. Never replace the master key during a restore.

Before accepting new cloud work, rollback is switching `CUSTOMER_STORAGE` to `local` and using the unchanged local source. After cloud users have created new work, switching back would omit that newer history; reconcile/export it first. Do not run local and cloud workers simultaneously against the same customers during cutover.

## Commit the implementation

After reviewing the changes, these paths include the implementation without runtime data/secrets:

```powershell
git add -- .gitignore .dockerignore .vercelignore requirements-production.txt app/pilot/cloud_db.py app/pilot/cloud_files.py app/pilot/store.py app/pilot_worker.py app/pilot_task.py app/retrieval/embeddings.py app/retrieval/cloud_vectors.py app/services/failure_details.py scripts/backup_restore.py scripts/migrate_supabase.py scripts/test_supabase_storage.py supabase/storage_schema.sql config_examples/supabase_storage.env.example deploy/env.example deploy/preflight.py deploy/healthcheck.py deploy/entrypoint.py deploy/container_smoke.py deploy/test-docker.ps1 deploy/vercel.example.json tests/test_supabase_storage.py docs/SUPABASE_STORAGE.md docs/BETA_TODO.md
git diff --cached --stat
git commit -m "Add optional Supabase customer storage and migration"
```

Official references: [Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres), [private buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals), [pgvector](https://supabase.com/docs/guides/ai/vector-columns), [pricing](https://supabase.com/pricing), [Vercel containers](https://vercel.com/docs/functions/runtimes/container), [daemon configuration](https://vercel.com/docs/daemons/config-reference), [WebSocket lifecycle](https://vercel.com/docs/functions/websockets).
