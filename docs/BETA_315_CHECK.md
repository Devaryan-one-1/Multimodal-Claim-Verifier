# Final checks for items 3, 4 and 15

Use the customer app, `app/customer_main.py`, with one worker. Upload `output/pdf/beta-table-chart-check.pdf` once. It contains invented data: a table on physical PDF page 1 and a labeled chart on page 2. Keep the existing Apple and internal 3M indexes.

## New PDF checks

After the document reaches Ready, select only this source. Submit these as separate reviews. The first row deliberately contains two assertions.

6 November: the approved chart year-category fix is applied. Refresh/restart the customer app and submit the chart claim as a **new review** using the already-ready PDF. Re-upload/reprocessing is unnecessary; older failed results remain historical.

### Chart counterclaim fix: live retest

The user also approved extending local source matching to chart facts. Unambiguous chart values now use the validated printed metric/year/value directly; ambiguous source matches retain model fallback. Use only the existing Northstar PDF and submit each claim below as a separate **new review**.

| Claim to paste | Expected |
|---|---|
| Northstar Fixture Ltd's annual customer count was 100 in 2026. | CONTRADICTED; actual 120; signed difference -20 customers. |
| Northstar Fixture Ltd's annual customer count was 120 in 2026. | SUPPORTED; actual 120. |
| Northstar Fixture Ltd's annual customer count was 80 in 2026. | CONTRADICTED; actual 120. The 2025 value must not be selected. |
| Northstar Fixture Ltd's annual customer count grew 50% between 2025 and 2026. | SUPPORTED; source values 80 and 120. |
| Northstar Fixture Ltd's annual customer count grew 25% between 2025 and 2026. | CONTRADICTED; actual growth 50%; signed difference -25 percentage points. |

All five must cite the chart on physical PDF page 2. For growth, check `(new - old) / old * 100`; View Page must show the printed labels. Expected verdict order: **CONTRADICTED, SUPPORTED, CONTRADICTED, SUPPORTED, CONTRADICTED**. Existing failed snapshots stay historical.

The shared View Page navigation fix is now applied: refresh/restart the customer app, reopen an existing chart result, manually choose PDF page 1, then click **Evidence > View Page**. It must return to page 2 even when that chart citation was already selected. The same action uses the actual cited page for table/text/other PDFs. No new factual review or document processing is needed for this check. Three navigation/security regressions and the full 419-test suite pass; the prepared real Streamlit fixture still awaits native execution.

| Claim to paste | Expected | Source |
|---|---|---|
| Northstar Fixture Ltd's annual revenue was USD 150 million in 2026. Northstar Fixture Ltd's annual revenue was USD 150 million in 2025. | Two atoms: SUPPORTED, CONTRADICTED. The 2025 value is USD 120 million. | Page 1, table |
| Northstar Fixture Ltd's annual research expense was USD 12 million in 2025. | Completed UNKNOWN: the 2025 cell is blank. The 2026 value must not shift into 2025. | Page 1, table |
| Northstar Fixture Ltd's annual customer count was 120 in 2026. | SUPPORTED using the chart's printed label and a chart fact address. A prose/text-only citation does not pass this chart check. | Page 2, chart |
| Northstar Fixture Ltd's annual revenue was USD 150 million in 2040. | Completed UNKNOWN: the report has no 2040 value. | No matching source year |

For the numerical results, check source values, units, formula, signed difference, source cell/fact address and View Page. This synthetic fixture checks a simple new ingestion; it does not replace representative real-report acceptance.

## Remaining Apple case

Use the already-ready Apple source for a fresh review:

`Apple's share-based compensation expense was $10,523 million for the nine months ended June 27, 2026.`

Passed by user report and real saved-result inspection. Expected: SUPPORTED, nine-month 2026 column. The actual result cites physical PDF page 8, whose cash-flow table also explicitly reports share-based compensation expense of 10,523 million for the nine months ended June 27, 2026; native extraction and the rendered page confirm it. Page 8 or the originally suggested page 14 is acceptable when its row/period/unit matches. Earlier quota/truncation attempts remain historical.

The future-year review now completes as UNKNOWN, and the compound review retains both clauses with SUPPORTED/CONTRADICTED page-1 table results. These claims do not need to be repeated. Explicit retry/latest-attempt UI behavior and chart View Page confirmation remain separate live checks.

## Failed requests and retry

Inspect an existing failed/paused review: require an operational failure message, useful details and an allowed retry action or an explanation why retry is unavailable. Its failed atoms must have no factual verdict and must not enter accuracy calculations. Saved completed subclaims may remain visible.

Retry once only after the provider recovers and the original connection/code remains compatible. An older-code job should prompt a new request. For Documents, a retry/new processing attempt must replace earlier error cards and show only the current attempt; earlier records stay saved. Do not exhaust provider credits/quota to manufacture a failure.

Latest offline results: **416/416 regressions pass**, zero skips, including native encryption checks in bundled Python 3.12. This includes eight new local-chart selection methods covering level/growth counterclaims, unrelated metrics, ambiguity/fallback, missing years/operands and unsafe facts. A replay of the actual failed parser fields and all 13 saved source records passes six checks with zero source-selection model calls and no supplied model address. Earlier 13/13 extraction/Apple checks controlled model outputs. Offline passes do not replace the fresh live retest above.
