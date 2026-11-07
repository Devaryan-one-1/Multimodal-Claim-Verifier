# Focused customer beta testing

Use `app/customer_main.py` as the customer-facing front end. `app/main.py` is the private internal research/evaluation dashboard. The customer app starts its authenticated page, backed by `app/pilot/auth.py`, `app/pilot/ui.py` and the workspace store.

The single numbered work list is [BETA_TODO.md](BETA_TODO.md). Items 1, 2, 4 and 5 are user-reported working. Items 7 and 8 have been hardened and offline checked; their two-account/email browser acceptance is pending. All remaining interface and launch work stays on that list.

## Current acceptance: items 6, 7, 8 and 11 (3 November)

Refresh the customer app to load the latest UI; restart it in the usual environment if the new History action is not visible. No PDF upload or index rebuild is needed. Finish active work before any worker restart.

- **6:** Open History, select a saved fixture review and click Open review. It opens All claims, including previously reviewed ones. Save a test decision/note, leave/reopen the review and sign out/in; confirm the saved note remains. Compare CSV/readable report/JSON against saved source values, units, period, formula, signed difference (percentage points for percent comparisons), citation and decision. JSON preserves the raw snapshot and adds common `export_rows`. Source values remain unrounded; CSV protects formula-leading cells with an apostrophe. Prepare an export, change/save a note and confirm the old Save file copy disappears until you prepare a new export. Navigating to another PDF page must likewise invalidate the prior page-image preparation.
- **7:** A uses a normal window and B a private window, with separate workspaces. User has confirmed that setup. B must see only B's documents/history. A prepares an original PDF/export; sign out of A's normal window and sign in as B there. No A-only content/prepared link should remain. Copies already delivered to A cannot be recalled. Offline revocation/forged-ID tests complement this live check.
- **8:** In disposable account A, request a reset email, enter the code yourself and set a new password. Old password must fail; new password must sign in and retain A's saved reviews. Record whether signup confirmation was required/delivered when setting up the test accounts. Never send passwords/codes to the agent. Invalid/expired/mismatched recovery and transient session failures are exercised with mocks; real email delivery is a separate result.
- **11:** User selected configuration preparation until a host/destination is chosen. Review [BACKUP_OPERATIONS.md](BACKUP_OPERATIONS.md). Transfer and restore tests use temporary data; the real retention preview deletes nothing. No schedule, off-host copy, cleanup or email/webhook is installed by preparation.

The expanded `deploy/test-docker.ps1` rebuilds the changed customer package, runs real Streamlit AppTest history/download/proto checks and the focused account/export/backup regressions using fixture data. Its account API boundaries are mocked; it does not prove live mail or browser privacy. Real backup mount environment settings are overridden for the fixture stack. A Chroma audit failure still yields an overall failed result even if all fixture checks complete. Only mark the new checks passed after reading that run's `RESULT.json` and logs.

Table embedding problems remain deferred. Start this round with facts established in readable PDF prose. Record table-related failures separately; do not change expected verdicts or silently count them as passing. Keep the existing internal 3M index and ready customer documents.

## 1. Offline regression checks

From the project root, use the same activated environment as the customer app:

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

The focused additions can also run on their own:

```powershell
python -m unittest discover -s tests -p 'test_customer_beta.py'
```

The new tests exercise actual customer auth/control flow, customer review rendering and exports. Streamlit, Supabase and PDF rendering are mocked at their external boundaries; export tests use a temporary SQLite database. They do not send emails, call a model, upload/reprocess documents, or modify the application's saved customer data.

Coverage includes verified identities, rotated/expired sessions, rejected account states, membership rechecks, identity-switch cleanup, logout during an auth outage, recovery verification, operational versus factual review status, retry limits, saved calculations, citation ownership and export consistency/escaping. This is distinct from browser acceptance and financial-verdict accuracy.

## 2. Start the actual customer services

In separate terminals, with the usual project environment active:

```powershell
streamlit run app/customer_main.py
```

```powershell
python app/pilot_worker.py
```

Run one worker. Confirm the customer app reports that processing is available. Use the existing Supabase configuration and the admin access-grant steps in [SUPABASE_SETUP.md](SUPABASE_SETUP.md).

