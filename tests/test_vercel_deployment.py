"""Cloud startup, upload and launch contracts; no hosted services or model calls."""
import base64
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "app"))
from deploy import entrypoint, preflight
from pilot import store


def cloud_config():
    return {
        "CUSTOMER_STORAGE": "supabase", "BETA_PRODUCTION": "1", "CUSTOMER_MAX_UPLOAD_MB": "4",
        "SUPABASE_URL": "https://fixture.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture",
        "SUPABASE_SITE_URL": "https://fixture.vercel.app", "BETA_SUPPORT_EMAIL": "beta@example.com",
        "CUSTOMER_KEY_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"x"*32).decode(),
        "GOOGLE_API_KEY": "fixture", "RERANK_MODEL_REVISION": "a"*40,
    }


class CloudStartupTests(unittest.TestCase):
    def test_cloud_profile_accepts_valid_configuration(self):
        self.assertEqual(preflight.configuration_errors(cloud_config(), cloud_only=True), [])

    def test_cloud_image_cannot_silently_fall_back_to_ephemeral_local_data(self):
        for mode in (None, "local"):
            config = cloud_config()
            if mode is None:
                config.pop("CUSTOMER_STORAGE")
            else:
                config["CUSTOMER_STORAGE"] = mode
            marker = MagicMock()
            marker.is_file.return_value = True
            with self.subTest(mode=mode), patch.object(preflight, "CLOUD_ONLY_MARKER", marker), \
                    patch("pilot.cloud_db.initialize") as initialize:
                errors = preflight.check(config)
                self.assertIn("local fallback is disabled", "\n".join(errors))
                initialize.assert_not_called()

    def test_vercel_guard_also_applies_without_image_marker(self):
        config = {**cloud_config(), "VERCEL": "1", "CUSTOMER_STORAGE": "local"}
        self.assertIn("local fallback is disabled", "\n".join(preflight.configuration_errors(config)))

    def test_cloud_upload_cap_and_production_checks_cannot_be_disabled(self):
        for changes, message in (({"CUSTOMER_MAX_UPLOAD_MB": "5"}, "at most 4"),
                                 ({"BETA_PRODUCTION": "0"}, "BETA_PRODUCTION=1"),
                                 ({"CUSTOMER_MAX_UPLOAD_MB": "0"}, "positive integer"),
                                 ({"BETA_SUPPORT_EMAIL": ""}, "support")):
            with self.subTest(changes=changes):
                self.assertIn(message, "\n".join(preflight.configuration_errors({**cloud_config(), **changes}, cloud_only=True)))

    def test_local_profile_retains_ten_mb_limit(self):
        config = {**cloud_config(), "CUSTOMER_STORAGE": "local", "BETA_PRODUCTION": "0", "CUSTOMER_MAX_UPLOAD_MB": "10"}
        self.assertEqual(preflight.configuration_errors(config), [])

    def test_entrypoint_passes_configured_limit_and_host_port_to_streamlit(self):
        for limit in ("4", "10"):
            with self.subTest(limit=limit), patch.dict(os.environ, {"CUSTOMER_MAX_UPLOAD_MB": limit, "PORT": "8501"}), \
                    patch.object(sys, "argv", ["entrypoint.py", "web"]), \
                    patch.object(entrypoint, "check", return_value=[]), patch.object(entrypoint.os, "execv") as launch:
                entrypoint.main()
                command = launch.call_args.args[1]
                self.assertIn("--server.maxUploadSize="+limit, command)
                self.assertIn("--server.port=8501", command)
                self.assertIn("app/customer_main.py", command)
                self.assertIn("--server.enableXsrfProtection=true", command)

    def test_worker_role_does_not_start_web_and_failed_preflight_never_launches(self):
        with patch.object(sys, "argv", ["entrypoint.py", "worker"]), \
                patch.object(entrypoint, "check", return_value=[]), patch.object(entrypoint.os, "execv") as launch:
            entrypoint.main()
            self.assertEqual(launch.call_args.args[1], [sys.executable, "app/pilot_worker.py"])
        with patch.object(sys, "argv", ["entrypoint.py", "web"]), \
                patch.object(entrypoint, "check", return_value=["fixture configuration error"]), \
                patch.object(entrypoint.os, "execv") as launch, patch.object(sys, "stderr"):
            self.assertRaises(SystemExit, entrypoint.main)
            launch.assert_not_called()


