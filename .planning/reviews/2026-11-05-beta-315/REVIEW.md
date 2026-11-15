# Beta checks: items 3, 4, 15; budget steps for 13

GSD skill: `gsd-debug`, inline. PDF skill used for the user-approved two-page synthetic fixture. No application code, live records, saved connections, billing settings or models were changed by the agent. No provider requests were sent.

## Verified

- The full suite passes **401/401**, with **zero skips**, using bundled Python 3.12. This includes the seven encryption checks unavailable in the Blender interpreter. The separate Blender run remains 394 passed/seven skipped.
- Thirteen further source checks pass: five Apple cases with period-specific saved snapshots, six cases from the generated PDF's native table, and two controlled chart-address cases. Production normalization, validation and arithmetic run; model responses are controlled. See `OFFLINE_CHECKS.json`.
- Real PDF extraction preserves the blank 2025 research cell in its original column. Both pages were rendered with Poppler and visually inspected. Printed chart labels are 80 for 2025 and 120 for 2026. The PDF is in `output/pdf/beta-table-chart-check.pdf`.
- Read-only inspection finds the worker healthy and Apple ready. The two latest quota attempts are paused at claim parsing, with no completed atoms/factual verdicts. Two earlier attempts stopped at numeric selection with `finish_reason=length`, text limit 8,192. The 32,768 vision allowance does not apply to these text calls.
- Offline coverage includes failure classification/privacy, latest-attempt display, explicit retry/ownership/code guards, completed subclaim preservation and exclusion of failed evaluation queries from accuracy.

## Harness issue

The earlier `replay_saved.py` grouped snapshots by metric only. After new quarterly compensation reviews were saved, it selected a quarter snapshot for an expected nine-month amount and failed. This was test-input drift, not evidence of a production period failure. The new read-only audit selects the metric and reporting period explicitly; all five Apple replay cases pass. The old script and historical report were not rewritten.

## Pending live acceptance

Browser access host is unavailable. Docker's engine is not reachable and its user config is inaccessible in this session. Neither is bypassed. Native Streamlit/Docker and browser passes are not inferred from offline tests.

The user chose a generated small PDF and received the link and upload instruction. Await its processing status, then the claims in `docs/BETA_315_CHECK.md`. The fresh Apple nine-month compensation case and operational recovery/retry remain provider-dependent. Do not mark items 3/4/15 completely accepted until these observations exist. A synthetic chart is limited evidence; a representative real chart/report remains advisable for broader document support.

## Budget preparation

`docs/EMBEDDING_BUDGET.md` provides current official Google project cap, email budget and usage-monitoring instructions. The existing embedding pacing is per process and is not a dollar limit. Item 13 is instructions prepared, configuration not applied.

## Live update: 6 November

The generated PDF processed successfully: one table, one chart, 13 evidence records/chunks. The first three saved reviews match the user's report: 2026 revenue SUPPORTED, incorrect 2025 revenue CONTRADICTED, blank 2025 research expense UNKNOWN. Future/compound claims and the fresh Apple nine-month compensation case remain untested.

The chart claim failed twice at numeric_selection. OpenRouter calls returned successfully with finish_reason=stop; this failure is distinct from the older quota/truncation cases. The extracted chart has the correct explicit values 80 and 120, but years are stored under category while period is null. The verifier requires the year in period. A controlled address replay reproduces the rejection; copying these printed years into period in memory produces SUPPORTED at 120 with page-2 chart provenance. See CHART_DIAGNOSIS.json. This confirms a chart period-label integration defect, not a missing embedding or arithmetic defect. The generic invalid_response diagnostic also misdescribes this local source validation failure.

Proposed fix, awaiting user approval: narrowly recognize unambiguous explicit year-category labels for an annual/time chart when period is absent, preserve rejection of conflicts and ambiguous non-time categories, and report source-validation failures separately from invalid model JSON. Add regressions using this actual fact shape, wrong-year/amount and conflicting labels. Existing saved evidence should be readable without a PDF re-upload or index rebuild. No application code or real source/job/index was changed during diagnosis; no provider request was sent by the agent.

The user confirmed Google is on the Free plan; item 13 currently needs usage/quota monitoring. No paid billing setup is requested.

## Approved chart fix: 6 November

The user approved the two scoped changes. `grounded_numeric.py` now recognizes a bare printed year stored as a category only when the chart title/caption or x-axis identifies a time chart. Explicit non-time axes/categories and conflicting year labels are rejected. An existing period remains authoritative and must identify a single matching year. Estimated values, wrong years and absent quarterly context remain rejected. The interpretation is recorded as `explicit_chart_year_category` in the calculation's source address. Original fact fields are not rewritten.