For new billable work, save your own connection in **My OpenRouter connection** first. Follow [CUSTOMER_BYOK.md](CUSTOMER_BYOK.md) for the encryption-secret setup, key/model choices and billing boundary. Each test user needs their own connection; shared workspace access does not authorize retrying another user's billable job.

## 3. Hands-on acceptance checks

These are a checklist to perform in real browsers, not results already established by the automated suite.

| Check | Action | Pass condition |
|---|---|---|
| Sign-in and recovery | Check a wrong password, email confirmation, password reset and sign-out using your test accounts. | Wrong credentials/unconfirmed identities cannot enter a workspace; real email codes work; the new password works after reset; sign-out returns to the account screen. |
| Uninvited customer | Sign in as account B before granting a workspace. | Only the access-request screen appears, with no customer sources or review history. |
| Customer separation | Grant A and B different workspaces. Keep their sessions in separate browser profiles. Use A's already-ready public PDF and saved review. | B sees none of A's document names, sources, reviews or exports. Switching accounts does not retain A's content in B's screen. |
| Financial results | Run the six source-reviewed cases below in A. | Each atom's expected verdict and actual result agree, with matching source period, entity and unit. No clause disappears from the compound case. |
| Live progress | Submit one review and leave it open without clicking Refresh. During processing, try typing a draft in the new-review form. Check a document processing request when one is next needed; reuse ready PDFs for the review check. | Progress updates every two seconds, saved subclaims and the final result appear automatically, and a newly ready PDF becomes selectable. Stage-only updates leave forms alone. The draft survives result/status updates. A paused/failed job exposes retry rather than an endless completion message. |
| Drafts | Edit a claim/name and leave the field. Switch to Documents and back. Edit a review note and change the selected claim, then return. Try Clear draft and Discard draft changes on disposable drafts. | Draft text is retained within the current session/workspace; no job or reviewer decision is submitted by editing alone. Clear/discard affects drafts, not saved reviews. Logout/new sessions do not preserve drafts. Check the last unsent keystrokes during a live completion separately in the browser. |
| Connection settings | Edit model IDs and attempt an invalid connection check. Then save valid choices, submit a fresh review and open Models and request details. Use a second tab to remove/replace a test connection. | Model edits remain after failure and the password field clears. Saved choices and job-selected choices are distinguishable. Observed model/request IDs match activity when available. Submission controls react to connection removal. Retry does not offer the removed/replaced key. |
| Page viewer | Open View Page on the completed results; use PDF page positions, not printed page labels. | The selected citation opens the correct source page; the displayed values/quotes really appear there and establish the claimed metric and period. |
| Exports | Export one completed review and compare CSV/HTML/JSON against the screen. | Source values, units, formula, page citations, review decision and run status agree. A partial review is identified as incomplete. |
| Persistence | Finish the review, then close/reopen the browser. With jobs idle, restart the customer app and worker, sign in and reopen History. | Documents, saved reviews, decisions and exports persist. Re-sign-in after a hard refresh is acceptable for the current session-based design. |
| Access revocation | Revoke A's membership, refresh A's existing tab and attempt a new action. | Workspace actions and preparation of new PDF/export copies are denied. The customer app uses inline authorized media instead of shared file URLs. Already delivered/downloaded copies cannot be recalled. Restart the app after updating to remove old shared media URLs. |
| Operational failure | Inspect a saved failed/paused review if available. If a new service failure happens, resolve it before explicit retry. | It is labelled failed/paused, offers retry only when allowed, and is excluded from factual accuracy. Safe provider/HTTP/error metadata is retained. Completed subclaims survive retry. |

Forced provider failures are covered by offline mocks; do not exhaust credits or free-model quota to manufacture an outage. The older [CUSTOMER_ACCEPTANCE.md](CUSTOMER_ACCEPTANCE.md) contains additional worker interruption, deletion, resource and reviewer-concurrency checks.

## 4. A small claim set with source-reviewed expectations

Use the already-ready public report. First identify the source sentence and PDF page, then write the claim and expected verdict. Preserve exact company, metric, period, currency and scale. Do not choose the expected answer by copying the model's prediction.

