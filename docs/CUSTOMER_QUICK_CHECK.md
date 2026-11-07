# Combined customer check: items 3, 6, 7 and 8

GSD workflow: `gsd-debug` inline. The user chose the existing Apple report. Use the current customer app with processing available and only that report selected. Update 4 November: the user now approved a selective table/graph refresh of Apple; use the action below. The existing internal 3M index stays preserved.

## New table/graph extraction check

Restart the customer app and worker (or rebuild/recreate both Docker services). In **Documents**, expand the existing Apple report and click **Refresh tables and graphs** once. Wait for **ready**, then create fresh reviews. Existing reviews keep their saved results. New reports can be uploaded normally.

If Apple failed with the currency-validation error on physical page 14, the approved fix is now applied. On that failed request click **Start new processing with my saved connection**. This uses the saved PDF and successful table caches; re-upload is unnecessary. An ordinary retry of an old-code request can pause for code-version mismatch. See [currency fix](../.planning/reviews/2026-11-04-visual-extraction/CURRENCY_FIX.md).

| Claim | Expected | Physical PDF page |
|---|---|---|
| At June 27, 2026, Apple's total current assets were $149,818 million. | SUPPORTED | 6 |
| At June 27, 2026, Apple's total current assets were $147,957 million. | CONTRADICTED; actual $149,818 million | 6 |
| Apple's inventories grew 94.0% between September 27, 2025 and June 27, 2026. | SUPPORTED; source values $5,718 million and $11,092 million | 6 |
| Apple's depreciation and amortization was $9,973 million for the nine months ended June 27, 2026. | SUPPORTED | 8 |

Require table-source cell addresses, correct source values/units, the formula and signed difference where applicable. Use **View Page** to compare with the PDF. Provider/quota failures remain incomplete processing and do not count as UNKNOWN or a pass. For a new graph report, check an explicitly printed numeric label; estimated bar heights/points must not establish a numerical verdict. Offline source replay passes; these live checks remain pending.

## Already confirmed

- **7: passed by user report.** B sees none of A's documents, reviews or prepared downloads, including after signing out of A and signing into B in the same window.
- **8: passed by user report.** Confirmation/reset emails arrive; the old password fails after reset and the new password signs in. Passwords and email codes remain with the user.

## Item 3: five small reviews

Update 4 November: the user-approved parser fix is applied and 22 new regressions pass. Restart/rebuild the customer package, then submit **new** reviews; the old failed results below remain historical. First sanity-check A and C, then repeat B/D/E. See the [parser fix record](../.planning/reviews/2026-11-04-parser-fix/REVIEW.md).

Paste only each claim into the app, without its expected verdict. Run A–D separately, then E as one compound review. Complete execution is required for every factual verdict; a provider failure/paused request is not an UNKNOWN pass.

| Case | Claim to paste | Expected |
|---|---|---|
| A | At June 27, 2026, Apple's total deferred revenue was $14.9 billion. | SUPPORTED. |
| B | At September 27, 2025, Apple's total deferred revenue was $14.9 billion. | CONTRADICTED; actual $13.7 billion; signed claimed-minus-actual difference +$1.2 billion. |
| C | Apple's total deferred revenue grew 8.8% between September 27, 2025 and June 27, 2026. | SUPPORTED under one-decimal rounding; actual growth about 8.759124%; signed difference about +0.040876 percentage points. |
| D | At June 27, 2040, Apple's total deferred revenue was $14.9 billion. | Completed UNKNOWN: the selected 2026 report cannot establish that future amount. No fake calculation/citation to a 2026 amount. |
| E | At June 27, 2026, Apple's total deferred revenue was $14.9 billion. At September 27, 2025, Apple's total deferred revenue was $14.9 billion. | Both clauses retained; first SUPPORTED, second CONTRADICTED. |

For A–C, click **View Page** and compare the cited figures on **physical PDF page 9**. The source states 14.9 billion for June 27, 2026 and 13.7 billion for September 27, 2025, respectively. The filename says 10-K, but the document is a quarterly 10-Q; use the actual dates. For C, require original source values/units, normalized inputs, source addresses, formula `(new - old) / old * 100`, actual value and signed difference. A correct label alone does not pass missing/wrong source or calculation checks.

| Case | Actual verdict(s) | Completed execution? | Calculation/page correct? | Result |
|---|---|---|---|---|
| A | UNKNOWN | Complete in saved record | Only checkbox evidence; real claim text lost | Fail |
| B | UNKNOWN | Complete in saved record | Lost claim text and wrong operation | Fail |
| C | UNKNOWN | Complete in saved record | Correct page retrieved; generic metric and wrong comparator block valid comparison | Fail |
| D | UNKNOWN | Complete in saved record | Lost claim text/wrong unit; insufficient-evidence reasoning not validated | Not accepted as a pass |
| E | UNKNOWN / UNKNOWN | Complete; two saved atoms | Both claim texts became `assertion` | Fail |

Report failures by case letter and the actual verdict/message. Provider/quota errors count as incomplete execution and should be resolved before retry; don't manufacture failures by exhausting credits.

## Item 6: reuse review C

1. Save a review decision and note: `Beta check - PDF page 9 verified`.
2. Sign out and back in; open the same review from **History**. The saved decision/note must remain.
3. Prepare and save CSV, readable HTML report and JSON. Compare saved source values/units, formula, actual value, signed difference/unit, citation/page, source version, decision/note and completion status with the screen. JSON has common `export_rows` plus the raw snapshot.
4. Change and save the note. The old prepared **Save file** copy must disappear until a new export is prepared. A fresh export must contain the changed saved note.

Result: **pending a valid completed numerical result after fixing item 3**. A temporary-DB regression pass does not prove the browser downloaded the correct file.

The user reported all five checks UNKNOWN before the patch. The saved-result parser defects and controlled reproductions are in [PARSER-FIX-PROPOSAL.md](../.planning/reviews/2026-11-03-customer-acceptance/PARSER-FIX-PROPOSAL.md). The approved fix is applied; fresh live results are pending. Existing reviews and indexes are preserved.

## Evidence collected in this round

- Customer regressions: **181 run, 174 passed, 7 existing native-encryption skips**.
- Source/citation/release guards: **21 passed**; model operand selection is controlled in numerical tests, not a live provider test.
- Local DB inspected read-only: seven completed atoms with citations, no operational failures among those atoms, zero saved numerical calculations and zero saved decisions at inspection. This does not inspect a separate Docker volume.
- Existing PDF hash matches the prior reviewed source. Fresh read-only text extraction confirms 32 pages, page 9 amounts/dates and no 2040 reference. Text extraction does not establish visual correctness; View Page remains a user check.
- No application-code change, real account/password change, provider call, upload, index rebuild or customer-data write was made by the agent. The expanded native Streamlit/Docker run remains separately tracked under deployment.

Source record: [SOURCE_CHECK.json](../.planning/reviews/2026-11-02-beta-launch/SOURCE_CHECK.json). Keep the same source/version; model outputs are observations, not gold labels.
