"""Diagnostic evidence boundaries: temporary files and controlled subprocesses."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from deploy import runtime_inventory as inventory


class RuntimeInventoryTests(unittest.TestCase):
    def test_file_evidence_hashes_without_changing_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"public-module.py"
            path.write_bytes(b"public fixture")
            result = inventory.file_info(path, checksum=True)
            self.assertEqual(result["sha256"], hashlib.sha256(b"public fixture").hexdigest())
            self.assertEqual(path.read_bytes(), b"public fixture")
            self.assertFalse(inventory.file_info(Path(temp)/"missing")["present"])

    def test_installed_and_vendored_versions_remain_distinct_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vendor = root/"pip/_vendor"
            (vendor/"pkg_resources").mkdir(parents=True)
            (vendor/"vendor.txt").write_text("# public fixture\nsetuptools==70.3.0\n")
            (vendor/"pkg_resources/__init__.py").write_text("# public fixture")
            def distribution(name):
                if name not in {"pip", "setuptools"}:
                    raise inventory.metadata.PackageNotFoundError(name)
                return types.SimpleNamespace(version="26.3" if name=="pip" else "84.1.0",
                                             locate_file=lambda relative: root/relative)
            with patch.object(inventory.metadata, "distribution", side_effect=distribution):
                report = inventory.python_packages()
            self.assertEqual(report["setuptools"]["version"], "84.1.0")
            self.assertIn("setuptools==70.3.0", report["pip"]["vendored_requirements"])
            components = report["pip"]["vendored_components"]
            self.assertTrue(components[0]["present"])
            self.assertTrue(all(not component["present"] for component in components[1:]))

    def test_package_manager_failure_never_exports_raw_process_output(self):
        secret = "DO_NOT_EXPORT_PRIVATE_VALUE"
        failed = types.SimpleNamespace(returncode=1, stdout=secret, stderr=secret)
        with patch.object(inventory.shutil, "which", return_value="/usr/bin/dpkg-query"), \
                patch.object(inventory.subprocess, "run", return_value=failed):
            report = inventory.os_packages()
        self.assertFalse(report["available"])
        self.assertNotIn(secret, json.dumps(report))

    def test_os_packages_record_binary_and_source_package_versions(self):
        result = types.SimpleNamespace(returncode=0, stdout="libexample1:amd64\t2.1\texample\t2.1-1\n", stderr="")
        with patch.object(inventory.shutil, "which", return_value="/usr/bin/dpkg-query"), \
                patch.object(inventory.subprocess, "run", return_value=result) as run:
            report = inventory.os_packages()
        self.assertEqual(report["packages"][0]["source"], "example")
        self.assertEqual(report["packages"][0]["source_version"], "2.1-1")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_perl_missing_module_and_timeout_are_evidence_without_private_errors(self):
        secret = "DO_NOT_EXPORT_PRIVATE_VALUE"
        missing = types.SimpleNamespace(returncode=2, stdout=secret,
                                        stderr="Can't locate Archive/Tar.pm " + secret)
        with patch.object(inventory.shutil, "which", return_value="/usr/bin/perl"), \
                patch.object(inventory.subprocess, "run", return_value=missing) as run:
            report = inventory.perl_archive_tar()
            self.assertTrue(report["module_missing"])
            self.assertNotIn(secret, json.dumps(report))
            self.assertEqual(set(run.call_args.kwargs["env"]), {"PATH", "LC_ALL"})
        with patch.object(inventory.shutil, "which", return_value="/usr/bin/perl"), \
                patch.object(inventory.subprocess, "run", side_effect=subprocess.TimeoutExpired(secret, 5, output=secret)):
            report = inventory.perl_archive_tar()
        self.assertEqual(report["error_type"], "TimeoutExpired")
        self.assertNotIn(secret, json.dumps(report))

    def test_collection_uses_only_memory_sqlite_and_never_exports_environment_secrets(self):
        secret = "DO_NOT_EXPORT_PRIVATE_VALUE"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": secret, "CUSTOMER_DATABASE_URL": secret}), \
                patch.object(inventory, "python_packages", return_value={}), \
                patch.object(inventory, "os_packages", return_value={"available": False}), \
                patch.object(inventory, "perl_archive_tar", return_value={"interpreter_present": False}), \
                patch.object(inventory.shutil, "which", return_value=None), \
                patch.object(inventory.sqlite3, "connect", wraps=inventory.sqlite3.connect) as connect:
            report = inventory.collect()
        connect.assert_called_once_with(":memory:")
        self.assertFalse(report["applicability_assessed"])
        self.assertIn("not the eventual hosted runtime", report["scope"])
        self.assertNotIn(secret, json.dumps(report))
        self.assertIsInstance(report["sqlite"]["compile_options"], list)

    def test_absent_interpreter_or_package_manager_is_reported_without_execution(self):
        with patch.object(inventory.shutil, "which", return_value=None), \
                patch.object(inventory.subprocess, "run") as run:
            self.assertFalse(inventory.os_packages()["available"])
            self.assertFalse(inventory.perl_archive_tar()["interpreter_present"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
