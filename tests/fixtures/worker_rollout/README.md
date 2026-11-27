# Worker rollout tests

The four synthetic PDFs and [claim manifest](cases.jsonl) pair each claim with a verdict and two expected citation targets. Select just the named PDF for each review. For Mill Beck, both citations are on page 1. Check the table number and the note wording as well as the page.

## Local observations on 21 November 2026

Four fresh reports went through PDF extraction and the hybrid verification pipeline with the configured OpenRouter text and vision models. All four indexed. The first-pass observations are in [local-results.jsonl](local-results.jsonl). Each duration covers the verification attempt after indexing. Model calls count dispatched verification requests, including a rejected HTTP request. An in-process cooldown dispatched zero requests. Embedding calls, PDF processing and queue time are outside those measurements.

| Report | First-pass observation |
| --- | --- |
| Fern Quay | Completed UNKNOWN with neither expected citation. The extracted evidence contains the marked revenue row on page 1, the explicit page reference, and the retail-only note on page 2. |
| Mill Beck | The text verification request received HTTP 429. No completed verdict. |
| Aster Vale | The claim parser encountered the provider cooldown after the preceding review. No completed verdict. |
| Red Tarn | Claim parsing failed after a successful provider HTTP response. No completed verdict. |

One of four reviews completed. Neither of the two required citations in that review was present. There were zero incorrect SUPPORTED verdicts and zero completed SUPPORTED verdicts. The median attempt took 26.7 seconds. The four attempts used 19 model calls, averaging 4.75 per attempt.

A separate local SQLite supervisor run uploaded Fern Quay, indexed it in a child process and dequeued a new review. The parser returned “Fern Quy” for “Fern Quay”. Document scoping paused the review with zero saved verdicts. This repeat is outside the four first-pass measurements.

Run the local regression suite and reproduce the score from the repository root in PowerShell.

```powershell
$env:CUSTOMER_STORAGE = 'local'
& '..\venv\Scripts\python.exe' -B -m unittest discover -s tests
& '..\venv\Scripts\python.exe' -B app/pilot/benchmark.py tests/fixtures/worker_rollout/local-results.jsonl
```

## Hosted checks

1. Use an isolated staging project and its test database. Follow the [worker activation instructions](../../../docs/DEPLOY_VERCEL.md#3-activate-one-worker-after-approval) to enable one worker. Verify its heartbeat, then upload the four reports.
2. Submit the manifest claims individually. Record the request ID, attempts, queue-to-terminal time, verification calls, verdict and conflict warning. Open each citation and compare its document, page and highlighted evidence with the manifest. Open the review again from History.
3. For the restart drill, use a test job with a saved pending footnote checkpoint. Interrupt the staging worker after the checkpoint and before the follow-up finishes. Restart it, retry the same job and confirm one saved result and no repeat of the three initial findings.
4. For the rate-limit drill, use a controlled provider response in an isolated worker test. Verify the 429 occurs during the footnote follow-up, that the review pauses and observes Retry-After, and that retry reuses the saved findings. Record any actual hosted occurrence separately. The local regression injects the 429 at this boundary.
5. Record citation counts only for reviewed verdicts. Keep the original first-attempt outcomes and record retries separately. Use new reports to measure performance after fixing failures exposed by these four.
