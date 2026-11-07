# Customer-owned OpenRouter connections

The customer app (`streamlit run app/customer_main.py`) now offers **My OpenRouter connection** after sign-in and workspace access. Each customer can save their own key and exact reasoning/vision model IDs. OpenRouter charges the account owning that key.

## Operator setup

1. Finish running work and stop the customer app and worker before updating files or copying the data directories.
2. Preserve `customer_data/` and the internal `data/` independently. The implementation backup was verified at `../backups/customer-byok-20261101T134242Z/` before source changes.
3. Install the customer dependencies in the usual project environment:

   ```powershell
   python -m pip install -r requirements-customer.txt
   ```

4. Set `CUSTOMER_KEY_ENCRYPTION_KEY` in the server environment or the project-root `.env`. The local implementation generated this once in the existing ignored `.env`; its value was not displayed. Preserve that value across restarts.
5. For a new installation, generate a secret locally with `Fernet.generate_key()` from the cryptography package and place it in the server's secret configuration. Use the same secret for the UI and worker. Never commit it or include it in a customer-facing export. Do not generate a new secret on each startup.
6. Back up the encryption secret separately from the SQLite database, using protected operator-controlled storage. Restoring encrypted connections requires the matching secret. If it is lost, customers must reconnect their keys.
7. Start the UI and one worker in separate terminals:

   ```powershell
   streamlit run app/customer_main.py
   ```

   ```powershell
   python app/pilot_worker.py
   ```

The app adds a `model_connections` table to the existing control database on initialization. It does not rebuild sources or vector indexes. Existing completed reviews remain readable. Existing pre-BYOK billable jobs require a fresh request with a saved customer connection; they do not use the operator's OpenRouter key.

## Customer flow

1. Sign in and open **My OpenRouter connection**.
2. Paste an OpenRouter key into the masked field.
3. Enter exact reasoning and vision model IDs. The same compatible multimodal model can serve both roles. The `:free` suffix is part of an ID when selecting a free variant.
4. Click **Check and save connection**. This checks the key through a read-only endpoint and checks listed provider capabilities. It makes no generation request.
5. Submit a review using an already-ready source, or upload a document. New document processing requires a vision model with image input and strict JSON Schema support.

The check proves that the key/capabilities can be queried, not that the account has sufficient credits or that generation will be available. Model calls can still fail, pause and require explicit retry.

Leave the key field blank when saving model changes to keep the saved key. Model changes apply to new requests; existing queued jobs retain their submitted models. Replacing or removing a key invalidates its previous connection version for future attempts. Running work may finish. Removing the saved key from this app does not revoke it at OpenRouter.

For a paused/failed document whose original connection is no longer usable, Documents offers **Start new processing with my saved connection**. This explicitly creates a new request using the clicking user's key and the existing PDF. It does not re-upload the source, rebuild already-ready documents, or retry another user's billable job on their key.

## Isolation and billing

- Connections belong to authenticated users, not to all members of a shared workspace.
- A billable job uses its creator's saved connection and a snapshot of that job's model choices. Only its creator can retry that work. Shared workspace review/history access does not authorize spending another member's credits.
- Keys are encrypted with Fernet and bound to the owner and connection version. Only the isolated job process loads the key for model calls. The shared Streamlit and worker-supervisor environments do not receive customer keys.
- Missing, replaced or unreadable keys pause the job before model calls. Customer work does not fall back to an operator key, another customer's key or inherited server model fallbacks.
- Keys are excluded from job payloads, checkpoints, model snapshots, exports and the application's diagnostic summaries.
- History, source downloads and source deletion do not require a saved OpenRouter key. Existing workspace access checks still apply.

## Remaining Google costs

Document and query embeddings still use the operator's Google/Gemini key and existing embedding model/dimensions. Customer OpenRouter keys cover OpenRouter reasoning, OCR and chart/vision requests. This change does not move embedding costs to OpenRouter or repair the deferred table embedding issue.

## Verification

Run the full suite in the project environment:

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

For the focused service tests:

```powershell
python -m unittest discover -s tests -p 'test_customer_connections.py'
```

These tests use synthetic keys, mocked HTTP and temporary storage. The encryption tests require a working cryptography installation and exercise real Fernet encryption; a skipped encryption test is not encryption verification.

On 2026-11-01 the user reported all 29 focused tests passing in 0.618 seconds, with no skips. The assistant's separate full-suite run passed 141 tests and skipped those seven encryption tests; the user's focused run supplied the missing encryption validation. Live provider/browser smoke checks are separate.

For the live smoke check, use two test users and their own restricted OpenRouter keys. Save different model choices, submit one review from each, and confirm the resulting model identifiers match each submission. Verify that another workspace member cannot retry a review using its creator's credits. Check saved key removal and confirm the next request is blocked without affecting history.

Never paste real keys into chat or test fixtures. Provider model pricing, data policies and free-model quotas remain the customer's responsibility when selecting an endpoint.
