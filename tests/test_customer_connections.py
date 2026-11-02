"""OpenRouter BYOK checks. HTTP is mocked; encryption tests require real Fernet."""
from contextlib import ExitStack
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from pilot import store, model_connections as connections
from pilot.exports import exports

try:
    from cryptography.fernet import Fernet
    Fernet(Fernet.generate_key()).encrypt(b"public-test-fixture")
except (ImportError, OSError):
    Fernet = None

KEY_A = "sk-or-user-a-public-fixture"
KEY_B = "sk-or-user-b-public-fixture"
TEXT = "vendor/reasoning:free"
VISION = "vendor/vision:free"
MASTER = base64.urlsafe_b64encode(b"x" * 32).decode()


def endpoint(vision=True, parameters=None):
    return {"architecture": {"input_modalities": ["text", "image"] if vision else ["text"]},
            "endpoints": [{"max_completion_tokens": 32768,
                           "supported_parameters": parameters if parameters is not None else
                           ["structured_outputs", "response_format", "max_tokens"]}]}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        limits = patch.dict(os.environ, {"CUSTOMER_MAX_OUTPUT_TOKENS": "8192", "CUSTOMER_MODEL_TIMEOUT_SECONDS": "120"})
        limits.start(); self.addCleanup(limits.stop)

    def test_exact_model_ids_and_no_server_fallbacks(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "LLM_ALLOW_FALLBACKS": "true"}):
            selected = connections.model_config(TEXT, VISION)
        self.assertEqual(selected["provider"], "openrouter")
        self.assertFalse(selected["allow_fallbacks"])
        self.assertEqual(selected["text_fallbacks"], [])
        self.assertEqual(selected, json.loads(json.dumps(selected)))
        for invalid in ("model", "https://example.com/model", "vendor/model with spaces"):
            with self.subTest(invalid=invalid), self.assertRaises(connections.ModelConnectionError):
                connections.model_config(invalid)

    def test_supported_text_and_vision_models_pass_read_only_checks(self):
        with patch.object(connections, "_get", side_effect=[{}, endpoint(), endpoint()]) as get:
            connections.validate(KEY_A, connections.model_config(TEXT, VISION))
        self.assertEqual(get.call_count, 3)
        self.assertTrue(all(call.args[1] == KEY_A for call in get.call_args_list))

    def test_same_multimodal_model_fetches_endpoint_metadata_once(self):
        with patch.object(connections, "_get", side_effect=[{}, endpoint()]) as get:
            connections.validate(KEY_A, connections.model_config(TEXT, TEXT))
        self.assertEqual(get.call_count, 2)

    def test_plain_json_mode_is_insufficient(self):
        with patch.object(connections, "_get", side_effect=[{}, endpoint(parameters=["response_format", "max_tokens"])]):
            with self.assertRaises(connections.ModelConnectionError):
                connections.validate(KEY_A, connections.model_config(TEXT))

    def test_no_endpoints_and_small_output_limits_are_rejected(self):
        small = endpoint(); small["endpoints"][0]["max_completion_tokens"] = 100
        for data in ({"endpoints": []}, small):
            with self.subTest(data=data), patch.object(connections, "_get", side_effect=[{}, data]):
                with self.assertRaises(connections.ModelConnectionError):
                    connections.validate(KEY_A, connections.model_config(TEXT))

    def test_text_only_model_cannot_be_selected_for_vision(self):
        with patch.object(connections, "_get", side_effect=[{}, endpoint(False)]):
            with self.assertRaises(connections.ModelConnectionError):
                connections.validate(KEY_A, connections.model_config(TEXT, TEXT))

    def test_invalid_keys_are_rejected_before_http(self):
        for key in ("", "short", KEY_A + "\n", "non-ascii-\u00e9" * 4):
            with self.subTest(key=key), patch.object(connections, "_get") as get:
                with self.assertRaises(connections.ModelConnectionError):
                    connections.validate(key, connections.model_config(TEXT))
                get.assert_not_called()

    def test_http_errors_never_copy_key_or_provider_body(self):
        http = types.ModuleType("httpx")
        response = types.SimpleNamespace(status_code=401, json=MagicMock(return_value={"error": KEY_A}))
        http.get = MagicMock(return_value=response)
        with patch.dict(sys.modules, {"httpx": http}):
            with self.assertRaises(connections.ModelConnectionError) as error:
                connections._get("/key", KEY_A)
        self.assertIn("401", str(error.exception))
        self.assertNotIn(KEY_A, str(error.exception))
        response.json.assert_not_called()
        self.assertEqual(http.get.call_args.kwargs["headers"]["Authorization"], "Bearer " + KEY_A)
        self.assertFalse(http.get.call_args.kwargs["follow_redirects"])

    def test_connection_errors_give_actionable_status_specific_hints(self):
        http = types.ModuleType("httpx")
        for status, hint in ((401, "API key"), (402, "credits"), (404, "model ID"), (429, "rate limit"), (503, "Try again later")):
            response = types.SimpleNamespace(status_code=status, json=MagicMock())
            http.get = MagicMock(return_value=response)
            with self.subTest(status=status), patch.dict(sys.modules, {"httpx": http}):
                with self.assertRaises(connections.ModelConnectionError) as error:
                    connections._get("/key", KEY_A)
                self.assertIn(hint, str(error.exception))
                response.json.assert_not_called()


