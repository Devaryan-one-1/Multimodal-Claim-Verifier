"""Cold backups and non-overwriting restore drills. Standard library only."""
from __future__ import annotations

import argparse
import base64
import csv
from contextlib import closing, contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
SKIP = {"worker.lock"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            value.update(block)
    return value.hexdigest()


def protect(path, directory=False):
    """Restrict a new empty destination before writing any private content."""
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    result = subprocess.run([str(system/"whoami.exe"), "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, check=True, timeout=10)
    sid = list(csv.reader(result.stdout.splitlines()))[0][1]
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise RuntimeError("The backup owner could not be identified.")
    rights = "(OI)(CI)F" if directory else "F"
    subprocess.run([str(system/"icacls.exe"), str(path), "/inheritance:r", "/grant:r",
                    f"*{sid}:{rights}", f"*S-1-5-18:{rights}"],
                   capture_output=True, check=True, timeout=10)


def read_key(key_file=None):
    key = os.getenv("CUSTOMER_KEY_ENCRYPTION_KEY", "").strip()
    if not key and key_file and Path(key_file).is_file():
        for line in Path(key_file).read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.removeprefix("export ").partition("=")
            if separator and name.strip() == "CUSTOMER_KEY_ENCRYPTION_KEY":
                key = value.strip().strip("\"'")
                break
        else:
            # A separate key backup is a single base64 line, not a whole .env.
            value = Path(key_file).read_text(encoding="utf-8-sig").strip()
            if "\n" not in value and "=" not in value.rstrip("="):
                key = value
    if key:
        try:
            if len(base64.urlsafe_b64decode(key)) != 32:
                raise ValueError()
        except Exception:
            raise ValueError("The encryption master key is invalid.") from None
    return key


@contextmanager
def stopped_worker(customer):
    if not customer.exists():
        yield
        return
    with (customer/"worker.lock").open("a+b") as handle:
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Stop the worker before creating a backup.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def read_db(path):
    return sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=10)


def check_sqlite(path):
    with closing(read_db(path)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("A database failed its integrity check.")


def file_target(root, relative):
    parts = PurePosixPath(relative)
    if not relative or "\\" in relative or parts.is_absolute() or ".." in parts.parts or parts.parts[0] not in {"data", "customer_data"}:
        raise ValueError("Unsafe backup manifest path.")
    target = root.joinpath(*parts.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("A backup path escapes its root.")
    return target


def verify(archive):
    archive = Path(archive).resolve()
    manifest = json.loads((archive/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported backup manifest.")
    seen = set()
    for record in manifest["files"]:
        relative = record["path"]
        if relative in seen:
            raise ValueError("Duplicate backup manifest path.")
        seen.add(relative)
        path = file_target(archive, relative)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record["size"] or digest(path) != record["sha256"]:
            raise ValueError("A backup file is missing or does not match its recorded hash.")
        if record["sqlite"]:
            check_sqlite(path)
    return manifest


def create_backup(project, destination, *, services_stopped=False, customer_root=None, key_file=None, secret_output=None):
    storage_mode=os.getenv("CUSTOMER_STORAGE")
    if storage_mode is None:
        config=Path(project)/".env"
        if config.is_file():
            for line in config.read_text(encoding="utf-8-sig").splitlines():
                name,separator,value=line.removeprefix("export ").partition("=")
                if separator and name.strip()=="CUSTOMER_STORAGE":
                    storage_mode=value.strip().strip("\"'")
    if (storage_mode or "local").strip().lower() != "local":
        raise ValueError("Cloud storage is active. Back up Postgres and the private bucket; a local cache backup does not protect cloud customer data.")
    project, destination = Path(project).resolve(), Path(destination).resolve()
    roots = {"data": project/"data", "customer_data": Path(customer_root or os.getenv("CUSTOMER_DATA_ROOT", str(project/"customer_data"))).resolve()}
    if not services_stopped:
        raise ValueError("Stop the apps and worker, then pass --services-stopped.")
    if destination.exists() or any(destination.is_relative_to(root.resolve()) for root in roots.values()):
        raise ValueError("Use a new backup directory outside the data folders.")
    if not any(root.exists() for root in roots.values()):
        raise ValueError("No data folders were found.")
    key = read_key(key_file)
    with ExitStack() as stack:
        stack.enter_context(stopped_worker(roots["customer_data"]))
        control = roots["customer_data"]/"control.sqlite3"
        connections = 0
        if control.exists():
            # Fence customer uploads/context writes during the cold file copy.
            fence = sqlite3.connect(control, timeout=10)
            stack.callback(fence.close)
            fence.execute("BEGIN IMMEDIATE")
            if fence.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]:
                raise RuntimeError("Finish or pause running work before backing up.")
            if fence.execute("SELECT 1 FROM sqlite_master WHERE name='model_connections'").fetchone():
                connections = fence.execute("SELECT count(*) FROM model_connections").fetchone()[0]
        if connections and not key:
            raise ValueError("The matching encryption master key is required for saved connections.")
        destination.mkdir(parents=True)
        protect(destination, directory=True)
        if secret_output:
            secret_output = Path(secret_output).resolve()
            if not key or secret_output.exists() or secret_output.is_relative_to(destination) or any(secret_output.is_relative_to(root) for root in roots.values()):
                raise ValueError("Use a new separate protected file for the master-key backup.")
            secret_output.parent.mkdir(parents=True, exist_ok=True)
            with secret_output.open("xb"):
                pass
            protect(secret_output)
            with secret_output.open("wb") as stream:
                stream.write((key+"\n").encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
        records = []
        for name, root in roots.items():
            if not root.exists():
                continue
            if root.is_symlink():
                raise ValueError("Data-folder symlinks are not accepted.")
            for source in sorted(root.rglob("*")):
                if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
                    raise ValueError("A source path escapes its data folder.")
                if not source.is_file() or source.name in SKIP or source.name.endswith(("-wal", "-shm")):
                    continue
                relative = name+"/"+source.relative_to(root).as_posix()
                target = file_target(destination, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as stream:
                    sqlite = stream.read(16) == b"SQLite format 3\x00" or source.suffix == ".sqlite3"
                if sqlite:
                    with closing(read_db(source)) as original, closing(sqlite3.connect(target)) as copied:
                        original.backup(copied)
                        copied.execute("PRAGMA journal_mode=DELETE")
                        copied.commit()
                    check_sqlite(target)
                else:
                    shutil.copy2(source, target)
                    if digest(source) != digest(target):
                        raise ValueError("A copied file does not match its source.")
                records.append({"path": relative, "size": target.stat().st_size, "sha256": digest(target), "sqlite": sqlite})
        manifest = {"format": 1, "created_utc": datetime.now(timezone.utc).isoformat(), "files": records,
                    "saved_connections": connections, "key_sha256": hashlib.sha256(key.encode()).hexdigest() if key else None}
        (destination/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verify(destination)
    return manifest


def restore(archive, destination, *, key_file=None, customer_only=False):
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    manifest = verify(archive)
    live = (PROJECT/"data", Path(os.getenv("CUSTOMER_DATA_ROOT", str(PROJECT/"customer_data"))).resolve())
    if (destination.exists() or destination.is_relative_to(archive) or PROJECT.is_relative_to(destination)
            or any(destination.is_relative_to(root) for root in live)):
        raise ValueError("Restore requires a new, isolated directory; existing paths are never overwritten.")
    key = read_key(key_file)
    if manifest.get("saved_connections") and (not key or hashlib.sha256(key.encode()).hexdigest() != manifest["key_sha256"]):
        raise ValueError("The matching encryption master key is required for this restore.")
    selected = [r for r in manifest["files"] if not customer_only or r["path"].startswith("customer_data/")]
    if not selected:
        raise ValueError("The backup has no files for the selected restore scope.")
    destination.mkdir(parents=True)
    protect(destination, directory=True)
    for record in selected:
        source, target = file_target(archive, record["path"]), file_target(destination, record["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != record["sha256"]:
            raise ValueError("A restored file failed hash verification.")
        if record["sqlite"]:
            check_sqlite(target)
    return {**manifest, "files": selected}


def copy_verified(archive, destination, *, key_file=None, secret_output=None):
    """Copy an immutable archive for off-host transfer; never copy live data."""
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    manifest = verify(archive)
    live = (PROJECT/"data", Path(os.getenv("CUSTOMER_DATA_ROOT", str(PROJECT/"customer_data"))).resolve())
    if (destination.exists() or destination.is_relative_to(archive) or archive.is_relative_to(destination)
            or PROJECT.is_relative_to(destination) or any(destination.is_relative_to(root) for root in live)):
        raise ValueError("Use a new archive-copy directory outside live data and the source backup.")
    key = read_key(key_file) if secret_output else ""
    if secret_output:
        secret_output = Path(secret_output).resolve()
        if (not key or not manifest.get("key_sha256") or hashlib.sha256(key.encode()).hexdigest() != manifest["key_sha256"]
                or secret_output.exists() or secret_output.is_relative_to(destination) or secret_output.is_relative_to(archive)
                or any(secret_output.is_relative_to(root) for root in live)):
            raise ValueError("Use the matching master key and a new separate protected key destination.")
    destination.mkdir(parents=True)
    protect(destination, directory=True)
    for record in manifest["files"]:
        target = file_target(destination, record["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_target(archive, record["path"]), target)
        if digest(target) != record["sha256"]:
            raise ValueError("The archive copy failed hash verification.")
    (destination/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    verify(destination)
    if secret_output:
        secret_output.parent.mkdir(parents=True, exist_ok=True)
        with secret_output.open("xb"):
            pass
        protect(secret_output)
        with secret_output.open("wb") as stream:
            stream.write((key+"\n").encode("ascii")); stream.flush(); os.fsync(stream.fileno())
        if secret_output.read_text(encoding="ascii").strip() != key:
            raise ValueError("The separate key copy failed verification.")
    return manifest


def retention_preview(archive_root, days, *, now=None):
    """Read-only retention plan. Always retain the two newest valid archives."""
    if days < 1:
        raise ValueError("Retention days must be positive.")
    archive_root = Path(archive_root).resolve()
    if not archive_root.is_dir():
        raise ValueError("Choose an existing backup archive directory.")
    cutoff = (now or datetime.now(timezone.utc)).timestamp()-days*86400
    valid, unverified = [], []
    for path in sorted(archive_root.iterdir()):
        if path.is_symlink() or not path.is_dir() or not (path/"manifest.json").is_file():
            continue
        try:
            manifest = verify(path)
            created = datetime.fromisoformat(manifest["created_utc"])
            if created.tzinfo is None:
                raise ValueError()
            valid.append((created.timestamp(), str(path)))
        except Exception:
            unverified.append(str(path))
    valid.sort(reverse=True)
    candidates = [path for stamp, path in valid[2:] if stamp < cutoff]
    return {"mode": "preview_only", "retention_days": days, "minimum_retained": 2,
            "candidates": candidates, "retained": [path for _, path in valid if path not in candidates],
            "unverified_do_not_remove": unverified, "files_deleted": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--project", type=Path, default=PROJECT)
    backup.add_argument("--customer-root", type=Path)
    backup.add_argument("--services-stopped", action="store_true")
    backup.add_argument("--key-file", type=Path, default=PROJECT/".env")
    backup.add_argument("--secret-output", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("archive", type=Path)
    drill = commands.add_parser("restore")
    drill.add_argument("archive", type=Path)
    drill.add_argument("destination", type=Path)
    drill.add_argument("--key-file", type=Path)
    drill.add_argument("--customer-only", action="store_true")
    transfer = commands.add_parser("copy", help="Copy an already verified archive; no live-service interruption needed.")
    transfer.add_argument("archive", type=Path)
    transfer.add_argument("destination", type=Path)
    transfer.add_argument("--key-file", type=Path)
    transfer.add_argument("--secret-output", type=Path)
    retention = commands.add_parser("retention", help="Preview only; never deletes archives or keys.")
    retention.add_argument("archive_root", type=Path)
    retention.add_argument("--days", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "retention":
            print(json.dumps(retention_preview(args.archive_root, args.days)))
            return
        if args.command == "backup":
            result = create_backup(args.project, args.destination, services_stopped=args.services_stopped,
                                   customer_root=args.customer_root, key_file=args.key_file, secret_output=args.secret_output)
        elif args.command == "verify":
            result = verify(args.archive)
        elif args.command == "copy":
            result = copy_verified(args.archive, args.destination, key_file=args.key_file, secret_output=args.secret_output)
        else:
            result = restore(args.archive, args.destination, key_file=args.key_file, customer_only=args.customer_only)
        print(json.dumps({"status": "verified", "files": len(result["files"]), "bytes": sum(r["size"] for r in result["files"])}))
    except Exception as exc:
        print(f"{type(exc).__name__}: Backup/restore did not finish. {str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Check file permissions and service state.'}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
