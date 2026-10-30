"""Offline regression checks for failure presentation and retry behavior."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from pilot.benchmark import score
from services.verification_status import execution_failed, verification_state


def failed(**updates):
    result = {"claim_text": "Revenue grew in 2025.", "status": "error",
              "verdict": "UNKNOWN", "error": "Synthetic provider failure",
              "document_ids": ["original-document"]}
    result.update(updates)
    return result


class SessionState(dict):
    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value


class StreamlitDouble(types.ModuleType):
    """Record view calls and simulate explicit button presses without a server."""

    def __init__(self):
        super().__init__("streamlit")
        self.events = []
        self.session_state = SessionState()
        self.retry_pressed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.events.append((name, args, kwargs))
            if name == "columns":
                count = args[0] if isinstance(args[0], int) else len(args[0])
                return [self for _ in range(count)]
            if name == "button":
                return self.retry_pressed and args[0] == "Retry this claim"
            if name in {"download_button", "toggle", "checkbox"}:
                return False
            return self

        return call


class VerificationStateTests(unittest.TestCase):
    def test_operational_markers_are_not_factual_unknown(self):
        cases = [failed(), failed(status="failed"), failed(status="paused", error=None),
                 {"status": "complete", "verdict": "UNKNOWN", "parsing_error": "bad parse"},
                 {"status": "complete", "parsed_claim": {"parsing_error": "bad parse"}},
                 {"verdict": "UNKNOWN", "pause": {"kind": "daily_quota"}},
                 {"parsed_claim": {"pause": {"kind": "daily_quota"}}}]
        for result in cases:
            with self.subTest(result=result):
                self.assertTrue(execution_failed(result))

    def test_completed_unknown_and_legacy_results_remain_factual(self):
        for result in ({"status": "complete", "verdict": "UNKNOWN"},
                       {"verdict": "SUPPORTED"}, {"status": "complete", "parsed_claim": None}):
            with self.subTest(result=result):
                self.assertFalse(execution_failed(result))

    def test_batch_states_keep_partial_completion_separate(self):
        complete = {"status": "complete", "verdict": "SUPPORTED"}
        paused = failed(status="paused", error=None)
        for results, expected in (([], "failed"), ([complete], "complete"),
                                  ([failed()], "failed"), ([paused], "paused"),
                                  ([failed(pause={"kind": "daily_quota"})], "paused"),
                                  ([complete, failed()], "incomplete"),
                                  ([complete, paused], "incomplete")):
            with self.subTest(expected=expected, results=results):
                self.assertEqual(verification_state(results), expected)

    def test_benchmark_excludes_failed_and_paused_unknowns(self):
        report = score([
            {"status": "complete", "gold": "SUPPORTED", "predicted": "SUPPORTED"},
            {"status": "failed", "gold": "UNKNOWN", "predicted": "UNKNOWN"},
            {"status": "paused", "gold": "UNKNOWN", "predicted": "UNKNOWN"},
        ])
        self.assertEqual(report["completed"], 1)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["completion_rate"], 1 / 3)


class VerificationUITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.st = StreamlitDouble()
        self.aggregate = Mock(return_value="UNKNOWN")
        pipeline = types.ModuleType("pipelines.verification_pipeline")
        pipeline.aggregate_verdict = self.aggregate
        calculation = types.ModuleType("ui.calculation")
        calculation._format = Mock(return_value="0")
        calculation.render_calculation = Mock()
        self.stack.enter_context(patch.dict(sys.modules, {
            "streamlit": self.st,
            "pipelines.verification_pipeline": pipeline,
            "ui.calculation": calculation,
        }))
        self.stack.enter_context(patch("services.application.aggregate_verdict", self.aggregate))
        self.claim = self.load_module("_claim_ui_under_test", APP / "ui/claim_input.py")
        self.dashboard = self.load_module("_dashboard_under_test", APP / "ui/dashboard.py")

    def load_module(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        self.stack.enter_context(patch.dict(sys.modules, {name: module}))
        spec.loader.exec_module(module)
        return module

    def status_update(self):
        return [kwargs for name, _, kwargs in self.st.events
                if name == "update" and "state" in kwargs][-1]

    def rendered_text(self, kind=None):
        return "\n".join(str(arg) for name, args, _ in self.st.events
                         if kind is None or name == kind for arg in args)

    def run_callback(self, results):
        callback = Mock(return_value=results)
        self.claim.execute_verification("Revenue grew in 2025.", ["original-document"], callback)
        return self.status_update()

    def test_returned_error_has_failed_banner(self):
        update = self.run_callback([failed()])
        self.assertEqual(update["label"], "Verification failed")
        self.assertEqual(update["state"], "error")

    def test_quota_pause_has_paused_banner(self):
        update = self.run_callback([failed(pause={"kind": "daily_quota"})])
        self.assertEqual(update["label"], "Verification paused")
        self.assertNotEqual(update["state"], "complete")

    def test_mixed_batch_has_incomplete_banner(self):
        update = self.run_callback([{"status": "complete", "verdict": "SUPPORTED"}, failed()])
        self.assertEqual(update["label"], "Verification incomplete")
        self.assertEqual(update["state"], "error")

    def test_completed_unknown_has_completed_banner(self):
        update = self.run_callback([{"status": "complete", "verdict": "UNKNOWN"}])
        self.assertEqual(update["label"], "Verification completed")
        self.assertEqual(update["state"], "complete")

    def test_empty_callback_is_failed(self):
        update = self.run_callback([])
        self.assertEqual(update["label"], "Verification failed")
        self.assertEqual(update["state"], "error")

    def test_callback_exception_keeps_original_source_scope(self):
        callback = Mock(side_effect=RuntimeError("Synthetic provider failure"))
        results = self.claim.execute_verification("Revenue grew in 2025.", ["original-document"], callback)
        self.assertEqual(self.status_update()["state"], "error")
        self.assertEqual(results[0]["document_ids"], ["original-document"])

    def test_missing_result_scope_is_saved_for_retry(self):
        callback = Mock(return_value=[failed(document_ids=None)])
        # Legacy callbacks may omit source scope entirely.
        result = failed()
        del result["document_ids"]
        callback.return_value = [result]
        results = self.claim.execute_verification("Revenue grew in 2025.", ["original-document"], callback)
        self.assertEqual(results[0]["document_ids"], ["original-document"])
        self.assertNotIn("document_ids", result)

    def test_failed_history_does_not_aggregate_a_factual_verdict(self):
        self.assertEqual(self.claim.history_verdict([failed()]), "Verification failed")
        self.aggregate.assert_not_called()

    def test_paused_history_does_not_aggregate_a_factual_verdict(self):
        self.assertEqual(self.claim.history_verdict([failed(status="paused", error=None)]), "Paused")
        self.aggregate.assert_not_called()

    def test_mixed_history_is_incomplete(self):
        results = [{"status": "complete", "verdict": "SUPPORTED"}, failed()]
        self.assertEqual(self.claim.history_verdict(results), "Incomplete")
        self.aggregate.assert_not_called()

    def test_completed_unknown_history_still_uses_factual_aggregation(self):
        results = [{"status": "complete", "verdict": "UNKNOWN"}]
        self.assertEqual(self.claim.history_verdict(results), "UNKNOWN")
        self.aggregate.assert_called_once_with(results)

    def test_recent_history_displays_failed_execution(self):
        self.st.session_state.claim_history = [{"id": "saved-run", "claim_text": "Revenue grew in 2025.",
                                               "results": [failed()]}]
        self.claim.render_recent_claims()
        self.assertIn("Verification failed", self.rendered_text("markdown"))
        self.assertNotIn("UNKNOWN", self.rendered_text("markdown"))

    def test_failed_and_paused_results_offer_retry(self):
        for status in ("error", "failed", "paused"):
            with self.subTest(status=status):
                self.st.events.clear()
                result = failed(status=status, error=None)
                self.claim.render_failed_claim_retries([result], ["changed-selection"], Mock())
                buttons = [args[0] for name, args, _ in self.st.events if name == "button"]
                self.assertIn("Retry this claim", buttons)

    def test_retry_uses_original_scope_and_preserves_completed_atoms(self):
        completed = {"status": "complete", "verdict": "SUPPORTED", "claim_text": "First atom."}
        paused = failed(status="paused", error=None)
        replacement = {"status": "complete", "verdict": "CONTRADICTED"}
        callback = Mock(return_value=[replacement])
        self.st.retry_pressed = True
        results = self.claim.render_failed_claim_retries([completed, paused], ["changed-selection"], callback)
        self.assertIsNotNone(results)
        self.assertIs(results[0], completed)
        self.assertEqual(results[1]["verdict"], "CONTRADICTED")
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(callback.call_args.kwargs["document_ids"], ["original-document"])
        self.assertEqual(callback.call_args.kwargs["claim_text"], paused["claim_text"])

    def test_multiple_retries_can_expand_without_replacing_other_atoms(self):
        self.st.retry_pressed = True
        callback = Mock(side_effect=[
            [{"claim_text": "A", "status": "complete", "verdict": "SUPPORTED"},
             {"claim_text": "B", "status": "complete", "verdict": "SUPPORTED"}],
            [{"claim_text": "C", "status": "complete", "verdict": "CONTRADICTED"}],
        ])
        results = self.claim.render_failed_claim_retries(
            [failed(claim_text="First failed."), failed(claim_text="Second failed.")],
            ["changed-selection"], callback)
        self.assertEqual([result["claim_text"] for result in results], ["A", "B", "C"])

    def test_completed_unknown_is_not_retried(self):
        callback = Mock()
        self.st.retry_pressed = True
        result = self.claim.render_failed_claim_retries(
            [{"status": "complete", "verdict": "UNKNOWN"}], ["source"], callback)
        self.assertIsNone(result)
        callback.assert_not_called()
        self.assertNotIn("Retry this claim", self.rendered_text("button"))

    def test_failed_cards_suppress_factual_unknown_heading(self):
        for result in (failed(), failed(status="paused", error=None),
                       {"status": "complete", "verdict": "UNKNOWN",
                        "parsed_claim": {"parsing_error": "Synthetic parser failure"}}):
            with self.subTest(result=result):
                self.st.events.clear()
                self.dashboard.render_result(result, 0, False)
                self.assertIn("Verification failed", self.rendered_text("error"))
                self.assertNotIn("<h2>", self.rendered_text("markdown"))

    def test_mixed_summary_says_incomplete(self):
        self.dashboard.render_overall([{"status": "complete", "verdict": "SUPPORTED"}, failed()])
        self.assertIn("Review incomplete", self.rendered_text("warning"))
        self.assertNotIn("Overall:", self.rendered_text("markdown"))


if __name__ == "__main__":
    unittest.main()
