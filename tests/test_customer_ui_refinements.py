"""Customer beta interface regressions; synthetic keys, no model or email calls."""
from contextlib import ExitStack
import json
import time
import types
import unittest
from unittest.mock import MagicMock, patch

from test_customer_beta import APP, StreamlitFixture, RerunPage, load_module
from pilot import store


class CustomerInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.st = StreamlitFixture()
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw = MagicMock(), MagicMock()
        self.stack.enter_context(patch.dict("sys.modules", {
            "streamlit": self.st, "pymupdf": types.ModuleType("pymupdf"), "PIL": pil}))
        self.ui = load_module(self.stack, "_customer_ui_refinements", APP / "pilot/ui.py")
        self.access = store.Access("owner", "a"*32, time.time()+300)
        self.st.session_state.customer_identity = self.access.principal
        self.saved = {"version": "saved-version", "models": {"text_model": "vendor/saved", "vision_model": "vendor/vision"}}
        self.metadata = self.stack.enter_context(patch.object(self.ui.model_connections, "metadata", return_value=self.saved))
        self.submit = self.stack.enter_context(patch.object(store, "submit_review", return_value="b"*32))

    def job(self, **updates):
        job = {"id": "b"*32, "principal": "owner", "kind": "verify", "status": "paused", "attempts": 1,
               "stage": "Verification failed", "payload": json.dumps({"name": "Review", "claim": "Original claim",
               "documents": ["c"*32], "openrouter": {"connection_version": "saved-version", "models": self.saved["models"]}}),
               "result": "{}"}
        job.update(updates)
        return job

    def test_claim_draft_survives_widget_cleanup_without_submitting(self):
        self.st.inputs.update({"Review name": "Unfinished review", "Claims to check": "Unfinished claim"})
        self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.st.inputs.clear()
        del self.st.session_state["a_claim"]
        del self.st.session_state["a_review_name"]
        self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.assertEqual(self.st.session_state["a_claim"], "Unfinished claim")
        self.assertEqual(self.st.session_state["a_review_name"], "Unfinished review")
        self.submit.assert_not_called()

    def test_drafts_are_workspace_scoped(self):
        self.st.inputs["Claims to check"] = "Workspace A claim"
        self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.st.inputs.clear()
        self.ui.review_composer(self.access, "b_", ["c"*32], True)
        self.assertEqual(self.st.session_state["b_claim"], "")
        self.assertEqual(self.st.session_state["a_claim"], "Workspace A claim")

    def test_clear_draft_does_not_touch_saved_work(self):
        self.st.inputs["Claims to check"] = "Draft"
        self.ui.review_composer(self.access, "a_", [], True)
        self.st.inputs.clear()
        self.st.pressed.add("Clear draft")
        with self.assertRaises(RerunPage):
            self.ui.review_composer(self.access, "a_", [], True)
        self.st.pressed.clear()
        self.ui.review_composer(self.access, "a_", [], True)
        self.assertEqual(self.st.session_state["a_claim"], "")
        self.submit.assert_not_called()

    def test_only_explicit_submission_queues_work_and_keeps_draft(self):
        self.st.inputs.update({"Review name": "My review", "Claims to check": "My claim"})
        self.st.pressed.add("Check figures")
        with self.assertRaises(RerunPage):
            self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.submit.assert_called_once_with(self.access, "My review", "My claim", ["c"*32])
        self.assertEqual(self.st.session_state["a_selected_review"], "b"*32)
        self.assertEqual(self.st.session_state["draft_a_claim"], "My claim")

    def test_disconnected_customer_can_keep_a_draft_without_queueing(self):
        self.st.inputs["Claims to check"] = "Keep this"
        self.st.pressed.add("Check figures")
        self.ui.review_composer(self.access, "a_", ["c"*32], False)
        self.assertEqual(self.st.session_state["draft_a_claim"], "Keep this")
        self.submit.assert_not_called()

    def test_completed_job_rerun_keeps_the_actual_claim_draft(self):
        self.st.inputs["Claims to check"] = "Still editing"
        self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.st.inputs.clear()
        snapshot = {"worker_available": True, "documents": [], "jobs": []}
        with patch.object(store, "progress_snapshot", return_value=snapshot):
            self.ui.live_updates(self.access, "a_", 1)
            snapshot["jobs"] = [{"id": "b"*32, "status": "complete", "completed": 0}]
            with self.assertRaises(RerunPage):
                self.ui.live_updates(self.access, "a_", 1)
            self.ui.live_updates(self.access, "a_", 2)
        self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.assertEqual(self.st.session_state["a_claim"], "Still editing")
        self.submit.assert_not_called()

    def test_submission_validation_error_keeps_the_draft(self):
        self.st.inputs["Claims to check"] = "My draft"
        self.st.pressed.add("Check figures")
        self.submit.side_effect = ValueError("Source is no longer ready")
        self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.assertEqual(self.st.session_state["draft_a_claim"], "My draft")
        self.assertIn("Source is no longer ready", self.st.rendered_text("error"))

    def test_review_note_survives_cleanup_and_retains_original_concurrency_version(self):
        with patch.object(store, "decisions", return_value={0: {"note": "Saved", "decision": "unreviewed", "updated": 10}}) as decisions, patch.object(store, "decide") as save:
            self.st.inputs["Review note"] = "Unfinished note"
            self.ui.decision_editor(self.access, "b"*32, 0, "note_")
            self.st.inputs.clear()
            del self.st.session_state["note_note"]
            decisions.return_value = {0: {"note": "Other reviewer", "decision": "reviewed", "updated": 20}}
            self.ui.decision_editor(self.access, "b"*32, 0, "note_")
            self.assertEqual(self.st.session_state["note_note"], "Unfinished note")
            self.st.pressed.add("Save decision")
            self.ui.decision_editor(self.access, "b"*32, 0, "note_")
            save.assert_not_called()
            self.assertEqual(self.st.session_state["note_base_updated"], 10)
            self.assertIn("Another reviewer changed", self.st.rendered_text("warning"))

    def test_note_save_retains_optimistic_lock_and_discard_loads_latest_note(self):
        with patch.object(store, "decisions", return_value={0: {"note": "Saved", "decision": "unreviewed", "updated": 10}}) as decisions, patch.object(store, "decide") as save:
            self.st.inputs["Review note"] = "My note"
            self.st.pressed.add("Save decision")
            with self.assertRaises(RerunPage):
                self.ui.decision_editor(self.access, "b"*32, 0, "note_")
            save.assert_called_once_with(self.access, "b"*32, 0, "unreviewed", "My note", expected_updated=10)
            self.st.inputs.clear()
            self.st.pressed = {"Discard draft changes"}
            decisions.return_value = {0: {"note": "Latest saved note", "decision": "reviewed", "updated": 20}}
            self.ui.decision_editor(self.access, "b"*32, 0, "note_")
            self.assertEqual(self.st.session_state["note_note"], "Latest saved note")
            self.assertEqual(self.st.session_state["note_choice"], "reviewed")
            self.assertEqual(self.st.session_state["note_base_updated"], 20)

    def test_failed_connection_check_keeps_model_drafts_without_storing_the_key_as_a_draft(self):
        self.st.inputs.update({"OpenRouter API key": "sk-or-synthetic-key", "Reasoning model ID": "vendor/edited", "Vision model ID": "vendor/edited-vision"})
        self.st.pressed.add("Check and save connection")
        with patch.object(self.ui.model_connections, "save", side_effect=self.ui.model_connections.ModelConnectionError("Unsupported model")):
            self.ui.connection_panel(self.access, "a_")
        self.assertEqual(self.st.session_state["draft_a_text_model"], "vendor/edited")
        self.assertEqual(self.st.session_state["draft_a_vision_model"], "vendor/edited-vision")
        self.assertNotIn("sk-or-synthetic-key", json.dumps(dict(self.st.session_state)))
        self.assertIn("Unsupported model", self.st.rendered_text("error"))
        self.assertIn("vendor/saved", self.st.rendered_text("text"))

    def test_retry_explains_removed_replaced_legacy_and_exhausted_requests(self):
        for metadata, job, expected in (
            (None, self.job(), "Connect"),
            ({**self.saved, "version": "replaced"}, self.job(), "changed"),
            (self.saved, self.job(payload="{}"), "predates"),
            (self.saved, self.job(attempts=3), "three"),
            (self.saved, self.job(principal="other"), "creator"),
            (self.saved, self.job(stage="Code changed. Create a new review to use this version."), "Code changed"),
        ):
            with self.subTest(expected=expected):
                self.metadata.return_value = metadata
                self.assertIn(expected, self.ui.retry_unavailable_reason(self.access, job))
        self.metadata.return_value = self.saved
        self.assertIsNone(self.ui.retry_unavailable_reason(self.access, self.job()))

    def test_removed_key_does_not_offer_a_retry_or_hide_the_original_claim(self):
        self.metadata.return_value = None
        self.st.pressed.add("Retry / resume")
        with patch.object(store, "resume") as resume:
            self.ui.review_detail(self.access, self.job(), "job_")
            resume.assert_not_called()
        self.assertIn("Connect your OpenRouter key", self.st.rendered_text("caption"))
        self.assertIn("Original claim", self.st.rendered_text("text"))
        self.assertNotIn("### UNKNOWN", self.st.rendered_text("markdown"))

    def test_safe_request_details_show_job_models_and_observed_calls_only(self):
        job = self.job()
        job["result"] = json.dumps({"model_calls": [{"provider": "openrouter", "requested_model": "vendor/saved",
            "actual_model": "vendor/actual", "http_status": 429, "provider_request_id": "gen-fixture",
            "error_message": "PRIVATE_ERROR", "api_key": "PRIVATE_KEY"}], "results": []})
        self.ui.request_details(job)
        shown = self.st.rendered_text()
        for value in ("vendor/saved", "vendor/actual", "gen-fixture", "429"):
            self.assertIn(value, shown)
        for private in ("PRIVATE_ERROR", "PRIVATE_KEY", "saved-version"):
            self.assertNotIn(private, shown)

    def test_failure_hints_distinguish_quota_billing_and_authentication(self):
        for code, expected in ((429, "rate limit"), (402, "credit"), (401, "key"), (503, "provider")):
            with self.subTest(code=code):
                result = {"model_calls": [{"http_status": code}]}
                self.assertIn(expected, self.ui.failure_hint(result).lower())

    def test_connection_fragment_reenters_login_after_membership_revocation(self):
        self.metadata.side_effect = PermissionError("Membership revoked")
        with self.assertRaises(RerunPage):
            self.ui.connection_panel(self.access, "a_")

    def test_workspace_switch_rejects_a_stale_composer_callback(self):
        self.st.session_state.customer_workspace = "b"*32
        self.st.pressed.add("Check figures")
        with self.assertRaises(RerunPage):
            self.ui.review_composer(self.access, "a_", ["c"*32], True)
        self.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
