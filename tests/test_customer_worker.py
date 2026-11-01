"""Customer retry and diagnostics regressions; temporary data, no model requests."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from pilot import store
from services import observability


def load_module(stack, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    stack.enter_context(patch.dict(sys.modules, {name: module}))
    spec.loader.exec_module(module)
    return module


def load_gateway(stack):
    # Only routing/snapshot/capture logic is exercised; external APIs are forbidden.
    httpx = types.ModuleType("httpx")
    httpx.post = MagicMock(side_effect=AssertionError("No network in offline tests"))
    httpx.get = httpx.post
    httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    httpx.TransportError = type("TransportError", (Exception,), {})
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = types.ModuleType("google.genai.types")
    genai.Client = httpx.post
    google.genai = genai
    pydantic = types.ModuleType("pydantic")
    pydantic.BaseModel = object
    jsonschema = types.ModuleType("jsonschema")
    jsonschema.Draft202012Validator = MagicMock()
    runtime = types.ModuleType("services.runtime")
    runtime.write = MagicMock()
    stack.enter_context(patch.dict(sys.modules, {
        "httpx": httpx, "google": google, "google.genai": genai,
        "google.genai.types": genai.types, "pydantic": pydantic,
        "jsonschema": jsonschema, "services.runtime": runtime,
    }))
    gateway = load_module(stack, "services.model_gateway", APP / "services/model_gateway.py")
    gateway.configure(gateway.ModelConfig(
        provider="openrouter", text_model="vendor/text-model", vision_model="vendor/vision-model",
        draft_model="vendor/text-model", text_fallbacks=("vendor/backup",), allow_fallbacks=True,
    ))
    stack.enter_context(patch.object(gateway, "record_model_call"))
    return gateway


ERROR_CALL = {
    "gateway_provider": "openrouter", "requested_model": "vendor/text-model",
    "actual_model": None, "status": "error", "task": "text",
    "duration_seconds": 0.125, "http_status": 402, "error_type": "ModelServiceError",
    "request_id": "provider-request-123", "usage": {"prompt_tokens": 7},
    "error_message": "PRIVATE_PROVIDER_BODY sk-private-secret PRIVATE_CLAIM",
    "prompt": "PRIVATE_CLAIM", "api_key": "sk-private-secret",
}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.gateway = load_gateway(self.stack)

    def test_snapshot_survives_json_checkpoint_roundtrip(self):
        snapshot = self.gateway.configuration_snapshot()
        self.assertEqual(snapshot, json.loads(json.dumps(snapshot)))

    def test_empty_fallbacks_survive_json_roundtrip(self):
        self.gateway.configure(self.gateway.replace(self.gateway.config(), text_fallbacks=()))
        snapshot = self.gateway.configuration_snapshot()
        self.assertEqual(snapshot, json.loads(json.dumps(snapshot)))

    def test_real_model_change_is_still_detected(self):
        saved = json.loads(json.dumps(self.gateway.configuration_snapshot()))
        self.gateway.configure(self.gateway.replace(self.gateway.config(), text_model="vendor/new-model"))
        self.assertNotEqual(saved, self.gateway.configuration_snapshot())


class DiagnosticTests(unittest.TestCase):
    def test_actual_gateway_fields_reach_event_log(self):
        with patch.object(observability, "configure_logging") as configure:
            observability.record_model_call(ERROR_CALL)
        record = configure.return_value.log.call_args.kwargs["extra"]["event_fields"]
        self.assertEqual(record["provider"], "openrouter")
        self.assertEqual(record["model"], "vendor/text-model")
        self.assertEqual(record["http_status"], 402)
        self.assertEqual(record["error_kind"], "ModelServiceError")
        self.assertEqual(record["elapsed_ms"], 125)
        self.assertEqual(record["provider_request_id"], "provider-request-123")
        self.assertEqual(configure.return_value.log.call_args.args[0], logging.WARNING)

    def test_summary_excludes_raw_errors_prompts_and_credentials(self):
        record = observability.model_call_summary(ERROR_CALL)
        serialized = json.dumps(record)
        for private in ("PRIVATE_PROVIDER_BODY", "PRIVATE_CLAIM", "sk-private-secret"):
            self.assertNotIn(private, serialized)
        self.assertEqual(record["usage"], {"prompt_tokens": 7})

    def test_summary_uses_actual_model_but_keeps_requested_model(self):
        record = observability.model_call_summary(dict(ERROR_CALL, actual_model="vendor/actual-model", status="ok"))
        self.assertEqual(record["model"], "vendor/actual-model")
        self.assertEqual(record["requested_model"], "vendor/text-model")
        self.assertEqual(record["actual_model"], "vendor/actual-model")

    def test_legacy_record_names_are_still_readable(self):
        record = observability.model_call_summary({"provider": "gemini", "model": "old-model", "elapsed_ms": 25})
        self.assertEqual((record["provider"], record["model"], record["elapsed_ms"]), ("gemini", "old-model", 25))

    def test_usage_does_not_copy_unexpected_provider_fields(self):
        record = observability.model_call_summary(dict(ERROR_CALL, usage={"prompt_tokens": 7, "private": "PRIVATE_CLAIM"}))
        self.assertEqual(record["usage"], {"prompt_tokens": 7})

    def test_success_and_cooldown_log_levels(self):
        for status, level in (("ok", logging.INFO), ("cooldown", logging.WARNING)):
            with self.subTest(status=status), patch.object(observability, "configure_logging") as configure:
                observability.record_model_call(dict(ERROR_CALL, status=status))
                self.assertEqual(configure.return_value.log.call_args.args[0], level)


class CustomerWorkerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": self.temp}))
        self.events = self.stack.enter_context(patch.object(observability, "event"))
        self.gateway = load_gateway(self.stack)
        self.task = load_module(self.stack, "_customer_task_under_test", APP / "pilot_task.py")
        self.stack.enter_context(patch.object(self.task, "code_version", return_value="fixture-version"))
        store.initialize()
        self.access = store.Access("fixture-user", "a" * 32, time.time() + 3600)
        self.doc, self.job, self.token = "b" * 32, "c" * 32, "d" * 32
        self.workspace = store.workspace_dir(self.access.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        data = b"Public fixture content; PDF parsing is outside these tests."
        self.sha = hashlib.sha256(data).hexdigest()
        path = store.file_path(self.access.workspace, "data/documents", self.doc, "original.pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        payload = {"claim": "PRIVATE_CLAIM", "documents": [self.doc], "versions": {self.doc: self.sha}}
        checkpoint = {"code_version": "fixture-version", "models": self.gateway.configuration_snapshot()}
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", (self.access.workspace, "Fixture"))
            c.execute("INSERT INTO members VALUES(?,?)", (self.access.principal, self.access.workspace))
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      (self.doc, self.access.workspace, "fixture.pdf", self.sha, "ready", time.time()))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,attempts,created,updated,run_token,checkpoint) VALUES(?,?,?,?,?,'running','Fixture',2,?,?,?,?)",
                      (self.job, self.access.workspace, self.access.principal, "verify", json.dumps(payload), time.time(), time.time(), self.token, json.dumps(checkpoint)))
        parser = types.ModuleType("verification.claim_parser")
        parser.parse_claims = MagicMock(return_value=[{"claim_text": "fixture", "operation": "level"}])
        verifier = types.ModuleType("pipelines.verification_pipeline")
        verifier._verify_one = MagicMock(return_value={"status": "complete", "verdict": "SUPPORTED"})
        self.parser, self.verifier = parser.parse_claims, verifier._verify_one
        self.stack.enter_context(patch.dict(sys.modules, {
            "verification.claim_parser": parser, "pipelines.verification_pipeline": verifier,
        }))

    def run_job(self):
        with patch("os.getcwd", return_value=str(self.workspace)):
            self.task.run(self.job, self.token)
        row = store.get_job(self.access, self.job)
        row["result"] = json.loads(row["result"])
        row["checkpoint"] = json.loads(row["checkpoint"])
        return row

    def update_checkpoint(self, **updates):
        with store.db() as c:
            checkpoint = json.loads(c.execute("SELECT checkpoint FROM jobs WHERE id=?", (self.job,)).fetchone()[0])
            checkpoint.update(updates)
            c.execute("UPDATE jobs SET checkpoint=? WHERE id=?", (json.dumps(checkpoint), self.job))

    def emit_error(self):
        self.gateway._record(dict(ERROR_CALL))

    def test_unchanged_saved_config_allows_review_to_finish(self):
        result = self.run_job()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["result"]["results"][0]["verdict"], "SUPPORTED")
        self.parser.assert_called_once()

    def test_real_model_change_pauses_before_provider_call(self):
        self.gateway.configure(self.gateway.replace(self.gateway.config(), text_model="vendor/new-model"))
        result = self.run_job()
        self.assertEqual(result["status"], "paused")
        self.assertIn("Model settings changed", result["stage"])
        self.parser.assert_not_called()

    def test_real_code_change_still_requires_new_review(self):
        self.update_checkpoint(code_version="older-version")
        result = self.run_job()
        self.assertEqual(result["status"], "paused")
        self.assertIn("Code changed", result["stage"])
        self.parser.assert_not_called()

    def test_parser_failure_keeps_safe_provider_diagnostics(self):
        def fail(_):
            self.emit_error()
            return [{"parsing_error": ERROR_CALL["error_message"], "error_kind": "ModelServiceError"}]
        self.parser.side_effect = fail
        row = self.run_job()
        self.assertEqual(row["status"], "paused")
        result = row["result"]
        self.assertEqual(result["error_stage"], "claim_parser")
        self.assertEqual(result["error_kind"], "ModelServiceError")
        call = result["model_calls"][0]
        self.assertEqual((call["provider"], call["model"], call["http_status"]), ("openrouter", "vendor/text-model", 402))
        self.assertEqual(row["checkpoint"]["parsing_calls"], result["model_calls"])
        self.assertEqual(result["results"], [])
        self.verifier.assert_not_called()
        for private in ("PRIVATE_PROVIDER_BODY", "PRIVATE_CLAIM", "sk-private-secret"):
            self.assertNotIn(private, json.dumps(result))

    def test_preflight_failure_keeps_error_class_without_inventing_a_request(self):
        self.parser.return_value = [{"parsing_error": "PRIVATE_PROVIDER_BODY", "error_kind": "ModelConfigurationError"}]
        row = self.run_job()
        self.assertEqual(row["status"], "paused")
        self.assertEqual(row["result"]["error_kind"], "ModelConfigurationError")
        self.assertEqual(row["result"]["model_calls"], [])

    def test_verification_failure_keeps_safe_calls_and_saved_subclaims(self):
        saved = {"status": "complete", "verdict": "SUPPORTED", "claim_text": "First fixture"}
        second = {"claim_text": "Second fixture", "operation": "level"}
        self.update_checkpoint(parsed=[{"claim_text": "First fixture"}, second], results=[saved])
        def fail(*args):
            self.emit_error()
            return {"status": "error", "error_stage": "numeric", "error_kind": "ModelServiceError", "reason": ERROR_CALL["error_message"]}
        self.verifier.side_effect = fail
        row = self.run_job()
        self.assertEqual(row["status"], "paused")
        self.assertEqual(row["result"]["results"], [saved])
        self.assertEqual(row["result"]["error_stage"], "numeric")
        self.assertEqual(row["result"]["model_calls"][0]["http_status"], 402)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(row["result"]))
        self.parser.assert_not_called()
        self.assertEqual(self.verifier.call_args.args[0], second)


if __name__ == "__main__":
    unittest.main()
