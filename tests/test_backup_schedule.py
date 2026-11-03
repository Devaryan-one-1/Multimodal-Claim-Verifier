"""Execute the host wrapper with a fake Docker CLI; no engine/data/keys touched."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
SHELL = ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").is_file()
         else shutil.which("bash") or shutil.which("sh"))


@unittest.skipUnless(SHELL, "POSIX shell is needed for the host-wrapper drills")
class BackupScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root/"bin"
        self.bin.mkdir()
        self.log = self.root/"calls.log"
        self.wrapper = self.root/"backup.sh"
        shutil.copyfile(PROJECT/"deploy/backup.sh", self.wrapper)
        fake = self.bin/"docker"
        fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_TEST_LOG"
case " $* " in
  *" ps --status running --services "*) printf '%s\\n' "$DOCKER_TEST_ACTIVE" ;;
  *" run --rm --no-deps backup backup "*) [ "$DOCKER_TEST_FAIL" != backup ] || exit 17 ;;
  *" start worker "*) [ "$DOCKER_TEST_FAIL" != restart ] || exit 18 ;;
esac
exit 0
''', encoding="utf-8", newline="\n")
        fake.chmod(0o700)

    def run_wrapper(self, fail="", active="web\nworker"):
        env = {**os.environ, "PATH": str(self.bin)+os.pathsep+os.environ.get("PATH", ""),
               "DOCKER_TEST_LOG": self.log.as_posix(), "DOCKER_TEST_FAIL": fail, "DOCKER_TEST_ACTIVE": active}
        return subprocess.run([SHELL, "--noprofile", "--norc", str(self.wrapper)] if "bash" in SHELL.lower()
                              else [SHELL, str(self.wrapper)], env=env, capture_output=True, text=True, timeout=15)

    def test_success_verifies_backup_and_restarts_previous_services(self):
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BACKUP_OK", result.stdout)
        calls = self.log.read_text()
        self.assertIn("backup verify", calls)
        self.assertIn("start web", calls)
        self.assertIn("start worker", calls)

    def test_backup_failure_remains_failed_but_services_restart(self):
        result = self.run_wrapper(fail="backup")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertIn("BACKUP_FAILED", result.stderr)
        calls = self.log.read_text()
        self.assertNotIn("backup verify", calls)
        self.assertIn("start web", calls)
        self.assertIn("start worker", calls)

    def test_restart_failure_is_an_operator_visible_failure(self):
        result = self.run_wrapper(fail="restart")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BACKUP_FAILED", result.stderr)

    def test_only_previously_running_services_are_restarted(self):
        result = self.run_wrapper(active="web")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn("start web", calls)
        self.assertNotIn("start worker", calls)


if __name__ == "__main__":
    unittest.main()