`failure_details.py` now distinguishes validation against saved source evidence from invalid provider JSON/schema. It provides controlled period/conflict/time-axis messages without exposing raw model text or claims. Both the new and older source-validation exception wording are recognized. Provider error classification and source-selection call order remain unchanged.

Seven new test methods reproduce the live fact shape and verify the positive/negative cases plus diagnostic privacy. They failed against the original behavior where applicable. The complete suite passes **408/408, zero skips**, under bundled Python 3.12. A separate replay imports real Pydantic schema validation and the production verifier, controls only model addresses, and passes **9/9** checks against the exact saved chart. Correct 2026 count is SUPPORTED at 120; incorrect 80 is CONTRADICTED; 2025 count is 80 and growth is 50%. Conflicting, ambiguous, estimated and wrong-year inputs are rejected. The saved evidence hash is unchanged. See `CHART_FIX_REPLAY.json`.

Scoped whitespace checks pass. No real provider request, PDF re-upload, index rebuild, job/result mutation or model/budget change was performed. No commit was made. A fresh live review is still needed to confirm model-selected chart addresses after deployment/reload; old failed reviews remain historical. Remaining future/compound and Apple compensation cases retain their pending status.

## Live chart acceptance after the fix

The user reported SUPPORTED. Read-only inspection of their real completed review `b3a3cfe8875443ca81a328d36321ad76` confirms the exact 2026 claim, actual value 120, only chart evidence on physical page 2, fact index 1 and `explicit_chart_year_category` interpretation. This establishes successful live ingestion/retrieval/model source selection/calculation for this synthetic chart; it is separate from the controlled replay. The user submitted the review; the agent did not send a provider request or operate the browser. A View Page click was not observed. Future/compound claims, fresh Apple nine-month compensation and explicit retry/latest-attempt browser checks remain open.

## Additional counterclaim failure

The user reports the other added claims work, while the 100-customer claim fails. Two saved attempts, `4aef13d8230c443e9a0293895fc66be6` and `5a68590687e549a0b3c3555766178084`, contain correct parsed metric/year/literal fields but pause at numeric_selection after two successful model calls and source-validation failure. Their exact returned source-selection fields were not saved, so it is not possible to identify the individual bad address/quote/role field from the stored diagnostics. The actual 50% customer-growth review `f235367388d9489f800a941681241a5f` succeeds with page-2 chart evidence.

Read-only production replay of the actual parsed 100-customer claim and all 13 source records finds exactly one validated 2026 chart fact: value 120. The existing local matcher ignores charts and returns None. With the validated chart address supplied, real schema validation and calculation return CONTRADICTED, claimed 100, actual 120, signed difference -20, page 2. See `CHART_COUNTERCLAIM_DIAGNOSIS.json`.

Proposed additional change, awaiting user approval: extend existing local matching to chart facts that are uniquely identified by metric, period and validated source properties; retain model fallback when ambiguous. Add 100-versus-120 and ambiguity regressions. This would be an explicitly approved expansion of chart source matching; the earlier tool-order question alone is not authorization. No new application edit or provider call was made during this investigation.

## Approved local chart matching and tests

The user explicitly approved that additional change and requested automated and manual checks. The existing `_local_selection` stage now enumerates table-cell and chart-fact addresses, validates each with the same production metric/period/unit/provenance checks, and proceeds only when every required operand has exactly one candidate. Candidate selection is independent of the claimed amount. Competing table/chart or repeated chart matches, missing operands and unsafe facts retain model fallback. Margin selection behavior remains unchanged. This is the newly approved chart extension, separate from the earlier advice-only tool-order question.

Eight new test methods exercise 100/120/80 level claims, correct/incorrect growth, unrelated charts containing the claimed value, ambiguity with actual model fallback, missing years, incomplete growth operands, estimated/conflicting facts and competing table/chart matches. The new positive/uniqueness tests failed before the implementation and pass after it. All **416/416** suite tests pass with zero skips under bundled Python 3.12. Scoped whitespace checks pass.

`LOCAL_CHART_REPLAY.json` uses the actual failed parser fields, all 13 saved evidence records, real Pydantic and production matching/calculation. Six checks pass. Claimed 100 returns CONTRADICTED against 120, signed difference -20; 120 is SUPPORTED; 80 in 2026 is CONTRADICTED against 120. Growth 50% is SUPPORTED and 25% is CONTRADICTED against actual 50%. An additional competing chart prevents local selection. All numerical sources are page-2 chart facts. The source-selection mock is configured to fail on any call and receives **zero calls**; no model address is supplied. The source file hash is unchanged.

The live five-claim retest is in `docs/BETA_315_CHECK.md`. Create new reviews; existing failed snapshots remain historical. No live request, PDF re-upload, index rebuild, job mutation, key/model/budget change or commit was performed. The fresh live counterclaim result remains pending.