class TemporaryStore(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": self.temp,
                                                       "CUSTOMER_KEY_ENCRYPTION_KEY": MASTER,
                                                       "CUSTOMER_MAX_OUTPUT_TOKENS": "8192",
                                                       "CUSTOMER_MODEL_TIMEOUT_SECONDS": "120"}))
        store.initialize()
        self.a = store.Access("user-a", "a" * 32, time.time() + 3600)
        self.b = store.Access("user-b", "a" * 32, time.time() + 3600)
        self.models = connections.model_config(TEXT, VISION)
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", (self.a.workspace, "Shared fixture"))
            for user in (self.a, self.b):
                c.execute("INSERT INTO members VALUES(?,?)", (user.principal, user.workspace))
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      ("d" * 32, self.a.workspace, "fixture.pdf", "hash", "ready", time.time()))

    def seed(self, user, version, models=None):
        with store.db() as c:
            c.execute("INSERT INTO model_connections VALUES(?,?,?,?,?)",
                      (user.principal, version, "opaque-encrypted-fixture", json.dumps(models or self.models), time.time()))

    def submit(self, user=None):
        return store.submit_review(user or self.a, "Fixture", "Public fixture claim", ["d" * 32])


class ConnectionStoreTests(TemporaryStore):
    def test_no_connection_blocks_new_billable_work(self):
        with self.assertRaises(connections.ModelConnectionError): self.submit()
        with store.db() as c: self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_job_snapshots_its_creator_connection_without_secret(self):
        self.seed(self.a, "a-version"); self.seed(self.b, "b-version")
        job = store.get_job(self.a, self.submit())
        binding = json.loads(job["payload"])["openrouter"]
        self.assertEqual(binding["connection_version"], "a-version")
        self.assertEqual(binding["models"], self.models)
        self.assertNotIn("opaque-encrypted-fixture", job["payload"])
        self.assertEqual(job["principal"], self.a.principal)

    def test_processing_requires_a_vision_model(self):
        self.seed(self.a, "a-version", connections.model_config(TEXT))
        with store.db() as c, self.assertRaises(connections.ModelConnectionError):
            store.queue(c, self.a, "process", {"document_id": "d" * 32})

    def test_shared_workspace_cannot_retry_another_users_billable_job(self):
        self.seed(self.a, "a-version"); self.seed(self.b, "b-version")
        job = self.submit()
        with store.db() as c: c.execute("UPDATE jobs SET status='paused' WHERE id=?", (job,))
        with self.assertRaises(PermissionError): store.resume(self.b, job)
        self.assertEqual(store.get_job(self.a, job)["status"], "paused")

    def test_creator_can_retry_with_unchanged_connection(self):
        self.seed(self.a, "a-version")
        job = self.submit()
        with store.db() as c: c.execute("UPDATE jobs SET status='paused',attempts=1 WHERE id=?", (job,))
        store.resume(self.a, job)
        self.assertEqual(store.get_job(self.a, job)["status"], "queued")

    def test_key_replacement_blocks_old_job_retry(self):
        self.seed(self.a, "a-version")
        job = self.submit()
        with store.db() as c:
            c.execute("UPDATE jobs SET status='paused' WHERE id=?", (job,))
            c.execute("UPDATE model_connections SET version='new-version' WHERE principal=?", (self.a.principal,))
        with self.assertRaises(ValueError): store.resume(self.a, job)

    def test_model_changes_do_not_rewrite_queued_model_choices(self):
        self.seed(self.a, "a-version")
        job = self.submit()
        with store.db() as c:
            c.execute("UPDATE model_connections SET models=? WHERE principal=?",
                      (json.dumps(connections.model_config("vendor/new-model", VISION)), self.a.principal))
        selected = json.loads(store.get_job(self.a, job)["payload"])["openrouter"]["models"]
        self.assertEqual(selected["text_model"], TEXT)

    def test_metadata_and_remove_are_scoped_to_the_current_user(self):
        self.seed(self.a, "a-version"); self.seed(self.b, "b-version")
        self.assertNotIn("secret", connections.metadata(self.a))
        connections.remove(self.a)
        self.assertIsNone(connections.metadata(self.a))
        self.assertEqual(connections.metadata(self.b)["version"], "b-version")

    def test_missing_or_cross_user_connections_fail_before_decryption(self):
        self.seed(self.a, "a-version")
        payload = {"openrouter": {"connection_version": "a-version", "models": self.models}}
        with self.assertRaises(connections.ModelConnectionError): connections.credentials_for_job(self.b, payload)
        with self.assertRaises(connections.ModelConnectionError): connections.credentials_for_job(self.a, {})

    def test_source_deletion_does_not_require_an_openrouter_key(self):
        job = store.request_delete(self.a, "d" * 32)
        self.assertNotIn("openrouter", json.loads(store.get_job(self.a, job)["payload"]))

    def test_expired_access_cannot_read_or_remove_a_connection(self):
        expired = store.Access(self.a.principal, self.a.workspace, time.time() - 10)
        for action in (connections.metadata, connections.remove):
            with self.subTest(action=action.__name__), self.assertRaises(PermissionError): action(expired)

    def paused_processing(self):
        with store.db() as c:
            c.execute("UPDATE documents SET status='error' WHERE id=?", ("d" * 32,))
            payload = {"document_id": "d" * 32, "options": {"enable_ocr": True, "visual_limit": 1}}
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,created,updated) VALUES(?,?,?,?,?,'paused','Fixture',?,?)",
                      ("e" * 32, self.a.workspace, self.a.principal, "process", json.dumps(payload), time.time(), time.time()))

    def test_new_processing_uses_callers_key_and_reuses_existing_source(self):
        self.paused_processing(); self.seed(self.b, "b-version")
        job = store.restart_processing(self.b, "e" * 32)
        row = store.get_job(self.b, job)
        payload = json.loads(row["payload"])
        self.assertEqual(row["principal"], self.b.principal)
        self.assertEqual(payload["document_id"], "d" * 32)
        self.assertEqual(payload["openrouter"]["connection_version"], "b-version")
        with self.assertRaises(ValueError): store.restart_processing(self.b, "e" * 32)

    def test_ready_sources_are_not_automatically_reprocessed(self):
        self.paused_processing(); self.seed(self.a, "a-version")
        with store.db() as c: c.execute("UPDATE documents SET status='ready' WHERE id=?", ("d" * 32,))
        with self.assertRaises(ValueError): store.restart_processing(self.a, "e" * 32)

    def test_new_processing_rejects_another_workspaces_job(self):
        self.paused_processing()
        foreign = store.Access("foreign", "f" * 32, time.time() + 3600)
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", (foreign.workspace, "Foreign"))
            c.execute("INSERT INTO members VALUES(?,?)", (foreign.principal, foreign.workspace))
        with self.assertRaises(ValueError): store.restart_processing(foreign, "e" * 32)


