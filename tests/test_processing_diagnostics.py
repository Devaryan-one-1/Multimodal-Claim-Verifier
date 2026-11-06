"""Actionable failures and separate vision budgets; no provider requests or real keys."""
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import MagicMock, patch

import test_customer_access as access_fixture
import test_customer_connections as connections_fixture
import test_customer_worker as worker_fixture
import test_customer_ui_refinements as ui_fixture
import test_visual_extraction as visual_fixture
from services.failure_details import describe_failure
from pilot import store, model_connections as connections


class FailureClassificationTests(unittest.TestCase):
    def test_common_failures_have_specific_codes_and_recovery_steps(self):
        cases = [
            ({"message": "Output reached the token limit (max_tokens=8192); the structured response was truncated."}, "output_truncated"),
            ({"message": "free-models-per-day PRIVATE_PROVIDER_BODY", "metadata": {"http_status": 429}}, "daily_quota"),
            ({"metadata": {"http_status": 429}}, "rate_limit"),
            ({"message": "embedding quota exhausted", "metadata": {"http_status": 429}}, "embedding_quota"),
            ({"metadata": {"http_status": 401}}, "key_rejected"),
            ({"metadata": {"http_status": 402}}, "credits_exhausted"),
            ({"metadata": {"http_status": 404}}, "model_unavailable"),
            ({"metadata": {"http_status": 503}}, "provider_unavailable"),
            ({"message": "choose a vision model in the sidebar"}, "vision_missing"),
            ({"kind": "ModelConfigurationError"}, "model_configuration"),
            ({"kind": "ModelConnectionError", "message": "saved connection removed"}, "connection_unavailable"),
            ({"kind": "ModelConnectionError", "message": "Encryption storage unavailable"}, "key_storage"),
            ({"kind": "ReadTimeout"}, "timeout"),
            ({"kind": "ConnectError"}, "network"),
            ({"message": "context length exceeded", "metadata": {"http_status": 400}}, "context_limit"),
            ({"metadata": {"http_status": 422}}, "model_incompatible"),
            ({"message": "response was invalid JSON"}, "invalid_response"),
            ({"metadata": {"finish_reason": "content_filter"}}, "refused"),
            ({"kind": "PDFLimitError"}, "pdf_limit"),
            ({"kind": "InvalidPDFError", "message": "password protected"}, "pdf_locked"),
            ({"kind": "InvalidPDFError"}, "invalid_pdf"),
            ({"kind": "NoUsableEvidenceError"}, "no_evidence"),
            ({"message": "Source version changed."}, "source_changed"),
            ({"message": "Vision table rows and columns have different widths"}, "table_transcription"),
            ({"error": OSError(errno.ENOSPC, "PRIVATE_PATH")}, "disk_full"),
            ({"error": FileNotFoundError("PRIVATE_PATH")}, "storage"),
            ({"error": PermissionError("Workspace access revoked")}, "access_lost"),
            ({"error": RuntimeError("PRIVATE_PROVIDER_BODY PRIVATE_CLAIM sk-private-secret")}, "internal_error"),
        ]
        for arguments, expected in cases:
            with self.subTest(expected=expected):
                result = describe_failure(**arguments)
                self.assertEqual(result["code"], expected)
                for field in ("title", "message", "action"):
                    self.assertTrue(result[field])
                for private in ("PRIVATE_PROVIDER_BODY", "PRIVATE_CLAIM", "sk-private-secret", "PRIVATE_PATH"):
                    self.assertNotIn(private, json.dumps(result))

    def test_safe_request_context_includes_location_limit_status_and_reference(self):
        result = describe_failure(stage="Reading table 2 on page 14", calls=[{
            "status": "error", "http_status": 200, "finish_reason": "length", "output_token_limit": 32768,
            "requested_model": "nvidia/fixture:free", "request_id": "gen-fixture", "error_message": "PRIVATE_BODY"}])
        self.assertEqual(result["code"], "output_truncated")
        self.assertEqual((result["page"], result["table"]), (14, 2))
        self.assertEqual(result["output_token_limit"], 32768)
        self.assertEqual(result["provider_request_id"], "gen-fixture")
        self.assertEqual(result["http_status"], 200)
        self.assertNotIn("PRIVATE_BODY", json.dumps(result))

    def test_successful_old_call_is_not_blame_for_a_new_failure(self):
        result = describe_failure(RuntimeError("PRIVATE_BODY"), calls=[{"status": "ok", "http_status": 402}])
        self.assertEqual(result["code"], "internal_error")
        self.assertNotIn("http_status", result)

    def test_source_validation_failure_is_distinct_from_invalid_model_json(self):
        for prefix in ("Source selection failed local schema validation after one correction; ",
                       "Source selection failed validation against saved evidence after one correction; "):
            result = describe_failure(kind="VerificationFailure", stage="numeric_selection",
                                      message=prefix+"Chart period does not match the claim. PRIVATE_CLAIM")
            self.assertEqual(result["code"], "source_validation")
            self.assertIn("period", result["message"].lower())
            self.assertNotIn("json", result["message"].lower())
            self.assertNotIn("PRIVATE_CLAIM", json.dumps(result))
        invalid = describe_failure(kind="ModelServiceError", stage="numeric_selection", message="Invalid JSON response")
        self.assertEqual(invalid["code"], "invalid_response")

    def test_invalid_diagnostic_values_and_secrets_are_not_copied(self):
        result = describe_failure(metadata={"requested_model": "sk-private-secret", "request_id": "sk-private-secret",
            "retry_after_seconds": float("inf"), "output_token_limit": "PRIVATE", "api_key": "PRIVATE"})
        self.assertNotIn("model", result)
        self.assertNotIn("provider_request_id", result)
        self.assertNotIn("output_token_limit", result)
        self.assertNotIn("retry_after_seconds", result)


class VisionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.gateway = worker_fixture.load_gateway(self.stack)
        self.stack.enter_context(patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-public-fixture"}))

    def test_gateway_uses_larger_budget_only_for_vision_and_records_it(self):
        schema = types.SimpleNamespace(model_json_schema=lambda: {"type": "object"}, model_validate=lambda value: value)
        with patch.object(self.gateway, "_openrouter", return_value=({}, {"finish_reason": "stop"})) as call:
            self.gateway.structured(schema, "fixture", task="vision")
            self.assertEqual(call.call_args.args[0].max_output_tokens, 32768)
            self.assertEqual(self.gateway.last_call()["output_token_limit"], 32768)
            self.gateway.structured(schema, "fixture", task="text")
            self.assertEqual(call.call_args.args[0].max_output_tokens, 8192)
        self.assertEqual(self.gateway.config().max_output_tokens, 8192)

    def test_provider_truncation_is_retained_with_safe_http_and_budget(self):
        schema = types.SimpleNamespace(model_json_schema=lambda: {"type": "object"}, model_validate=lambda value: value)
        error = self.gateway.ModelServiceError("structured response was truncated", metadata={"finish_reason": "length", "http_status": 200})
        with patch.object(self.gateway, "_openrouter", side_effect=error):
            with self.assertRaises(self.gateway.ModelServiceError) as raised:
                self.gateway.structured(schema, "PRIVATE_CLAIM", task="vision")
        result = describe_failure(raised.exception)
        self.assertEqual(result["code"], "output_truncated")
        self.assertEqual(result["output_token_limit"], 32768)
        self.assertNotIn("PRIVATE_CLAIM", json.dumps(result))


class SavedBudgetTests(unittest.TestCase):
    def setUp(self):
        connections_fixture.TemporaryStore.setUp(self)

    def test_new_requests_upgrade_legacy_budget_without_rewriting_saved_key_or_models(self):
        legacy = dict(self.models); legacy.pop("vision_max_output_tokens")
        connections_fixture.TemporaryStore.seed(self, self.a, "fixture-version", legacy)
        with store.db() as c:
            snapshot = connections.snapshot(c, self.a, vision=True)
            unchanged = c.execute("SELECT models,secret FROM model_connections WHERE principal=?", (self.a.principal,)).fetchone()
        self.assertEqual(snapshot["models"]["vision_max_output_tokens"], 32768)
        self.assertEqual(json.loads(unchanged["models"]), legacy)
        self.assertEqual(unchanged["secret"], "opaque-encrypted-fixture")

    def test_legacy_job_keeps_its_original_vision_budget(self):
        legacy = dict(self.models); legacy.pop("vision_max_output_tokens")
        connections_fixture.TemporaryStore.seed(self, self.a, "fixture-version", legacy)
        with patch.object(connections, "_decode", return_value="sk-fixture"):
            _, config = connections.credentials_for_job(self.a, {"openrouter": {"connection_version": "fixture-version", "models": legacy}})
        self.assertEqual(config["vision_max_output_tokens"], 8192)

    def test_model_validation_checks_each_roles_required_budget(self):
        small = connections_fixture.endpoint(); small["endpoints"][0]["max_completion_tokens"] = 8192
        with patch.object(connections, "_get", side_effect=[{}, small, small]):
            with self.assertRaisesRegex(connections.ModelConnectionError, "32768"):
                connections.validate("sk-public-fixture", self.models)


class ProcessingFailurePersistenceTests(unittest.TestCase):
    def setUp(self):
        worker_fixture.CustomerWorkerTests.setUp(self)
        self.pipeline = types.ModuleType("pipelines.document_pipeline")
        self.pipeline.process_document = MagicMock()
        self.stack.enter_context(patch.dict(sys.modules, {"pipelines.document_pipeline": self.pipeline}))
        self.stack.enter_context(patch.object(self.task, "check_pdf"))
        with store.db() as c:
            c.execute("UPDATE jobs SET kind='process',payload=? WHERE id=?", (json.dumps({"document_id": self.doc, "options": {}}), self.job))

    def run_job(self):
        return worker_fixture.CustomerWorkerTests.run_job(self)

    def test_real_worker_saves_detailed_processing_error_without_private_body(self):
        def fail(*args, reporter=None, **kwargs):
            reporter.update("Reading table 2 on page 14")
            self.gateway._record({"status": "error", "task": "vision", "http_status": 200, "finish_reason": "length",
                "output_token_limit": 32768, "requested_model": "vendor/vision", "error_message": "PRIVATE_PROVIDER_BODY"})
            raise self.gateway.ModelServiceError("PRIVATE_PROVIDER_BODY PRIVATE_CLAIM sk-private-secret", metadata={"finish_reason": "length", "http_status": 200})
        self.pipeline.process_document.side_effect = fail
        row = self.run_job()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["result"]["failure"]["code"], "output_truncated")
        self.assertEqual((row["result"]["failure"]["page"], row["result"]["failure"]["table"]), (14, 2))
        self.assertEqual(row["result"]["model_calls"][0]["output_token_limit"], 32768)
        self.assertNotIn("UNKNOWN", row["result"].get("verdict", ""))
        for private in ("PRIVATE_PROVIDER_BODY", "PRIVATE_CLAIM", "sk-private-secret"):
            self.assertNotIn(private, json.dumps(row["result"]))