class UploadLimitTests(unittest.TestCase):
    def test_four_mb_cap_rejects_oversize_before_parsing_or_saving_pdf(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "CUSTOMER_STORAGE": "local", "CUSTOMER_DATA_ROOT": temp, "CUSTOMER_MAX_UPLOAD_MB": "4"}):
            store.initialize()
            access = store.Access("fixture", "a"*32, time.time()+300)
            with store.db() as c:
                c.execute("INSERT INTO workspaces VALUES(?,?)", (access.workspace, "Fixture"))
                c.execute("INSERT INTO members VALUES(?,?)", (access.principal, access.workspace))
            pdf = types.SimpleNamespace(open=MagicMock())
            with patch.dict(sys.modules, {"pymupdf": pdf}):
                self.assertRaisesRegex(ValueError, "up to 4 MB", store.upload, access, "fixture.pdf", b"x"*(4*1024*1024+1), {})
            pdf.open.assert_not_called()
            with store.db() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM documents").fetchone()[0], 0)
                self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)


class CloudVectorDispatchTests(unittest.TestCase):
    def test_real_pgvector_adapter_is_selected_with_chroma_unavailable(self):
        runtime = types.ModuleType("services.runtime")
        for name in ("client", "normalize", "read", "retry", "write"):
            setattr(runtime, name, MagicMock())
        runtime.digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        genai = types.ModuleType("google.genai")
        genai.types = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location("retrieval.embeddings", PROJECT/"app/retrieval/embeddings.py")
        embeddings = importlib.util.module_from_spec(spec)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"services.runtime": runtime, "google.genai": genai,
                                                        "retrieval.embeddings": embeddings, "chromadb": None}))
            stack.enter_context(patch.dict(os.environ, {"GEMINI_EMBEDDING_MODEL": "gemini-embedding-001",
                                                        "GEMINI_EMBEDDING_DIMENSIONS": "768"}))
            spec.loader.exec_module(embeddings)
            from deploy.container_smoke import check_cloud_vectors
            check_cloud_vectors()
        runtime.client.assert_not_called()


class PackageAndLaunchContractTests(unittest.TestCase):
    def test_cloud_release_and_local_chroma_are_separate_image_branches(self):
        production = (PROJECT/"requirements-production.txt").read_text()
        local = (PROJECT/"requirements-local.txt").read_text()
        docker = (PROJECT/"Dockerfile").read_text()
        self.assertNotIn("chromadb==", production)
        self.assertIn("chromadb==", local)
        self.assertIn("FROM runtime AS local-release", docker)
        self.assertIn("FROM runtime AS cloud-runtime", docker)
        self.assertTrue(docker.rstrip().endswith("FROM cloud-runtime AS release"))
        self.assertNotIn("pip install", docker.split("FROM runtime AS cloud-runtime")[1].split("FROM cloud-runtime AS security-audit")[0])

    def test_worker_starts_disabled_and_uses_supervisor_entrypoint(self):
        config = json.loads((PROJECT/"vercel.json").read_text())
        self.assertEqual(config["framework"], "container")
        worker = config["daemons"]["worker"]
        self.assertEqual(worker["instances"], {"dub1": 0})
        self.assertEqual(worker["command"], ["python", "deploy/entrypoint.py", "worker"])
        self.assertEqual(config["regions"], list(worker["instances"]))

    def test_upload_allowlist_does_not_include_private_files_or_internal_dashboard(self):
        for filename in (".vercelignore", ".dockerignore"):
            with self.subTest(filename=filename):
                patterns = [p for p in (PROJECT/filename).read_text().splitlines() if p and not p.startswith("#")]
                self.assertEqual(patterns[0], "**")
                allowed = [p[1:] for p in patterns if p.startswith("!")]
                for private in (".env", "customer_data/", "data/", "pas.txt", ".streamlit/secrets.toml", ".vercel/"):
                    self.assertNotIn(private, allowed)
                self.assertIn("app/main.py", patterns)
                self.assertIn("!supabase/storage_schema.sql", patterns)


if __name__ == "__main__":
    unittest.main()