## User acceptance of local chart matching

The user subsequently reported "works" for the chart retest. Read-only current-code checks confirm three saved cases: review `f6530841a1a3487a9df78a18e963c932` returns CONTRADICTED for 100 versus actual 120, difference -20; `419f2fdcb0454b339aea190fe44c8ea5` returns SUPPORTED at 120; `a19c8ba7030d42079508f0470d37fa20` returns CONTRADICTED for 25% versus actual growth 50%, difference -25 percentage points. Each cites only chart evidence on physical page 2. This confirms recovery of the previously failing 100-customer case.

`LIVE_CHART_ACCEPTANCE.json` records the direct observations. The 80-level and 50%-growth cases were not found as new current-code saved runs in this inspection, so the broader user-reported pass is kept distinct from the three directly confirmed records. The 50%-growth case had succeeded before the local-matcher extension. No chart View Page click was observed. The agent sent no provider request and modified no real job. Remaining future/compound/Apple and retry checks are still tracked separately.

## Final claim acceptance

The user reports the final three checks work. Real saved records confirm:

- `05e56a4adc5141fcb8aa23bac9e94bb9`: 2040 Northstar revenue claim completes as factual UNKNOWN, with no calculation/actual value.
- `391d69244df24b3196dc292483b219a1`: both compound clauses retained; 2026 revenue SUPPORTED at 150 million, 2025 revenue CONTRADICTED against 120 million, each citing the page-1 table.
- `d3c1ad2363b446c3921162bef6aa0f2b`: Apple nine-month share-based compensation SUPPORTED at 10,523 million, using the page-8 cash-flow table. The selected row, million scale and nine months ended June 27, 2026 match native PDF extraction and the rendered full page. Page 8 is therefore a valid alternate citation to the originally suggested page 14; there is no reason to force the latter.

See `FINAL_CLAIM_ACCEPTANCE.json`. The PDF skill was used for this read-only alternate-citation check. The agreed claim/table/chart test set is now accepted, with user reports and directly inspected records distinguished above. Explicit retry/latest-attempt browser behavior and chart View Page clicks remain unobserved; native Docker/hosted checks and broader real-report coverage are separate work. No application code, real data, model settings, indexes or provider requests were changed by recording acceptance.

## Chart Evidence View Page diagnosis

The user reports that Evidence > View Page does nothing or stays on the wrong page. The actual review_detail button path is reproduced with the UI fixture: a chart citation at index 0 targets page 2; the viewer has been manually browsed to page 1. Clicking View Page sets citation index 0 again but leaves PDF page state at 1. The viewer's citation_signature is unchanged, so it does not reset the page. By comparison, the calculation's View input page action already sets both citation and page.

Proposed change, awaiting approval: inside the existing authorized-citation branch, set the target PDF page together with the citation index. Add a regression returning from manually browsed page 1 to an already-selected page-2 chart. This is a navigation-state bug; no PDF re-upload, source processing or new factual review is needed. No application edit or provider request was made during diagnosis. This reproduction uses the actual UI function with a mocked PDF renderer and fixture state; native browser observation is not claimed.

## Approved shared View Page fix

The user clarified that the fix must apply generally and approved it. The shared Evidence > View Page handler now sets the target page together with the ownership-filtered citation index. This is one added application statement in `app/pilot/ui.py`, with no report name, chart-specific branch or hardcoded page.

Three new regression methods cover chart/table/text citations targeting pages 2/7/4 in different documents; returning to the same citation from manual page 1; ownership-filtered indexes; and blocked foreign/zero/negative/boolean/string page references. The navigation cases fail before the change and pass afterward. The pre-existing foreign-source read-denial assertions remain intact. Full suite **419/419 passes, zero skips** under bundled Python 3.12; focused customer tests pass 35/35. Scoped whitespace checks pass.

The existing native Streamlit smoke fixture now creates a two-page PDF and exercises real review_detail/viewer controls: browse from cited page 2 to page 1, click Evidence View Page, require page 2 with the same citation selected. Embedded fixture syntax passes. Native execution/browser confirmation is still pending because this session lacks Streamlit/browser access and a usable Docker daemon; this is not presented as a native/browser pass.

Manual confirmation: refresh/restart the customer app, reopen the existing chart review, manually choose PDF page 1 and click Evidence > View Page. Expect page 2. Existing reviews/PDFs are reusable. No job mutation, re-upload, index rebuild, key/model change or provider request was performed.

The user subsequently reported "works" for that View Page check. Chart navigation is now user-confirmed. The agent did not operate the browser; the native Streamlit/Docker fixture remains unexecuted in this session. Explicit retry/latest-attempt UI acceptance remains separate.
