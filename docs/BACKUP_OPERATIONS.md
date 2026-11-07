# Backup operations for the customer beta

Item 11 status: the real local cold backup/isolated restore and Docker fixture restore already passed. On 3 November the user selected **prepare configuration; host/destination not chosen**. No host schedule, cloud account, external copy, retention deletion or notification was installed or performed by this update.

## Verified archive transfer

`scripts/backup_restore.py copy` transfers an existing immutable archive, rather than copying live application files. It verifies the source manifest/hashes/SQLite first, requires a new destination, restricts permissions, copies only manifest-listed files and verifies the copy. It can separately copy the matching master key. Existing files and live data directories are refused. The app and worker can remain running while copying an already completed archive.

From the usual activated project environment, replace the destination placeholders with your owned storage:

```powershell
python scripts/backup_restore.py copy "F:\Multi Modal Change Detector\backups\beta-release-20261102T185422Z" "ARCHIVE_DESTINATION\beta-release-20261102T185422Z" --key-file "F:\Multi Modal Change Detector\backups\secrets\beta-release-20261102T185422Z.key" --secret-output "SEPARATE_KEY_DESTINATION\beta-release-20261102T185422Z.key"
python scripts/backup_restore.py verify "ARCHIVE_DESTINATION\beta-release-20261102T185422Z"
```

Do not run placeholder paths literally. Choose a private off-machine destination and a separate protected key location. A local/cloud-sync folder alone does not prove a successful remote copy: confirm synchronization and access to the remote copy. The permission check must succeed before private key bytes are written. Keep the original archive/key until the new copy and isolated restore pass. The master key must match the archive fingerprint; do not regenerate it.

For an isolated restore, choose a new empty directory and provide the separately preserved key:

```powershell
python scripts/backup_restore.py restore "ARCHIVE_DESTINATION\beta-release-20261102T185422Z" "NEW_ISOLATED_RESTORE_DIRECTORY" --key-file "SEPARATE_KEY_DESTINATION\beta-release-20261102T185422Z.key"
```

The tool never overwrites an existing restore directory. Do not point a running app/worker at the drill directory.

## Future Docker host storage

The private `deploy/.env` can override the backup mount destinations:

```dotenv
BETA_BACKUP_ROOT=/mnt/private-backup-archives
BETA_BACKUP_KEY_ROOT=/mnt/separate-private-key-backups
```

These must be existing absolute host directories with permissions appropriate for the container's UID 10001. Configure the host's remote/encrypted storage separately; these settings only mount the selected directories. Defaults remain separate **on-host** Docker volumes named `backups` and `backup-secrets`. Neither default is an off-host copy.

`deploy/test-docker.ps1` explicitly forces its own fixture volumes even if the operator's environment defines real backup destinations, so test archives do not enter the real backup folders.

## Prepared nightly schedule

`deploy/systemd/claim-verifier-backup.service` and `.timer` are Linux host templates. The timer example runs at 03:00 UTC with up to five minutes of delay and catches up after downtime. Before installing, select the host, replace `/srv/claim-verifier` and `User=claim-verifier` with the real path/operator, verify that operator's Docker access, and configure the private environment/storage destinations. The service requires a system Docker service; adapt it if the host uses rootless Docker or a managed container platform. No user/group/service settings are changed by these files.

Once the real deployment stack is idle, manually run the wrapper first:

```sh
sh deploy/backup.sh /absolute/project/deploy/.env
```

It stops web/worker, verifies a cold backup and separate key, then restarts only services that were previously running. An unsuccessful backup or service restart exits nonzero and emits `BACKUP_FAILED`; success emits `BACKUP_OK`. Do not schedule raw copies of live SQLite/Chroma files or force-kill an active worker to make a copy.

This is a maintenance window: unfinished work may pause for explicit retry when the worker stops. Choose and communicate the final schedule before enabling it.

After reviewing the host-specific templates, the host operator can install them in `/etc/systemd/system/`, run `systemctl daemon-reload`, then `systemctl enable --now claim-verifier-backup.timer`. Verify `systemctl list-timers claim-verifier-backup.timer` and inspect each run with `journalctl -u claim-verifier-backup.service`. These install commands have **not** been run here.

Systemd/journal exit state is available for the host's chosen monitoring service. Connect a real failure notification destination before treating failure alerts as complete. No email/webhook has been configured or sent from this session.

## Retention without accidental deletion

Select a retention period with the host/storage decision. The command below is an example **preview** for 14 days:

```powershell
python scripts/backup_restore.py retention "ARCHIVE_ROOT_DIRECTORY" --days 14
```

It verifies archives, always retains at least the two newest valid snapshots, and lists older candidates. Unverified archives are listed separately for investigation. It deletes **zero files**, including keys. Review a real inventory and confirm the off-host restore before approving any cleanup policy. No automatic pruning is enabled by this update; monitor storage growth.

## Acceptance to finish after host/storage selection

Record the active scheduler, last successful archive/hash verification, separately protected matching key, confirmed remote copy, isolated restore, approved retention policy and delivered failure notification. The existing workspace backup is preserved; this preparation does not claim off-host protection is active.
