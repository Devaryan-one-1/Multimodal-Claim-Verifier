"""Beta disclosure, safe feedback links and read-only monitoring controls."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "app"))
from pilot import beta_information
from pilot.cloud_db import Row
from deploy.preflight import configuration_errors
from scripts import beta_status
import test_customer_beta as fixtures


class BetaInformationTests(unittest.TestCase):
    def test_feedback_allows_https_discord_and_encodes_email(self):
        url = "https://discord.gg/beta-invite"
        self.assertEqual(beta_information.feedback_target({"BETA_FEEDBACK_URL": url}), url)
        target = beta_information.feedback_target({"BETA_SUPPORT_EMAIL": "beta+support@example.com"})
        self.assertTrue(target.startswith("mailto:beta+support@example.com?subject="))
        self.assertNotIn("#", beta_information.feedback_target({"BETA_SUPPORT_EMAIL": "beta#support@example.com"}))

    def test_unsafe_feedback_links_are_not_renderable(self):
        for value in ("javascript:alert(1)", "http://discord.gg/invite", "https://user:secret@example.com/help",
                      "https://example.com/\nprivate", "https://example.com/#fragment", "https://[invalid"):
            with self.subTest(value=value):
                self.assertIsNone(beta_information.feedback_target({"BETA_FEEDBACK_URL": value}))
        self.assertIsNone(beta_information.feedback_target({"BETA_SUPPORT_EMAIL": "support@example.com\r\nBcc:other@example.com"}))

    def test_disclosures_explain_providers_and_retained_snapshots_without_a_fake_contact(self):
        st = fixtures.StreamlitFixture()
        with patch.dict(os.environ, {"BETA_FEEDBACK_URL": "", "BETA_SUPPORT_EMAIL": ""}):
            beta_information.render(st)
        text = st.rendered_text()
        for phrase in ("Supabase", "OpenRouter", "Google", "no automatic expiry", "Saved review snapshots",
                       "Negated numerical claims", "cross-document comparisons", "private support", "invited you"):
            self.assertIn(phrase, text)
        self.assertFalse(any(name == "link_button" for name, _, _ in st.events))

    def test_real_feedback_link_is_available_from_settings_connection_panel(self):
        case = fixtures.CustomerReviewScreenTests()
        case.setUp()
        try:
            with patch.dict(os.environ, {"BETA_FEEDBACK_URL": "https://discord.gg/beta-invite"}):
                case.ui.connection_panel(case.access, "fixture")
            self.assertIn("Beta information & feedback", case.st.rendered_text("expander"))
            links = [args[1] for name, args, _ in case.st.events if name == "link_button"]
            self.assertIn("https://discord.gg/beta-invite", links)
        finally:
            case.doCleanups()

    def test_production_requires_a_real_contact_but_local_mode_can_remain_empty(self):
        env = {"BETA_PRODUCTION": "1", "BETA_FEEDBACK_URL": "", "BETA_SUPPORT_EMAIL": ""}
        self.assertTrue(any("BETA_FEEDBACK_URL" in message for message in configuration_errors(env)))
        env["BETA_FEEDBACK_URL"] = "https://discord.gg/beta-invite"
        self.assertFalse(any("BETA_FEEDBACK_URL" in message for message in configuration_errors(env)))
        self.assertFalse(any("BETA_FEEDBACK_URL" in message for message in configuration_errors({})))


class BetaStatusTests(unittest.TestCase):
    def connection(self):
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.executescript("CREATE TABLE worker_status(id INTEGER,heartbeat REAL); CREATE TABLE jobs(status TEXT,created REAL,updated REAL);")
        return c

    def test_healthy_snapshot_is_aggregated_and_does_not_write(self):
        c = self.connection()
        c.execute("INSERT INTO worker_status VALUES(1,9999)")
        c.execute("INSERT INTO jobs VALUES('complete',9000,9990)")
        before = c.total_changes
        report = beta_status.snapshot(c, now=10000)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["worker_age_seconds"], 1)
        self.assertEqual(report["job_counts"]["complete"], 1)
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(c.total_changes, before)

    def test_stale_worker_queued_running_and_failed_work_require_attention(self):
        c = self.connection()
        c.execute("INSERT INTO worker_status VALUES(1,9000)")
        c.executemany("INSERT INTO jobs VALUES(?,?,?)", [("queued",9000,9000), ("running",8000,8000), ("failed",9000,9990), ("paused",9000,9990)])
        with patch.dict(os.environ, {"CUSTOMER_JOB_TIMEOUT_SECONDS": "1800"}):
            report = beta_status.snapshot(c, now=10000)
        self.assertEqual(report["status"], "attention")
        self.assertEqual(report["failed_or_paused_last_24_hours"], 2)
        self.assertEqual(len(report["warnings"]), 4)

    def test_missing_local_database_is_never_created(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CUSTOMER_STORAGE": "local", "CUSTOMER_DATA_ROOT": temp}):
            with self.assertRaises(sqlite3.OperationalError):
                with beta_status.connection():
                    self.fail("A missing database must not open")
            self.assertFalse((Path(temp) / "control.sqlite3").exists())

    def test_local_connection_enforces_read_only(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CUSTOMER_STORAGE": "local", "CUSTOMER_DATA_ROOT": temp}):
            c = sqlite3.connect(Path(temp) / "control.sqlite3")
            c.execute("CREATE TABLE marker(value TEXT)")
            c.close()
            with beta_status.connection() as read_only:
                with self.assertRaises(sqlite3.OperationalError):
                    read_only.execute("INSERT INTO marker VALUES('must not write')")

    def test_cloud_connection_sets_read_only_before_monitor_queries(self):
        c = MagicMock()
        @contextmanager
        def cloud_db():
            yield c
        with patch.dict(os.environ, {"CUSTOMER_STORAGE": "supabase"}), patch("pilot.cloud_db.db", cloud_db):
            with beta_status.connection() as actual:
                self.assertIs(actual, c)
        c.execute.assert_called_once_with("SET TRANSACTION READ ONLY")

    def test_cloud_dict_rows_preserve_actual_counts(self):
        c = MagicMock()
        c.execute.side_effect = [types.SimpleNamespace(fetchone=lambda: Row(heartbeat=9999)),
            types.SimpleNamespace(fetchall=lambda: [Row(status="complete", count=7)]),
            types.SimpleNamespace(fetchone=lambda: Row(oldest=None)),
            types.SimpleNamespace(fetchone=lambda: Row(oldest=None)),
            types.SimpleNamespace(fetchone=lambda: Row(count=0))]
        self.assertEqual(beta_status.snapshot(c, now=10000)["job_counts"]["complete"], 7)

    def test_unavailable_storage_never_prints_private_error_details(self):
        output = io.StringIO()
        with patch.dict(sys.modules, {"dotenv": types.SimpleNamespace(load_dotenv=lambda *a, **kw: None)}), patch.object(beta_status, "connection", side_effect=RuntimeError("PRIVATE_KEY_AND_HOST")), patch("sys.stdout", output):
            self.assertEqual(beta_status.main(), 1)
        self.assertNotIn("PRIVATE_KEY_AND_HOST", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["code"], "monitor_storage_unavailable")


if __name__ == "__main__":
    unittest.main()