| Case | Construct from the source | Expected outcome |
|---|---|---|
| C01 | A numerical level assertion copied accurately from a prose sentence, with its year and unit. | SUPPORTED, with the matching source value and citation. |
| C02 | Another explicit prose fact about a different metric or period. | SUPPORTED, with the correct source context. |
| C03 | C01 with a materially wrong value, beyond the source's rounding tolerance; retain the same metric/year/unit. | CONTRADICTED, with a comparison against the actual value. |
| C04 | C02 with an intentionally wrong numerical value; retain the correct period and unit. | CONTRADICTED. |
| C05 | A well-formed assertion whose metric/value cannot be established from the selected source. Manually confirm there is no contradicting source fact either. | A completed factual UNKNOWN, distinct from provider failure. |
| C06 | Combine one supported assertion and one contradicted assertion, retaining both clauses. | Both atoms are present and separately checked with the expected verdicts. A completed compound result must not hide an unchecked clause. |

For numerical cases inspect source values, normalization, units, formula, claimed/actual values, and signed difference wherever a calculation is available. A missing or wrong citation is a failed case even when the label looks correct.

Record case ID, claim text, source document/version, exact quote, PDF page, expected atom verdicts, actual atom verdicts, execution status, model identifier and notes. Keep incomplete execution separate from completed UNKNOWN. This is a small smoke set, not a measured production-accuracy benchmark.

## 5. Record the outcome

Mark each manual check **not run**, **pass**, **fail** or **deferred**, with a short reason. No manual check is automatically passed by the unit-test count.

Before beta invitations, resolve customer-access failures, wrongly SUPPORTED smoke cases, misleading completed statuses and lost saved data. Log the deferred table embedding issue explicitly. Current provider restrictions still determine whether customer documents may be sent to the selected endpoint.

For a later held-out accuracy study, use the existing `app/pilot/benchmark.py` and human-labelled JSONL predictions described in [CUSTOMER_ACCEPTANCE.md](CUSTOMER_ACCEPTANCE.md). Keep that dataset separate from existing evaluation data and report completion coverage alongside verdict metrics.

## Earlier customer-screen verification: 2026-11-01

- Full suite: **111 tests passed**, including **26 new customer-beta tests** in `tests/test_customer_beta.py`.
- Execution used the available Blender Python 3.11 interpreter with `-X utf8 -B`; the production environment's installed SDK/PDF packages were not exercised by this run.
- Customer review tests include the actual calculation renderer, checking original/normalized values, formula dispatch, positive/negative signed differences and percentage-point display. Browser layout and real PDF-page matching still require the manual checks.
- Changes for this testing refinement are limited to tests and testing documentation. Table embedding fixes remain deferred.
- The manual checklist and six-claim set are prepared; no fresh live-browser, email-delivery or model-generation pass is claimed here.

## Live customer progress: 2026-11-01

The customer UI now uses a native Streamlit fragment to read workspace job stages and worker health every two seconds. It refreshes the full page when a job status, saved claim count or source readiness changes, so ready sources and completed results appear automatically. Stage and heartbeat changes redraw only the progress panel. Refresh status remains an optional fallback.

The polling endpoint rechecks membership on every read, returns summary counts rather than historical evidence, and does not submit work or call providers. The fragment re-enters authentication when its access expires or the logged-in identity changes. An ordinary full render never interrupts a pending form submission to apply a progress update.

Run the focused checks with `python -m unittest discover -s tests -p 'test_customer_progress.py'`. The 16 checks cover polling registration, real claim counts, terminal transitions, ready-source updates, worker recovery, workspace separation, access expiration/revocation and avoiding unnecessary full-page reruns. Complete the Live progress browser row above to establish real browser behavior and draft preservation; offline fixtures do not establish those by themselves.

Validation after this change: full suite **164 tests run, 157 passed, 7 skipped** using Blender Python 3.11 (`-X utf8 -B`). The skips are the existing real-cryptography checks unavailable in that interpreter; the user's earlier project-environment run of all 29 connection tests passed. `git diff --check` passed. Live browser inspection was blocked because Computer Use could not determine Chrome's current URL confidently enough to enforce its policy. No live-browser progress or draft-preservation pass is claimed.

## Customer-interface refinement: 2026-11-02

Claim/name, review-note and model-choice widgets now keep a separate session draft after a field change; API keys are excluded from that draft mechanism. Editing is isolated in non-timer fragments, and submission/save remains explicit. A reviewer draft keeps its original decision version, so another reviewer's saved changes disable saving until the local draft is discarded. Ordinary navigation can restore drafts after Streamlit cleans the widget's state.

