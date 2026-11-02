"""Live customer progress checks using temporary data; no provider requests."""
from contextlib import ExitStack
import copy
import json
import os
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

from test_customer_beta import APP, StreamlitFixture, RerunPage, load_module
from pilot import store


class ProgressSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": folder}))
        store.initialize()
        self.access = store.Access("owner", "a"*32, time.time()+300)
        self.other = store.Access("other", "b"*32, time.time()+300)
        with store.db() as c:
            for access in (self.access, self.other):
                c.execute("INSERT INTO workspaces VALUES(?,?)", (access.workspace, access.principal))
                c.execute("INSERT INTO members VALUES(?,?)", (access.principal, access.workspace))
            c.execute("INSERT INTO worker_status VALUES(1,?)", (time.time(),))
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      ("c"*32, self.access.workspace, "Report.pdf", "fixture", "queued", time.time()))
        self.add_job("d"*32, self.access, "verify", "running", {"name": "My review", "claim": "private-claim"},
                     {"results": [{"status": "complete", "verdict": "SUPPORTED", "evidence": "private-evidence"},
                                  {"status": "error", "verdict": "UNKNOWN"}]}, {"parsed": [{}, {}, {}]})
        self.add_job("e"*32, self.other, "verify", "running", {"name": "Foreign review"}, {}, {})

    def add_job(self, job_id, access, kind, status, payload, result, checkpoint):
        with store.db() as c:
            c.execute("""INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,checkpoint,created,updated)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (job_id, access.workspace, access.principal, kind, json.dumps(payload), status, "Checking source",
                       json.dumps(result), json.dumps(checkpoint), time.time(), time.time()))

    def test_progress_is_workspace_scoped_and_only_counts_completed_claims(self):
        snapshot = store.progress_snapshot(self.access)
        self.assertEqual(len(snapshot["jobs"]), 1)
        self.assertEqual(snapshot["jobs"][0]["name"], "My review")
        self.assertEqual(snapshot["jobs"][0]["completed"], 1)
        self.assertEqual(snapshot["jobs"][0]["total"], 3)
        self.assertEqual(snapshot["documents"], [("c"*32, "queued")])
        self.assertTrue(snapshot["worker_available"])
        self.assertNotIn("private-claim", json.dumps(snapshot))
        self.assertNotIn("private-evidence", json.dumps(snapshot))
        self.assertEqual(store.progress_snapshot(self.other)["documents"], [])

    def test_processing_uses_document_name_and_snapshot_reads_do_not_modify_jobs(self):
        self.add_job("f"*32, self.access, "process", "queued", {"document_id": "c"*32}, {}, {})
        before = store.get_job(self.access, "f"*32)
        jobs = {j["id"]: j for j in store.progress_snapshot(self.access)["jobs"]}
        self.assertEqual(jobs["f"*32]["name"], "Report.pdf")
        self.assertEqual(before, store.get_job(self.access, "f"*32))

    def test_historical_job_bodies_are_not_loaded(self):
        for status in ("complete", "failed", "paused"):
            with self.subTest(status=status), store.db() as c:
                c.execute("UPDATE jobs SET status=?,payload='not-json',result='not-json',checkpoint='not-json' WHERE id=?",
                          (status, "d"*32))
            job = store.progress_snapshot(self.access)["jobs"][0]
            self.assertEqual(job["status"], status)
            self.assertEqual(job["completed"], 0)

    def test_stale_and_missing_worker_heartbeat_are_unavailable(self):
        with store.db() as c:
            c.execute("UPDATE worker_status SET heartbeat=?", (time.time()-16,))
        self.assertFalse(store.progress_snapshot(self.access)["worker_available"])
        with store.db() as c:
            c.execute("DELETE FROM worker_status")
        self.assertFalse(store.progress_snapshot(self.access)["worker_available"])

    def test_every_poll_checks_membership_and_expiration(self):
        expired = store.Access(self.access.principal, self.access.workspace, time.time()-1)
        with self.assertRaises(PermissionError):
            store.progress_snapshot(expired)
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (self.access.principal,))
        with self.assertRaises(PermissionError):
            store.progress_snapshot(self.access)

    def test_connection_changes_are_scoped_to_the_signed_in_person(self):
        with store.db() as c:
            for access in (self.access, self.other):
                c.execute("INSERT INTO model_connections VALUES(?,?,?,?,?)",
                          (access.principal, access.principal+"-version", "PRIVATE_CIPHERTEXT", "{}", 1))
        before = store.progress_snapshot(self.access)
        self.assertEqual(before["connection"], ("owner-version", 1))
        self.assertNotIn("PRIVATE_CIPHERTEXT", json.dumps(before))
        with store.db() as c:
            c.execute("UPDATE model_connections SET updated=2 WHERE principal=?", (self.other.principal,))
        self.assertEqual(store.progress_snapshot(self.access)["connection"], before["connection"])
        self.assertEqual(store.progress_snapshot(self.other)["connection"], ("other-version", 2))


class LiveProgressScreenTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.st = StreamlitFixture()
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw = MagicMock(), MagicMock()
        self.stack.enter_context(patch.dict("sys.modules", {
            "streamlit": self.st, "pymupdf": types.ModuleType("pymupdf"), "PIL": pil}))
        self.ui = load_module(self.stack, "_customer_ui_progress", APP / "pilot/ui.py")
        self.access = store.Access("owner", "a"*32, time.time()+300)
        self.st.session_state.customer_identity = self.access.principal
        self.snapshot = {"worker_available": True, "documents": [("c"*32, "ready")],
                         "jobs": [{"id": "d"*32, "kind": "verify", "status": "running",
                                   "name": "My review", "stage": "Understanding claims", "completed": 0, "total": 0}]}
        self.read = self.stack.enter_context(patch.object(store, "progress_snapshot", side_effect=lambda _: copy.deepcopy(self.snapshot)))

    def render(self, revision=1):
        self.ui.live_updates(self.access, "fixture_", revision)

    def test_native_fragment_polls_every_two_seconds(self):
        registration = [kwargs for name, _, kwargs in self.st.events if name == "fragment"]
        self.assertEqual([r for r in registration if r.get("run_every")], [{"run_every": 2}])

    def test_running_status_stays_running_and_does_not_invent_percentage(self):
        self.render()
        self.assertIn("Understanding claims", self.st.rendered_text("text"))
        panels = [kwargs for name, _, kwargs in self.st.events if name == "status"]
        self.assertEqual(panels[0]["state"], "running")
        self.assertFalse(any(name == "progress" for name, _, _ in self.st.events))

    def test_stage_updates_do_not_rerun_forms_or_replace_draft_state(self):
        self.st.session_state["draft"] = "unfinished claim"
        self.render()
        self.snapshot["jobs"][0]["stage"] = "Searching evidence"
        self.render()
        self.assertIn("Searching evidence", self.st.rendered_text("text"))
        self.assertEqual(self.st.session_state["draft"], "unfinished claim")
        self.assertFalse(any(name == "rerun" for name, _, _ in self.st.events))

    def test_completed_subclaim_refreshes_results_and_uses_real_progress(self):
        self.render()
        self.snapshot["jobs"][0].update(completed=1, total=3)
        with self.assertRaises(RerunPage):
            self.render()
        self.render(revision=2)
        progress = [(args, kwargs) for name, args, kwargs in self.st.events if name == "progress"]
        self.assertEqual(progress[-1], ((1/3,), {"text": "1 of 3 claims checked"}))
        self.render(revision=2)  # The same update must not cause an endless rerun.

    def test_complete_paused_and_failed_transitions_refresh_without_marking_failed_as_complete(self):
        for status in ("complete", "paused", "failed"):
            with self.subTest(status=status):
                self.st.session_state.clear()
                self.st.session_state.customer_identity = self.access.principal
                self.snapshot["jobs"][0]["status"] = "running"
                self.render()
                self.snapshot["jobs"][0]["status"] = status
                with self.assertRaises(RerunPage):
                    self.render()
                self.st.events.clear()
                self.render(revision=2)
                self.assertFalse(any(name == "status" for name, _, _ in self.st.events))

    def test_ready_source_refreshes_the_source_picker(self):
        self.snapshot["documents"] = [("c"*32, "queued")]
        self.render()
        self.snapshot["documents"] = [("c"*32, "ready")]
        with self.assertRaises(RerunPage):
            self.render()

    def test_connection_removed_in_another_tab_refreshes_submission_controls(self):
        self.snapshot["connection"] = ("saved-version", 1)
        self.render()
        self.snapshot["connection"] = None
        with self.assertRaises(RerunPage):
            self.render()
        self.render(revision=2)

    def test_normal_full_render_cannot_interrupt_a_pending_form_submission(self):
        self.render()
        self.snapshot["jobs"][0]["status"] = "complete"
        self.render(revision=2)
        self.assertFalse(any(name == "rerun" for name, _, _ in self.st.events))

    def test_worker_health_recovers_without_refreshing_the_whole_page(self):
        self.snapshot["worker_available"] = False
        self.render()
        self.assertIn("Processing service unavailable", self.st.rendered_text("warning"))
        self.snapshot["worker_available"] = True
        self.render()
        self.assertIn("Processing service available", self.st.rendered_text("caption"))
        self.assertFalse(any(name == "rerun" for name, _, _ in self.st.events))

    def test_unavailable_worker_shows_waiting_instead_of_a_running_spinner(self):
        self.snapshot.update(worker_available=False, worker_age_seconds=1500)
        self.render()
        self.assertIn("Waiting for processing service", self.st.rendered_text("text"))
        self.assertIn("25 minutes ago", self.st.rendered_text("caption"))
        self.assertNotIn("while work continues", self.st.rendered_text())
        self.assertFalse(any(name == "status" for name, _, _ in self.st.events))

    def test_identity_change_and_expiration_reenter_login_before_reading_progress(self):
        self.st.session_state.customer_identity = "other-person"
        with self.assertRaises(RerunPage):
            self.render()
        self.read.assert_not_called()
        self.st.session_state.customer_identity = self.access.principal
        self.access = store.Access(self.access.principal, self.access.workspace, time.time()-1)
        with self.assertRaises(RerunPage):
            self.render()
        self.read.assert_not_called()

    def test_revoked_membership_reenters_login(self):
        self.read.side_effect = PermissionError("Revoked")
        with self.assertRaises(RerunPage):
            self.render()

    def test_temporary_poll_failure_offers_manual_fallback(self):
        self.read.side_effect = RuntimeError("private backend detail")
        self.render()
        self.assertIn("Use Refresh status", self.st.rendered_text("warning"))
        self.assertNotIn("private backend detail", self.st.rendered_text())


if __name__ == "__main__":
    unittest.main()
