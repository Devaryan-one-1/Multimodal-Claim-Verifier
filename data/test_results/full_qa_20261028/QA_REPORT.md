# Claim verifier QA report - 28 October 2026

Tests continued after failures. Application source code, reviewed claims and approvals were preserved. New reports live under this test directory.

## Steps 1-11

| Step | Status | Finding |
| --- | --- | --- |
| 1 | PASS (headless) | Dashboard loaded without application exceptions; browser visual access unavailable. |
| 2 | PASS | 3M original PDF 158 pages; approved drafts and saved reports persisted across restart. Source hashes unchanged. |
| 3 | PARTIAL | OpenRouter model selected; Gemini gemini-embedding-001 768 dimensions retained; reasoning model change did not embed documents. Live reasoning blocked by HTTP 402; vision model blank. |
| 4 | FAILED OPERATIONALLY | All 101 direct inputs attempted; 101 service errors. No valid factual accuracy denominator. |
| 5 | FUNCTIONAL CHECKS PASS; LIVE BLOCKED | Primitive/safety checks 18/19 (the failure is HTTP 402 pause classification). Numerical source-fixture checks 11/11. Live reviewed calculations blocked at parser. |
| 6 | PARTIAL | Table/text/chart page navigation, full table, citation identity and coordinate highlight passed functional tests. Chart record has no usable content or indexed chunks; no saved OCR evidence. |
| 7 | PASS for error/coverage handling | Six isolated PDF fixtures attempted. Invalid and encrypted rejected; text-only ready with warning; scan/blank failed without ready index; difficult table text retained with warning. |
| 8 | PARTIAL / LOGGING BUG | Timestamps present and no keys/prompts/document text detected. 401 records lack correlation (400 ablation calls plus one deliberately unwrapped malformed-table fixture). Model/provider/duration fields on llm_called are null. |
| 9 | PARTIAL / CREDIT PAUSE BUG | Five-prediction limit, reuse, cache-only, 429 pause and error exclusion passed isolated control-flow fixtures. Natural HTTP 402 credit exhaustion does not pause evaluation. No compatible real successful predictions existed for this configuration. |
| 10 | PARTIAL | 100 approved main claims, 25 per verdict. Missing currencies/charts/OCR/footnotes coverage. Retrieval complete with parser disabled; full verdict and hallucination attempts failed operationally. All four ablations attempted; 400 errors. Human grounding review pending. |
| 11 | PASS | README includes architecture, setup, limitations and historical retrieval; verdict F1, numerical accuracy, ablation and hallucination conclusions remain pending. |

## Provider failure

Configured provider: OpenRouter. Reasoning model: `nvidia/nemotron-3-ultra-550b-a55b`. Maximum output: 8192 tokens.

Exact provider error excerpt: `Model service error 402: This request requires more credits, or fewer max_tokens. You requested up to 8192 tokens, but can only afford 7454.`

The complete unmodified error and model-call record are retained in each claim JSON under `claims/`. A service failure is not a factual UNKNOWN gold-label outcome. No accuracy or F1 is claimed from these failed predictions.

## Completed retrieval run

Parser disabled; candidate depth 20; 75 eligible complete claims and 49 eligible subclaims. Queries with no positive gold evidence are excluded. Historical parser-enabled results in README are a different configuration.

| Level | Stage | Recall@5 | Recall@10 | MRR@20 |
| --- | --- | ---: | ---: | ---: |
| claim | initial | 46.67% | 61.33% | 0.3300 |
| claim | reranked | 43.33% | 59.33% | 0.2835 |
| subclaim | initial | 42.86% | 57.14% | 0.3207 |
| subclaim | reranked | 51.02% | 61.22% | 0.3493 |

## Runtime attempts

| Workflow | Outcomes |
| --- | --- |
| Individual inputs | 101 attempted, 101 operational errors |
| Full verdict workflow | 160 claim/subclaim queries; 160 errors |
| Hallucination workflow | 44 claim/subclaim queries; 44 errors |
| Ablation | 400 attempts; 400 errors; four identical model configurations |

## Failures requiring attention

1. OpenRouter rejects the configured 8192-token reservation because the key can afford 7454. Provider capacity must be restored or the output limit changed before valid verdict metrics can be measured.
2. `quota_pause` does not classify natural HTTP 402 credit exhaustion as a pause. The UI can therefore repeat failed requests until the new-prediction budget stops it.
3. `render_ablation_evaluation` exists but is absent from `run_dashboard`. The backend was tested directly.
4. Four hundred ablation `llm_called` events lack request IDs; event adapter uses field names that leave provider/model/duration null.
5. Reviewed coverage lacks currencies, charts, OCR and footnotes. The stored chart has no readable content, and the ablation corpus has zero indexed chart chunks. The vision model is unconfigured.

## Scope and safeguards

- Streamlit AppTest exercised the actual dashboard. Browser visual access was unavailable; this is not a claim of a full visual browser pass.
- Numerical/display/control-flow fixtures are labeled and stored separately. Their synthetic predictions are not model-performance measurements.
- Source page 102 was visually inspected: China/Hong Kong net PP&E is 392 million in 2025 and 407 million in 2024. Its year/value pairs agree with saved reviewed evidence.
- Human grounding assessments remain pending. Automated checks were saved, and review persistence was tested in an isolated fixture; no human approval was fabricated.
- Preserved 42 tracked source/data files; changed files: [].
- Existing saved reports remain untouched. New live report files are under `datasets/main/` and `datasets/hallucination/`; synthetic control-flow reports are under distinct fixture directories.

## Artifacts

- `summary.json`: machine-readable outcome for every step.
- `claims/`: one full result JSON per reviewed claim plus the question.
- `evaluations/`: full verdict, retrieval, hallucination and ablation reports.
- `uploads/`: exact per-input processing results.
- `ui_checks.json`, `numerical_source_checks.json`, `offline.json`, `failure_resume_checks.json`: detailed functional checks.
- `logs.json`: event privacy, correlation and source-preservation checks.
