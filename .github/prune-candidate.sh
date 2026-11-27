#!/usr/bin/env bash
set -euo pipefail
baseline=compare-baseline:release
security=(--network none --read-only --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:rw,nosuid,size=268435456,mode=1777)
probe() {
  docker run --rm "${security[@]}" --entrypoint python \
    -v "$PWD/.github/package-probe.py:/probe.py:ro" \
    -v "$PWD/reports:/audit:ro" "$1" -B /probe.py
}
probe "$baseline" > reports/probe.log
sed -n 's/^PACKAGE_REPORT=//p' reports/probe.log > reports/package-probe.json
python .github/pruning-result.py prepare
if [[ ! -f reports/Dockerfile.pruned ]]; then exit 0; fi
mkdir -p "$RUNNER_TEMP/empty-context"
docker build --network none -f reports/Dockerfile.pruned -t compare-candidate:release "$RUNNER_TEMP/empty-context"
docker run --rm "${security[@]}" --entrypoint python compare-candidate:release -B deploy/container_smoke.py cloud | tee reports/candidate-smoke.txt
docker run --rm -i "${security[@]}" --entrypoint python compare-candidate:release -B - <<'PY' | tee reports/candidate-worker.txt
import sqlite3, ssl, subprocess, sys
sys.path.insert(0, "app")
import pilot_worker, pilot_task
assert sqlite3.connect(":memory:").execute("SELECT 1").fetchone() == (1,)
assert ssl.create_default_context().get_ca_certs()
subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'app'); import pilot_task"], check=True)
print("Worker imports, Python child, TLS trust store and SQLite passed")
PY
docker run --rm --network none -v /var/run/docker.sock:/var/run/docker.sock:ro -v compare-trivy:/cache \
  -v "$PWD/reports:/reports" "$(cat reports/scanner.digest)" image --image-src docker --cache-dir /cache \
  --skip-db-update --skip-java-db-update --offline-scan --disable-telemetry --scanners vuln --pkg-types os,library \
  --ignorefile /dev/null --list-all-pkgs --format json --output /reports/candidate-release.json compare-candidate:release
python .github/pruning-result.py compare
