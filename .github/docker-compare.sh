#!/usr/bin/env bash
set -euo pipefail
name=$1
base=$2
scanner=$(cat reports/scanner.digest)
docker pull --platform linux/amd64 "$base"
pinned=$(docker inspect --format '{{index .RepoDigests 0}}' "$base")
printf '%s\n' "$pinned" > "reports/$name-base.digest"
docker inspect --format '{{.Id}}' "$base" > "reports/$name-base.id"
scan() {
  # Collect complete reports even when vulnerabilities exist. The release gate
  # runs separately. Offline scanning keeps the same DB across all four scans.
  docker run --rm --network none \
    -v /var/run/docker.sock:/var/run/docker.sock:ro -v compare-trivy:/cache \
    -v "$PWD/reports:/reports" "$scanner" image --image-src docker --cache-dir /cache \
    --skip-db-update --skip-java-db-update --offline-scan --disable-telemetry \
    --scanners vuln --pkg-types os,library --ignorefile /dev/null --list-all-pkgs \
    --format json --output "/reports/$2.json" "$1"
}
scan "$pinned" "$name-base"
sed "s|^FROM python:3.12-slim-trixie AS runtime$|FROM $pinned AS runtime|" Dockerfile > "$RUNNER_TEMP/Dockerfile.$name"
grep -Fxq "FROM $pinned AS runtime" "$RUNNER_TEMP/Dockerfile.$name"
image=compare-$name:release
docker build --platform linux/amd64 --target release -f "$RUNNER_TEMP/Dockerfile.$name" -t "$image" .
docker inspect --format '{{.Id}}' "$image" > "reports/$name-release.id"
scan "$image" "$name-release"
docker run --rm --network none --read-only --user 10001:10001 --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp:rw,nosuid,size=268435456,mode=1777 \
  --entrypoint python "$image" -B deploy/container_smoke.py cloud | tee "reports/$name-smoke.txt"
