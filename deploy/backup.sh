#!/bin/sh
# Cold backup for an existing single-host stack; never deletes old backups.
set -eu
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${1:-"$directory/.env"}
compose() { docker compose --env-file "$env_file" -f "$directory/compose.yaml" "$@"; }
running=$(compose ps --status running --services)
active=""
for service in web worker; do
    case "
$running
" in *"
$service
"*) active="$active $service" ;; esac
done
if [ -z "$active" ]; then
    echo 'No customer services are running. Use the manual backup command.' >&2
    exit 1
fi
finish() {
    result=$?
    trap - EXIT
    for service in $active; do
        if ! compose start "$service"; then result=1; fi
    done
    if [ "$result" -eq 0 ]; then
        echo 'BACKUP_OK: verified archive and separate key; previous services restarted.'
    else
        echo 'BACKUP_FAILED: inspect this run and service health before continuing.' >&2
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose stop web worker
stamp=$(date -u +%Y%m%dT%H%M%SZ)
compose run --rm --no-deps backup backup "/backups/beta-$stamp" --services-stopped --secret-output "/backup-secrets/beta-$stamp.key"
compose run --rm --no-deps backup verify "/backups/beta-$stamp"