class LegacyProcessingDetailsTests(unittest.TestCase):
    def setUp(self):
        access_fixture.CustomerAccessTests.setUp(self)
        self.addCleanup(access_fixture.CustomerAccessTests.tearDown, self)
        self.path = store.file_path(self.a.workspace, "data/processed", "c"*32, "document.json")
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"error_kind": "ModelServiceError", "error_stage": "document_processing",
            "error": "Output reached the token limit (max_tokens=8192); the structured response was truncated. PRIVATE_CLAIM"}))
        with store.db() as c:
            c.execute("UPDATE jobs SET kind='process',status='failed',payload=?,updated=? WHERE id=?",
                      (json.dumps({"document_id": "c"*32}), self.path.stat().st_mtime, "d"*32))

    def test_old_generic_error_gets_safe_details_without_database_migration(self):
        details = store.processing_failure(self.a, "d"*32)
        self.assertEqual(details["code"], "output_truncated")
        self.assertEqual(details["output_token_limit"], 8192)
        self.assertNotIn("PRIVATE_CLAIM", json.dumps(details))

    def test_another_attempts_error_is_not_attached_to_old_job(self):
        with store.db() as c:
            c.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time()-100, "d"*32))
        self.assertEqual(store.processing_failure(self.a, "d"*32)["code"], "internal_error")

    def test_cross_workspace_error_details_remain_private(self):
        with self.assertRaises(PermissionError): store.processing_failure(self.b, "d"*32)


class FailureDisplayTests(unittest.TestCase):
    def setUp(self):
        ui_fixture.CustomerInterfaceTests.setUp(self)

    def test_customer_sees_cause_recovery_location_limit_and_reference(self):
        details = describe_failure(stage="Reading table 2 on page 14", metadata={"finish_reason": "length", "output_token_limit": 32768, "http_status": 200})
        job = ui_fixture.CustomerInterfaceTests.job(self, result=json.dumps({"failure": details, "error_kind": "ModelServiceError"}))
        self.ui.show_failure(self.access, job)
        text = self.st.rendered_text()
        for expected in ("output limit", "Start a new", "PDF page: 14", "Table: 2", "32768", "output_truncated", job["id"]):
            self.assertIn(expected, text)


