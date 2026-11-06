"""Items 6/7/8: real temporary-store persistence/exports and account boundaries."""
import csv
import io
import json
import sys
import time
import types
import unittest
from unittest.mock import MagicMock, patch

import test_customer_beta as fixtures
import test_customer_account_security as security
import test_customer_account_auth as account
from pilot import store, private_media
from pilot.exports import exports


class HistoryExportAcceptanceTests(unittest.TestCase):
    def setUp(self):
        fixtures.CustomerExportTests.setUp(self)
        self.st = fixtures.StreamlitFixture()
        self.st.session_state.update(customer_identity=self.access.principal, customer_workspace=self.access.workspace)
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw = MagicMock(), MagicMock()
        self.stack.enter_context(patch.dict(sys.modules, {"streamlit": self.st, "pymupdf": types.ModuleType("pymupdf"), "PIL": pil}))
        self.ui = fixtures.load_module(self.stack, "_history_acceptance_ui", fixtures.APP/"pilot/ui.py")

    def save_result(self, result=None, status="complete"):
        with store.db() as c:
            c.execute("UPDATE jobs SET result=?,status=? WHERE id=?",
                      (json.dumps({"results": [result or self.result]}), status, self.job))

    def test_export_contains_difference_period_units_addresses_and_run_status(self):
        self.result["comparison"].update(claimed=110000000, difference=10000000, tolerance=500000, operator="equal")
        self.result["parsed_claim"] = {"end_period": 2025, "period_type": "annual"}
        self.result["calculation"].update(input_metadata={"value": {"currency": "USD", "scale": 1000000}},
                                         source_inputs={"value": {"document_id": "b"*32, "page": 7, "row_index": 2, "column_index": 1}})
        self.save_result()
        csv_data, html, raw = exports(self.access, self.job)
        row = next(csv.DictReader(io.StringIO(csv_data)))
        self.assertEqual(row["run_status"], "complete")
        self.assertEqual(row["execution_status"], "complete")
        self.assertEqual(row["signed_difference"].lstrip("'"), "+10000000")
        self.assertEqual(json.loads(row["reporting_period"])["end_period"], 2025)
        self.assertEqual(json.loads(row["source_addresses"])["value"]["column_index"], 1)
        self.assertEqual(json.loads(row["input_metadata"])["value"]["currency"], "USD")
        exported = json.loads(raw)["export_rows"][0]
        for key in ("run_status", "execution_status", "signed_difference", "source_addresses", "input_metadata", "reporting_period"):
            self.assertEqual(row[key].lstrip("'"), str(exported[key]))
        self.assertIn("+10000000", html)

    def test_failed_legacy_unknown_is_not_exported_as_a_factual_verdict(self):
        self.result.update(status="error", verdict="UNKNOWN", error="provider unavailable")
        self.save_result(status="paused")
        csv_data, html, raw = exports(self.access, self.job)
        row = next(csv.DictReader(io.StringIO(csv_data)))
        self.assertEqual(row["execution_status"], "failed")
        self.assertEqual(row["verdict"], "")
        self.assertEqual(row["run_status"], "paused")
        self.assertEqual(json.loads(raw)["export_rows"][0]["verdict"], "")
        self.assertIn("Run status: paused", html)

    def test_completed_unknown_remains_factual(self):
        self.result.update(verdict="UNKNOWN", reason="Insufficient source evidence")
        self.save_result()
        row = next(csv.DictReader(io.StringIO(exports(self.access, self.job)[0])))
        self.assertEqual(row["verdict"], "UNKNOWN")

    def test_percentage_difference_uses_percentage_points_and_saved_value_fallback(self):
        self.result["calculation"]["value"] = 8.75
        self.result["comparison"] = {"claimed": 10, "unit": "percent"}
        self.save_result()
        row = next(csv.DictReader(io.StringIO(exports(self.access, self.job)[0])))
        self.assertEqual(row["actual"], "8.75")
        self.assertEqual(row["signed_difference"].lstrip("'"), "+1.25")
        self.assertEqual(row["difference_unit"], "percentage points")

    def test_reopened_database_keeps_history_notes_review_events_and_export_content(self):
        store.decide(self.access, self.job, 0, "reviewed", "Checked physical page 7.")
        before = exports(self.access, self.job)
        # All store functions close their connections; repeat process initialization.
        store.initialize()
        renewed = store.Access(self.access.principal, self.access.workspace, time.time()+300)
        self.assertEqual(store.jobs(renewed, "verify")[0]["id"], self.job)
        self.assertEqual(store.decisions(renewed, self.job)[0]["note"], "Checked physical page 7.")
        self.assertEqual(exports(renewed, self.job), before)
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM decision_events WHERE job=?", (self.job,)).fetchone()[0], 1)

    def test_history_opens_the_saved_review_without_ready_documents_or_a_key(self):
        self.st.pressed.add("Open review")
        key = "customer_"+self.access.workspace+"_"
        self.ui.history_page(self.access, key)
        self.assertEqual(self.st.session_state[key+"area"], "Reviews")
        self.assertEqual(self.st.session_state[key+"selected_review"], self.job)
        self.assertEqual(self.st.session_state[key+self.job+"filter"], "All")

    def add_same_named_review(self):
        other = "d"*32
        with store.db() as c:
            c.execute("UPDATE jobs SET payload=?,created=? WHERE id=?",
                      (json.dumps({"name": "Financial review", "claim": "Current assets were USD 149,818 million."}), 1793917123, self.job))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated) VALUES(?,?,?,'verify',?,'complete','Done',?,?,?)",
                      (other, self.access.workspace, self.access.principal,
                       json.dumps({"name": "Financial review", "claim": "Inventories grew 94 percent."}),
                       json.dumps({"results": [{"verdict": "UNKNOWN", "claim_text": "assertion"}]}),
                       1793917124, 1793917124))
        return other

    def test_same_named_reviews_have_distinct_claim_date_and_reference_labels(self):
        other = self.add_same_named_review()
        key = "customer_"+self.access.workspace+"_"
        self.ui.history_page(self.access, key)
        _, args, options = next(event for event in self.st.events if event[0] == "selectbox")
        labels = [options["format_func"](job) for job in args[1]]
        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(any("Current assets" in label and self.job[:8] in label for label in labels))
        self.assertTrue(any("Inventories" in label and other[:8] in label for label in labels))
        self.assertTrue(all("2026-11-05" in label and "UTC" in label for label in labels))
        self.assertIn("Current assets", self.st.rendered_text("dataframe"))
        self.st.events.clear()
        with patch.object(self.ui, "review_detail"):
            self.ui.reviews_page(self.access, key, connected=False)
        _, _, saved_options = next(event for event in self.st.events if event[0] == "selectbox")
        self.assertEqual(saved_options["format_func"](self.job), options["format_func"](self.job))

    def test_history_handoff_survives_widget_cleanup_and_opens_exact_saved_result(self):
        other = self.add_same_named_review()
        key = "customer_"+self.access.workspace+"_"
        store.decide(self.access, self.job, 0, "reviewed", "Retain this saved note.")
        for chosen in (self.job, other):
            with self.subTest(chosen=chosen):
                self.st.session_state[key+"history_review"] = chosen
                self.st.pressed.add("Open review")
                self.ui.history_page(self.access, key)
                self.st.pressed.clear()
                # Streamlit removes keys for widgets absent from the previous page.
                self.st.session_state.pop(key+"selected_review", None)
                with patch.object(self.ui, "review_detail") as detail:
                    self.ui.reviews_page(self.access, key, connected=False)
                opened = detail.call_args.args[1]
                self.assertEqual(opened["id"], chosen)
                expected = "SUPPORTED" if chosen == self.job else "UNKNOWN"
                self.assertEqual(json.loads(opened["result"])["results"][0]["verdict"], expected)
                self.assertEqual(self.st.session_state[key+chosen+"filter"], "All")
                self.assertNotIn(key+"open_review_request", self.st.session_state)
        self.assertEqual(store.decisions(self.access, self.job)[0]["note"], "Retain this saved note.")

    def test_unavailable_history_selection_does_not_open_a_different_review(self):
        key = "customer_"+self.access.workspace+"_"
        self.st.session_state[key+"open_review_request"] = "e"*32
        with patch.object(self.ui, "review_detail") as detail:
            self.ui.reviews_page(self.access, key, connected=False)
        detail.assert_not_called()
        self.assertIn("unavailable", self.st.rendered_text("error").lower())

    def test_history_callback_rechecks_revocation_and_never_changes_selected_review(self):
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (self.access.principal,))
        with self.assertRaises(fixtures.RerunPage):
            self.ui.open_saved_review(self.access, "customer_", self.job)
        self.assertNotIn("customer_selected_review", self.st.session_state)

    def test_new_saved_note_changes_the_ui_download_revision(self):
        calculation = types.ModuleType("ui.calculation")
        calculation.render_calculation = MagicMock()
        with patch.dict(sys.modules, {"ui.calculation": calculation}), patch.object(self.ui, "viewer"), patch.object(private_media, "download") as download:
            self.ui.review_detail(self.access, store.get_job(self.access, self.job), "review")
            original = download.call_args_list[0].kwargs["revision"]
            store.decide(self.access, self.job, 0, "unreviewed", "New saved note")
            download.reset_mock()
            self.ui.review_detail(self.access, store.get_job(self.access, self.job), "review")
            current = download.call_args_list[0].kwargs["revision"]
            self.assertNotEqual(original, current)
            self.assertEqual({call.kwargs["revision"] for call in download.call_args_list}, {current})
            self.assertIn("New saved note", exports(self.access, self.job)[0])