Connection checking clears only its password form, preserving edited model IDs on failure. Saved connection models and submitted request models are displayed separately. Models and request details shows only allowlisted observed provider/model/status/request identifiers. HTTP failures provide actionable hints without displaying provider bodies. Retry controls explain removed/replaced connections, old requests/code, ownership and the attempt limit.

Polling also detects current-user connection changes. When the worker is unavailable, the screen shows waiting cards without a running spinner, reports its last heartbeat age, and explains that status checks continue while processing awaits service recovery. A stale heartbeat was confirmed in the actual database on 2026-11-02; it is not proof that live polling is broken. Windows denied process inspection, and the operator was asked to start the worker in the usual environment.

Final offline validation for this refinement: **184 tests run, 177 passed, 7 skipped**, using Blender Python 3.11 with `-X utf8 -B`. Twenty tests were added, including the real task/checkpoint resume regression with mocked provider calls. The seven skips are the existing encryption checks requiring the project cryptography installation. Scoped tracked-file whitespace checks passed. Browser draft/keystroke behavior and live service recovery still require the combined acceptance check.

## Account separation and recovery: 2026-11-02

Items 7 and 8 in [BETA_TODO.md](BETA_TODO.md) are implemented and covered by offline regressions. Tests use temporary databases, synthetic identities and a mocked Supabase boundary. They send no emails, change no real passwords/memberships and do not establish live SMTP/browser behavior.

The customer app no longer registers private PDF images or downloads with Streamlit's shared media file routes. Page images are embedded inline after authorization. Each download button prepares an inline copy after checking current identity, selected workspace, access deadline and membership before and after preparation. Click **Save file** to download the prepared copy. One copy is retained in the session so live updates do not hide that control, with authorization checked again before redisplay. This is suitable for the current small uploads; a streaming file service would be a separate scaling decision. A copied data URL contains the file itself and is not a revocable server link.

**Restart the customer app after updating** to clear files previously registered in its old media cache. Keep the existing worker running. No data/index migration is required.

Combined browser checks:

1. Sign in as A and B in different browser profiles, with distinct workspaces. Reuse existing ready public reports. B must not see A's documents, saved reviews, decisions, exports or page images. Check switching workspaces/accounts too.
2. As A, open a cited page, prepare an original PDF and an export, then click Save file. Confirm images and downloads work in the actual browser. No customer image/download should use a new `/media/` server URL.
3. Using the normal administrator workflow on disposable test access, revoke A's membership. A must lose workspace access on the next poll/action. New preparation/download actions must fail. Restore the test grant afterward if needed; existing stored work is not deleted by revocation.
4. Use a disposable new account to test confirmation and **Resend confirmation**. Confirm wrong/expired codes do not grant access. A confirmed but uninvited account must still see only the access-request screen. No reset/confirmation eligibility is revealed by send responses.
5. Test a wrong sign-in password, then reset a test account using its email code and a matching password of at least 12 characters. Invalid code/password pairs must not change the password. Successful reset clears the local customer session; the new password works and the old one fails.
6. If the same account is signed in elsewhere, its next full app verification after a successful global reset must reject the revoked refresh session. Existing fragment access is bounded to five minutes and current workspace membership is checked on operations/polls; already delivered data cannot be revoked retroactively.
7. Sign out and sign in as the other account. Previous drafts, selected sources/citations, prepared files and review notes must not appear. Check recovery/send failures without copying passwords or codes into logs/chat.

Run the focused offline checks with `python -m unittest discover -s tests -p 'test_customer_account*.py'`. The standard full-suite command above also includes these checks.

Verification for items 7 and 8: **220 tests run, 213 passed, 7 skipped**, using the available Blender Python 3.11 interpreter with `-X utf8 -B`. This adds 36 regressions covering the new account/media behavior and related UI boundaries. The skips are the existing real-cryptography checks; no cryptography implementation was changed here. Scoped tracked-file whitespace checks passed. The installed Streamlit iframe sandbox includes download support, and the legacy inline-component fallback is unit tested. Actual browser downloads, real OTP email delivery and real second-account/revocation acceptance remain not run by this session.
