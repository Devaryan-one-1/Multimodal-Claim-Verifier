"""Cold backup and isolated restore drills against real temporary SQLite/files."""
import base64
from contextlib import closing, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/"scripts/backup_restore.py"
spec = importlib.util.spec_from_file_location("backup_ops_test", SCRIPT)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
KEY = base64.urlsafe_b64encode(b"x"*32).decode()


@contextmanager
def connection(path):
    with closing(sqlite3.connect(path)) as c:
        with c:
            yield c


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base/"project"
        (self.project/"data").mkdir(parents=True)
        (self.project/"data/report.pdf").write_bytes(b"SOURCE_PDF_FIXTURE")
        self.customer = self.project/"customer_data"
        self.customer.mkdir()
        self.control = self.customer/"control.sqlite3"
        with connection(self.control) as c:
            c.executescript("CREATE TABLE jobs(status TEXT); CREATE TABLE model_connections(secret TEXT);")
        self.archive, self.destination = self.base/"backup", self.base/"restore"
        self.addCleanup(patch.stopall)
        self.protection = patch.object(ops, "protect").start()
        patch.object(ops, "PROJECT", self.project).start()
        patch.dict(os.environ, {"CUSTOMER_KEY_ENCRYPTION_KEY": "", "CUSTOMER_DATA_ROOT": str(self.customer)}).start()

    def backup(self, **kwargs):
        return ops.create_backup(self.project, self.archive, services_stopped=True, **kwargs)

    def seed_key(self):
        self.key_file = self.project/".env"
        self.key_file.write_text("UNRELATED_SECRET=DO_NOT_COPY\nCUSTOMER_KEY_ENCRYPTION_KEY="+KEY+"\n", encoding="utf-8")
        with connection(self.control) as c:
            c.execute("INSERT INTO model_connections VALUES('opaque-ciphertext')")

    def test_backup_restore_preserves_files_and_database_contents(self):
        with connection(self.control) as c:
            c.execute("INSERT INTO jobs VALUES('paused')")
        manifest = self.backup()
        ops.restore(self.archive, self.destination)
        self.assertEqual((self.destination/"data/report.pdf").read_bytes(), b"SOURCE_PDF_FIXTURE")
        with connection(self.destination/"customer_data/control.sqlite3") as c:
            self.assertEqual(c.execute("SELECT status FROM jobs").fetchall(), [("paused",)])
        self.assertEqual(len(manifest["files"]), 2)
        self.assertNotIn("worker.lock", json.dumps(manifest))
        self.protection.assert_any_call(self.archive, directory=True)

    def test_requires_stopped_services_assertion_and_rejects_running_jobs(self):
        with self.assertRaises(ValueError):
            ops.create_backup(self.project, self.archive)
        with connection(self.control) as c:
            c.execute("INSERT INTO jobs VALUES('running')")
        with self.assertRaises(RuntimeError):
            self.backup()
        self.assertFalse(self.archive.exists())

    def test_customer_migration_excludes_the_internal_corpus(self):
        self.backup()
        restored = ops.restore(self.archive, self.destination, customer_only=True)
        self.assertTrue((self.destination/"customer_data/control.sqlite3").is_file())
        self.assertFalse((self.destination/"data").exists())
        self.assertTrue(all(row["path"].startswith("customer_data/") for row in restored["files"]))

    def test_real_worker_lock_prevents_concurrent_backup(self):
        with ops.stopped_worker(self.customer):
            with self.assertRaises(RuntimeError):
                self.backup()

    def test_wal_snapshot_keeps_committed_queued_jobs_without_sidecar_files(self):
        with connection(self.control) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("INSERT INTO jobs VALUES('queued')")
            c.commit()
            self.backup()
            ops.restore(self.archive, self.destination)
        with connection(self.destination/"customer_data/control.sqlite3") as c:
            self.assertEqual(c.execute("SELECT status FROM jobs").fetchone()[0], "queued")
        self.assertFalse(any(p.name.endswith(("-wal", "-shm")) for p in self.archive.rglob("*")))

    def test_hash_tampering_prevents_restore_before_destination_is_created(self):
        self.backup()
        (self.archive/"data/report.pdf").write_bytes(b"CHANGED")
        with self.assertRaises(ValueError):
            ops.restore(self.archive, self.destination)
        self.assertFalse(self.destination.exists())

    def test_existing_restore_destination_is_never_overwritten(self):
        self.backup()
        self.destination.mkdir()
        sentinel = self.destination/"keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(ValueError):
            ops.restore(self.archive, self.destination)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_backup_cannot_be_created_inside_live_data(self):
        with self.assertRaises(ValueError):
            ops.create_backup(self.project, self.project/"data/new-backup", services_stopped=True)

    def test_manifest_path_traversal_is_rejected(self):
        self.backup()
        path = self.archive/"manifest.json"
        manifest = json.loads(path.read_text())
        for unsafe in ("../outside", "/etc/passwd", "data/../../outside", "data\\outside"):
            with self.subTest(path=unsafe):
                manifest["files"][0]["path"] = unsafe
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    ops.verify(self.archive)

    def test_saved_connections_require_the_matching_separately_preserved_key(self):
        self.seed_key()
        secret = self.base/"protected/master.key"
        manifest = self.backup(key_file=self.key_file, secret_output=secret)
        self.assertEqual(secret.read_text().strip(), KEY)
        self.assertNotIn(KEY, json.dumps(manifest))
        self.assertFalse((self.archive/".env").exists())
        with self.assertRaises(ValueError):
            ops.restore(self.archive, self.destination)
        ops.restore(self.archive, self.destination, key_file=secret)
        self.assertTrue((self.destination/"customer_data/control.sqlite3").is_file())

    def test_saved_connections_reject_a_missing_or_wrong_key(self):
        self.seed_key()
        with self.assertRaises(ValueError):
            self.backup()
        self.backup(key_file=self.key_file)
        wrong = self.base/"wrong.key"
        wrong.write_text(base64.urlsafe_b64encode(b"y"*32).decode())
        with self.assertRaises(ValueError):
            ops.restore(self.archive, self.destination, key_file=wrong)

    def test_key_is_not_written_if_permission_restriction_fails(self):
        self.seed_key()
        secret = self.base/"protected/master.key"
        def protect(path, directory=False):
            if not directory:
                raise PermissionError("ACL fixture")
        self.protection.side_effect = protect
        with self.assertRaises(PermissionError):
            self.backup(key_file=self.key_file, secret_output=secret)
        self.assertEqual(secret.read_bytes(), b"")
        self.assertFalse((self.archive/"manifest.json").exists())

    def test_duplicate_manifest_entries_are_rejected(self):
        self.backup()
        path = self.archive/"manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"].append(dict(manifest["files"][0]))
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            ops.verify(self.archive)

    def test_verified_archive_copy_can_be_restored_without_touching_live_data(self):
        self.backup()
        copy = self.base/"external-copy"
        copied = ops.copy_verified(self.archive, copy)
        self.assertEqual(copied, ops.verify(copy))
        ops.restore(copy, self.destination)
        self.assertEqual((self.destination/"data/report.pdf").read_bytes(), b"SOURCE_PDF_FIXTURE")
        self.assertEqual((self.project/"data/report.pdf").read_bytes(), b"SOURCE_PDF_FIXTURE")

    def test_archive_copy_refuses_existing_or_nested_destinations_and_tampering(self):
        self.backup()
        for destination in (self.archive, self.archive/"nested", self.project/"data/copy"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                ops.copy_verified(self.archive, destination)
        (self.archive/"data/report.pdf").write_bytes(b"TAMPERED")
        with self.assertRaises(ValueError):
            ops.copy_verified(self.archive, self.base/"external")
        self.assertFalse((self.base/"external").exists())

    def test_key_copy_is_separate_and_matching_without_exposing_it_in_manifest(self):
        self.seed_key(); self.backup(key_file=self.key_file)
        copy, secret = self.base/"external", self.base/"separate/external.key"
        with self.assertRaises(ValueError):
            ops.copy_verified(self.archive, copy, key_file=self.key_file, secret_output=copy/"master.key")
        ops.copy_verified(self.archive, copy, key_file=self.key_file, secret_output=secret)
        self.assertEqual(secret.read_text().strip(), KEY)
        self.assertNotIn(KEY, (copy/"manifest.json").read_text())
        ops.restore(copy, self.destination, key_file=secret)

    def test_retention_only_previews_and_keeps_two_newest_valid_archives(self):
        self.backup()
        archives = self.base/"archives"
        archives.mkdir()
        now = datetime.now(timezone.utc)
        copies = []
        for age in (40, 30, 20):
            copy = archives/f"beta-{age}"
            ops.copy_verified(self.archive, copy)
            manifest = json.loads((copy/"manifest.json").read_text())
            manifest["created_utc"] = (now-timedelta(days=age)).isoformat()
            (copy/"manifest.json").write_text(json.dumps(manifest))
            copies.append(copy)
        preview = ops.retention_preview(archives, 14, now=now)
        self.assertEqual(preview["candidates"], [str(copies[0])])
        self.assertEqual(len(preview["retained"]), 2)
        self.assertEqual(preview["files_deleted"], 0)
        self.assertTrue(all(path.exists() for path in copies))
        (copies[1]/"data/report.pdf").write_bytes(b"TAMPERED")
        preview = ops.retention_preview(archives, 14, now=now)
        self.assertEqual(preview["candidates"], [])
        self.assertEqual(preview["unverified_do_not_remove"], [str(copies[1])])


if __name__ == "__main__":
    unittest.main()