class PreparedDownloadAcceptanceTests(unittest.TestCase):
    setUp = security.PrivateDeliveryTests.setUp
    tearDown = security.PrivateDeliveryTests.tearDown

    def view(self):
        return security.PrivateDeliveryTests.view(self)

    def deliver(self, view, load, revision):
        private_media.download(view, self.a, "Export", load, filename="fixture.csv", mime="text/csv", key="csv", revision=revision)

    def test_changed_saved_content_invalidates_prepared_copy_without_reading_new_data(self):
        view, load = self.view(), MagicMock(return_value="old fixture")
        view.pressed.add("Export")
        self.deliver(view, load, "old-revision")
        view.pressed.clear(); view.events.clear()
        self.deliver(view, load, "new-revision")
        load.assert_called_once()
        self.assertFalse(any(name == "iframe" for name, _, _ in view.events))
        self.assertNotIn("customer_prepared_download", view.session_state)

    def test_unchanged_revision_keeps_the_prepared_copy_across_live_reruns(self):
        view, load = self.view(), MagicMock(return_value="fixture")
        view.pressed.add("Export")
        self.deliver(view, load, "revision")
        view.pressed.clear(); view.events.clear()
        self.deliver(view, load, "revision")
        load.assert_called_once()
        self.assertTrue(any(name == "iframe" for name, _, _ in view.events))


class RecoveryAcceptanceTests(unittest.TestCase):
    setUp = account.AccountAuthTests.setUp
    reset_inputs = account.AccountAuthTests.reset_inputs

    def test_reset_for_another_address_cannot_reuse_a_verified_recovery(self):
        self.reset_inputs()
        self.client.auth.update_user.side_effect = RuntimeError("Password policy fixture")
        self.auth.recovery_form(self.client, "url", "key")
        self.assertIn(self.auth.RECOVERY, self.st.session_state)
        self.st.inputs["Email for reset"] = "another@example.com"
        self.client.auth.verify_otp.side_effect = RuntimeError("Invalid fresh recovery code")
        self.client.auth.update_user.reset_mock()
        self.auth.recovery_form(self.client, "url", "key")
        self.client.auth.update_user.assert_not_called()
        self.assertNotIn(self.auth.RECOVERY, self.st.session_state)


if __name__ == "__main__":
    unittest.main()