class LatestProcessingDisplayTests(unittest.TestCase):
    def setUp(self):
        visual_fixture.VisualRefreshUITests.setUp(self)
        self.doc = "b"*32

    def seed_attempt(self, identity, status, created, updated=None, source=None, code="internal_error"):
        with store.db() as c:
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated) VALUES(?,?,?,'process',?,?,?,?,?,?)",
                      (identity, self.access.workspace, self.access.principal, json.dumps({"document_id": source or self.doc}),
                       status, "Current stage", json.dumps({"failure": describe_failure(kind=code)}), created, updated or created))
            c.execute("UPDATE documents SET status='error',metadata=? WHERE id=?", (json.dumps({"warnings": ["STALE_SAVED_WARNING"]}), source or self.doc))

    def render(self):
        self.ui.documents_page(self.access, "customer_", connected=True)
        return self.st.rendered_text()

    def test_new_running_attempt_hides_old_errors_and_old_metadata_warnings(self):
        self.seed_attempt("d"*32, "failed", 1, code="output_truncated")
        self.seed_attempt("e"*32, "paused", 2, code="code_changed")
        self.seed_attempt("f"*32, "running", 3)
        text = self.render()
        self.assertEqual(text.count("Process:"), 1)
        self.assertIn("running", text)
        self.assertEqual([event for event in self.st.events if event[0] == "error"], [])
        self.assertNotIn("STALE_SAVED_WARNING", text)
        self.assertNotIn("Start new processing with my saved connection", text)
        self.assertEqual(len([j for j in store.jobs(self.access) if j["kind"] == "process"]), 3)

    def test_only_latest_failed_attempts_details_and_actions_are_shown(self):
        self.seed_attempt("d"*32, "failed", 1, code="output_truncated")
        self.seed_attempt("e"*32, "failed", 2, code="daily_quota")
        text = self.render()
        self.assertEqual(text.count("Process:"), 1)
        self.assertIn("daily_quota", text)
        self.assertNotIn("output_truncated", text)
        self.assertIn("e"*32, text)
        self.assertNotIn("d"*32, text)

    def test_retry_of_same_job_becomes_latest_by_activity_not_creation_time(self):
        self.seed_attempt("d"*32, "queued", 1, updated=4)
        self.seed_attempt("e"*32, "failed", 2, code="output_truncated")
        text = self.render()
        self.assertEqual(text.count("Process:"), 1)
        self.assertIn("queued", text)
        self.assertNotIn("output_truncated", text)
        self.assertNotIn("STALE_SAVED_WARNING", text)

    def test_successful_latest_attempt_hides_prior_failure(self):
        self.seed_attempt("d"*32, "failed", 1, code="output_truncated")
        self.seed_attempt("e"*32, "complete", 2)
        with store.db() as c:
            c.execute("UPDATE documents SET status='ready',metadata=? WHERE id=?", (json.dumps({"warnings": ["CURRENT_COMPLETION_WARNING"]}), self.doc))
        text = self.render()
        self.assertEqual(text.count("Process:"), 1)
        self.assertIn("complete", text)
        self.assertNotIn("output_truncated", text)
        self.assertIn("CURRENT_COMPLETION_WARNING", text)

    def test_deleted_report_does_not_show_stale_job_or_unavailable_source_error(self):
        self.seed_attempt("d"*32, "failed", 1, source="a"*32, code="output_truncated")
        self.assertNotIn("Process:", self.render())

    def test_different_reports_each_keep_their_own_latest_attempt(self):
        other = "a"*32
        with store.db() as c:
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      (other, self.access.workspace, "Other.pdf", "other-hash", "error", 1))
        self.seed_attempt("d"*32, "failed", 1, code="output_truncated")
        self.seed_attempt("e"*32, "failed", 2, source=other, code="daily_quota")
        text = self.render()
        self.assertEqual(text.count("Process:"), 2)
        self.assertIn("output_truncated", text)
        self.assertIn("daily_quota", text)


class ProcessingRetryStateTests(unittest.TestCase):
    def setUp(self):
        connections_fixture.TemporaryStore.setUp(self)
        connections_fixture.ConnectionStoreTests.paused_processing(self)
        connections_fixture.TemporaryStore.seed(self, self.a, "fixture-version")
        with store.db() as c:
            payload = json.loads(c.execute("SELECT payload FROM jobs WHERE id=?", ("e"*32,)).fetchone()[0])
            payload["openrouter"] = {"connection_version": "fixture-version", "models": self.models}
            c.execute("UPDATE jobs SET payload=?,result=? WHERE id=?", (json.dumps(payload), json.dumps({"failure": describe_failure(kind="output_truncated")}), "e"*32))

    def test_retry_queues_document_and_retains_old_attempt_details_for_audit(self):
        store.resume(self.a, "e"*32)
        self.assertEqual(store.get_job(self.a, "e"*32)["status"], "queued")
        self.assertEqual(store.documents(self.a)[0]["status"], "queued")
        self.assertEqual(json.loads(store.get_job(self.a, "e"*32)["result"])["failure"]["code"], "output_truncated")

    def test_stale_retry_cannot_queue_duplicate_processing(self):
        store.restart_processing(self.a, "e"*32)
        with self.assertRaisesRegex(ValueError, "already has active processing"):
            store.resume(self.a, "e"*32)

    def test_stale_retry_cannot_reprocess_an_already_ready_source(self):
        with store.db() as c: c.execute("UPDATE documents SET status='ready' WHERE id=?", ("d"*32,))
        with self.assertRaisesRegex(ValueError, "already ready"):
            store.resume(self.a, "e"*32)


if __name__ == "__main__": unittest.main()
