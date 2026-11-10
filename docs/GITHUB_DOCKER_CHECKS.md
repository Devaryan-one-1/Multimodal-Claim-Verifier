# Run Docker checks on GitHub

The `Docker checks` workflow runs `deploy/test-docker.ps1` on a fresh GitHub-hosted
Ubuntu runner. Docker Desktop and free space on your C: drive are not required.
It builds the customer cloud image, performs its pip audit and Trivy OS/library
scan, then runs the separate local-image native/UI/regression/persistence/restore
fixtures. A vulnerability, unavailable scanner or failed test keeps the run red.
Trivy scans all severity levels, including unfixed findings; no ignore file is used.

It uses generated fixture configuration only. No Supabase, OpenRouter, Google or
Docker credentials are needed in GitHub Secrets. It does not upload customer data,
publish container images, run model calls, deploy Vercel or start a live worker.
Only result/log/SARIF files are uploaded as the seven-day `docker-check-results`
artifact; generated fixture environment files and backup keys are excluded.

## First run

1. Review and commit the latest application/deployment files together with
   `.github/workflows/docker-checks.yml`. The cloud image changes are still local;
   pushing only the workflow would test the older repository version.
2. Push the reviewed branch. The workflow runs automatically on a push, including
   `beta/supabase-storage`. No merge into the default branch is needed for this
   first run.
3. Open the repository's **Actions** tab and select **Docker checks**, then the
   newest run for the pushed commit. Enable Actions if GitHub asks. Do not add real
   customer/provider secrets for these fixture tests.
4. Wait for the final status. Under **Artifacts**, download `docker-check-results`
   and share `RESULT.json` plus the relevant `docker.log` section when it fails.
   Hosted resources also have limits: disk/quota/network/scanner failures are
   unpassed checks, not successful releases.
5. Continue to [Vercel setup](DEPLOY_VERCEL.md) only after the checks pass and the
   separate remote/deployment approval. Hosted app/privacy/reconnect checks and a
   cloud backup/restore drill remain separate.

Once the workflow file is on the repository's default branch, **Run workflow**
can also launch it manually. Before then, a push triggers it; the manual button
may not appear. See [GitHub workflow events](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

Standard hosted runners are free for public repositories; private repositories
use their included/billed Actions allowance. The workflow has a 60-minute job
timeout and does not enable paid larger runners. See
[hosted runner limits](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

Scanner reference: [Trivy 0.76.2 release](https://github.com/aquasecurity/trivy/releases/tag/v0.76.2)
and [image scanning options](https://trivy.dev/docs/latest/references/configuration/cli/trivy_image/).