@unittest.skipUnless(Fernet is not None, "Real cryptography/Fernet is unavailable in this interpreter; run in the project environment")
class RealEncryptionTests(TemporaryStore):
    def save(self, user, key=KEY_A, model=TEXT):
        with patch.object(connections, "validate"):
            connections.save(user, key, model, VISION)

    def test_encrypted_at_rest_and_correct_key_only_for_creator(self):
        self.save(self.a); self.save(self.b, KEY_B)
        job_a = store.get_job(self.a, self.submit(self.a)); job_b = store.get_job(self.b, self.submit(self.b))
        with store.db() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM model_connections")]
        self.assertNotIn(KEY_A, json.dumps(rows)); self.assertNotIn(KEY_B, json.dumps(rows))
        self.assertEqual(connections.credentials_for_job(self.a, json.loads(job_a["payload"]))[0], KEY_A)
        self.assertEqual(connections.credentials_for_job(self.b, json.loads(job_b["payload"]))[0], KEY_B)
        with self.assertRaises(connections.ModelConnectionError):
            connections.credentials_for_job(self.b, json.loads(job_a["payload"]))

    def test_ciphertext_cannot_be_swapped_between_users(self):
        self.save(self.a); self.save(self.b, KEY_B)
        with store.db() as c:
            secret = c.execute("SELECT secret FROM model_connections WHERE principal=?", (self.a.principal,)).fetchone()[0]
            c.execute("UPDATE model_connections SET secret=? WHERE principal=?", (secret, self.b.principal))
        payload = {"openrouter": {"connection_version": connections.metadata(self.b)["version"], "models": self.models}}
        with self.assertRaises(connections.ModelConnectionError): connections.credentials_for_job(self.b, payload)

    def test_same_key_preserves_version_and_keeps_original_job_models(self):
        self.save(self.a)
        job = store.get_job(self.a, self.submit())
        version = connections.metadata(self.a)["version"]
        self.save(self.a, "", "vendor/new-model")
        self.assertEqual(connections.metadata(self.a)["version"], version)
        self.assertEqual(connections.credentials_for_job(self.a, json.loads(job["payload"]))[1]["text_model"], TEXT)

    def test_key_rotation_and_removal_invalidate_previous_jobs(self):
        self.save(self.a)
        payload = json.loads(store.get_job(self.a, self.submit())["payload"])
        self.save(self.a, KEY_B)
        with self.assertRaises(connections.ModelConnectionError): connections.credentials_for_job(self.a, payload)
        connections.remove(self.a)
        with self.assertRaises(connections.ModelConnectionError): connections.credentials_for_job(self.a, payload)

    def test_missing_or_wrong_master_key_fails_safely(self):
        self.save(self.a)
        payload = json.loads(store.get_job(self.a, self.submit())["payload"])
        for master in ("", "invalid", base64.urlsafe_b64encode(b"y" * 32).decode()):
            with self.subTest(master_kind=bool(master)), patch.dict(os.environ, {"CUSTOMER_KEY_ENCRYPTION_KEY": master}):
                with self.assertRaises(connections.ModelConnectionError) as error:
                    connections.credentials_for_job(self.a, payload)
                self.assertNotIn(KEY_A, str(error.exception))

    def test_exports_and_checkpoints_never_contain_the_raw_key(self):
        self.save(self.a)
        job = self.submit()
        csv_data, report, raw = exports(self.a, job)
        for output in (csv_data, report, raw): self.assertNotIn(KEY_A, output)
        self.assertNotIn("secret", json.loads(raw)["review"])

    def test_no_lost_update_if_connection_removed_during_validation(self):
        self.save(self.a)
        with patch.object(connections, "validate", side_effect=lambda *args: connections.remove(self.a)):
            with self.assertRaises(connections.ModelConnectionError):
                connections.save(self.a, "", TEXT, VISION)
        self.assertIsNone(connections.metadata(self.a))


if __name__ == "__main__": unittest.main()
