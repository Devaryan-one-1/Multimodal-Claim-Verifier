"""Runtime installer/snapshot/permission guards using isolated metadata/files."""
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from deploy import runtime_hardening as hardening


class RuntimeHardeningTests(unittest.TestCase):
    def test_snapshot_uses_metadata_without_installer_and_preserves_exact_versions(self):
        packages = [types.SimpleNamespace(metadata={"Name": "Pillow"}, version="13.0.0"),
                    types.SimpleNamespace(metadata={"Name": "Torch"}, version="2.14.0+cpu")]
        with tempfile.TemporaryDirectory() as temp, patch.object(hardening.metadata, "distributions", return_value=packages):
            snapshot = Path(temp)/"requirements.txt"
            hardening.write_snapshot(snapshot)
            self.assertEqual(snapshot.read_text(), "pillow==13.0.0\ntorch==2.14.0+cpu\n")

    def test_empty_invalid_or_conflicting_distribution_inventory_fails(self):
        invalid = types.SimpleNamespace(metadata={"Name": "bad package"}, version="1")
        first = types.SimpleNamespace(metadata={"Name": "some_package"}, version="1")
        second = types.SimpleNamespace(metadata={"Name": "some-package"}, version="2")
        for packages in ([], [invalid], [first, second]):
            with self.subTest(packages=packages), patch.object(hardening.metadata, "distributions", return_value=packages):
                self.assertRaises(RuntimeError, hardening.installed_requirements)

    def test_no_installer_and_exact_snapshot_pass_with_clean_permissions(self):
        packages = {"torch": "2.14.0+cpu", "setuptools": "84.1.0"}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(hardening, "installed_requirements", return_value=packages), \
                patch.object(hardening, "find_spec", return_value=None), \
                patch.object(hardening.shutil, "which", return_value=None), \
                patch.object(hardening, "privileged_files", return_value=[]):
            snapshot = Path(temp)/"requirements.txt"
            snapshot.write_text(hardening.requirements_text(packages))
            result = hardening.check_runtime(snapshot, [temp])
        self.assertTrue(result["pip_absent"])
        self.assertTrue(result["privilege_bits_cleared"])
        self.assertEqual(result["packages"], 2)

    def test_pip_distribution_module_or_executable_each_block_the_runtime(self):
        cases = [({"pip": "26.3"}, None, None),
                 ({"setuptools": "84.1.0"}, object(), None),
                 ({"setuptools": "84.1.0"}, None, "/usr/local/bin/pip3")]
        for packages, module, command in cases:
            with self.subTest(command=command, module=module), \
                    patch.object(hardening, "installed_requirements", return_value=packages), \
                    patch.object(hardening, "find_spec", return_value=module), \
                    patch.object(hardening.shutil, "which", return_value=command):
                self.assertRaisesRegex(RuntimeError, "pip", hardening.check_runtime)

    def test_missing_or_stale_snapshot_does_not_silently_pass(self):
        packages = {"torch": "2.14.0+cpu"}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(hardening, "installed_requirements", return_value=packages), \
                patch.object(hardening, "find_spec", return_value=None), \
                patch.object(hardening.shutil, "which", return_value=None):
            snapshot = Path(temp)/"requirements.txt"
            self.assertRaises(OSError, hardening.check_runtime, snapshot)
            snapshot.write_text("torch==2.13.0\n")
            self.assertRaisesRegex(RuntimeError, "snapshot", hardening.check_runtime, snapshot)

    def test_setuid_and_setgid_files_are_detected_without_changing_them(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/"fixture-program"
            file.write_text("public fixture")
            for bit in (stat.S_ISUID, stat.S_ISGID):
                with self.subTest(bit=bit), patch.object(Path, "lstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFREG|bit|0o755)):
                    self.assertEqual(hardening.privileged_files([temp]), [str(file)])
            self.assertEqual(file.read_text(), "public fixture")

    def test_unreadable_system_tree_cannot_be_reported_as_clean(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(hardening.os, "walk", side_effect=PermissionError("fixture")):
            self.assertRaises(PermissionError, hardening.privileged_files, [temp])

    def test_privilege_findings_block_an_otherwise_matching_runtime(self):
        packages = {"setuptools": "84.1.0"}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(hardening, "installed_requirements", return_value=packages), \
                patch.object(hardening, "find_spec", return_value=None), \
                patch.object(hardening.shutil, "which", return_value=None), \
                patch.object(hardening, "privileged_files", return_value=["/usr/bin/fixture"]):
            snapshot = Path(temp)/"requirements.txt"
            snapshot.write_text(hardening.requirements_text(packages))
            self.assertRaisesRegex(RuntimeError, "Setuid/setgid", hardening.check_runtime, snapshot)


if __name__ == "__main__":
    unittest.main()
