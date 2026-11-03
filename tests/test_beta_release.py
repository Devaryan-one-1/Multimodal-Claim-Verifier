"""Source-backed numerical cases, citation boundaries and local release guards.

Provider selection is controlled. No customer data, keys or model API calls.
"""
from contextlib import ExitStack, closing
import base64
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

import test_financial_integrity as financial
import test_customer_beta as beta
import test_customer_worker as tasks

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from deploy import preflight, healthcheck
from pilot.pdf_limits import check_pages, PDFLimitError
from pilot import store
from scripts.dependency_audit import normalized_requirements

# Exact sentence checked against the saved Apple source, physical PDF page 9.
DEFERRED = ("As of June 27, 2026 and September 27, 2025, the Company had total "
            "deferred revenue of $14.9 billion and $13.7 billion, respectively.")


class SourceNumericalTests(unittest.TestCase):
    def setUp(self):
        financial.NumericalTypeTests.setUp(self)

    def evaluate(self, literal, *, operation="level", year=2026, swap=False, text=DEFERRED):
        evidence = {"id": "source", "source_evidence_id": "saved-source-id", "type": "text",
                    "document_id": "source-document", "page": 9, "content": text}
        bank = self.numeric._bank(evidence)
        def operand(name, raw):
            value = financial.SourceOperand(name=name)
            value.row_index = value.column_index = None
            value.number_index = next(v["index"] for v in bank if v["raw"] == raw)
            return value
        if operation == "growth":
            operands = [operand("old", "$13.7 billion"), operand("new", "$14.9 billion")]
        else:
            raw = "$14.9 billion" if (year == 2026) != swap else "$13.7 billion"
            operands = [operand("value", raw)]
        claim = {"operation": operation, "metric": "deferred revenue", "end_period": year,
                 "period_type": "point_in_time" if operation == "level" else "unspecified",
                 "unit": "percent" if operation == "growth" else "amount", "claimed_literal": literal}
        if operation == "growth":
            claim["start_period"] = 2025
            if swap:
                operands[0].number_index, operands[1].number_index = operands[1].number_index, operands[0].number_index
        selection = types.SimpleNamespace(applicable=True, reason="Controlled source selection", operands=operands)
        with patch.object(self.numeric, "_select", return_value=selection):
            return self.numeric.verify_numeric(claim, [evidence])

    def test_each_respective_year_resolves_its_own_amount_and_source_address(self):
        for year, literal, value in ((2026, "$14.9 billion", 14.9e9), (2025, "$13.7 billion", 13.7e9)):
            with self.subTest(year=year):
                calculation, comparison, reason, used = self.evaluate(literal, year=year)
                self.assertIsNotNone(calculation, reason)
                self.assertEqual(comparison["verdict"], "SUPPORTED")
                self.assertEqual(calculation["inputs"]["value"], value)
                self.assertEqual(calculation["result_unit"], "amount")
                # A bare dollar symbol does not establish a specific currency code.
                self.assertIsNone(calculation["currency"])
                self.assertEqual(calculation["source_inputs"]["value"]["page"], 9)
                self.assertEqual(calculation["source_inputs"]["value"]["document_id"], "source-document")
                self.assertEqual(used, ["source"])

    def test_incorrect_amount_is_contradicted_with_signed_difference(self):
        calculation, comparison, reason, _ = self.evaluate("$14.9 billion", year=2025)
        self.assertIsNotNone(calculation, reason)
        self.assertEqual(comparison["verdict"], "CONTRADICTED")
        self.assertEqual(comparison["difference"], 1.2e9)

    def test_growth_retains_raw_values_formula_and_signed_difference(self):
        for literal, verdict in (("8.8%", "SUPPORTED"), ("10%", "CONTRADICTED")):
            calculation, comparison, reason, _ = self.evaluate(literal, operation="growth")
            self.assertIsNotNone(calculation, reason)
            expected = (14.9-13.7)/13.7*100
            self.assertAlmostEqual(calculation["value"], expected)
            self.assertEqual(calculation["raw_inputs"], {"old": "$13.7 billion", "new": "$14.9 billion"})
            self.assertIn("old", calculation["formula"])
            self.assertEqual(comparison["verdict"], verdict)
            self.assertAlmostEqual(comparison["difference"], float(literal[:-1])-expected)

    def test_swapped_respective_values_never_produce_a_factual_verdict(self):
        for operation in ("level", "growth"):
            result = self.evaluate("$13.7 billion" if operation == "level" else "-8%", operation=operation, swap=True)
            financial.NumericalTypeTests.assert_unavailable(self, result)

    def test_ambiguous_comparative_prose_is_not_guessed(self):
        text = DEFERRED.replace(", respectively", "")
        financial.NumericalTypeTests.assert_unavailable(self, self.evaluate("$14.9 billion", text=text))

    def test_explicit_comparative_clauses_keep_each_value_with_its_year(self):
        item = {"content": "Revenue was $80 million in 2023 and $100 million in 2024."}
        bank = self.numeric._bank(item)
        operand = financial.SourceOperand()
        claim = {"metric": "Revenue", "end_period": 2024}
        correct = next(n for n in bank if n["raw"] == "$100 million")
        incorrect = next(n for n in bank if n["raw"] == "$80 million")
        self.numeric._text_year_check(claim, operand, item, correct)
        with self.assertRaisesRegex(ValueError, "different year"):
            self.numeric._text_year_check(claim, operand, item, incorrect)

    def test_calendar_day_cannot_be_used_as_a_financial_value(self):
        item = {"content": DEFERRED}
        chosen = next(n for n in self.numeric._bank(item) if n["raw"] == "27,")
        with self.assertRaisesRegex(ValueError, "date"):
            self.numeric._text_year_check({"metric": "deferred revenue", "end_period": 2026}, financial.SourceOperand(), item, chosen)


class CitationReleaseTests(unittest.TestCase):
    def setUp(self):
        beta.CustomerReviewScreenTests.setUp(self)
        self.documents = [{"id": "doc-a", "name": "A.pdf", "sha": "sha-a"},
                          {"id": "doc-b", "name": "B.pdf", "sha": "sha-b"}]
        self.stack.enter_context(patch.object(store, "documents", return_value=self.documents))
        self.bytes = self.stack.enter_context(patch.object(store, "document_bytes", return_value=b"fixture"))

    def evidence(self, doc="doc-a", page=9):
        return {"id": "same-id", "document_id": doc, "page": page}

    def test_foreign_invalid_and_boolean_pages_cannot_be_selected(self):
        result = {"evidence": [self.evidence("foreign"), *[self.evidence(page=p) for p in (True, 0, -1, "9")]]}
        self.real_viewer(self.access, result, "viewer")
        self.bytes.assert_not_called()
        self.assertIn("unavailable", self.st.rendered_text("warning"))

    def test_changed_pdf_version_is_refused_before_reading_bytes(self):
        self.real_viewer(self.access, {"evidence": [self.evidence()]}, "viewer", {"doc-a": "older-sha"})
        self.bytes.assert_not_called()
        self.assertIn("version", self.st.rendered_text("warning"))

    def test_out_of_range_citation_is_refused_before_rendering(self):
        pdf = MagicMock()
        pdf.__enter__.return_value = pdf
        pdf.__len__.return_value = 2
        self.ui.pymupdf.open = MagicMock(return_value=pdf)
        self.real_viewer(self.access, {"evidence": [self.evidence()]}, "viewer")
        pdf.__getitem__.assert_not_called()
        self.assertIn("outside this PDF", self.st.rendered_text("warning"))

    def test_identity_includes_document_even_when_evidence_ids_and_pages_match(self):
        pdf = MagicMock()
        pdf.__enter__.return_value = pdf
        pdf.__len__.return_value = 2
        self.ui.pymupdf.open = MagicMock(return_value=pdf)
        for document in ("doc-a", "doc-b"):
            self.st.session_state["viewsearch_results"] = [1]
            self.real_viewer(self.access, {"evidence": [self.evidence(document)]}, "view")
            self.assertNotIn("viewsearch_results", self.st.session_state)
            self.assertIn(document, self.st.session_state["viewcitation_signature"])

    def test_calculation_input_link_selects_exact_source_document_and_page(self):
        evidence = [self.evidence(), self.evidence("doc-b", 12)]
        calculation = {"inputs": {"value": 14.9e9}, "raw_inputs": {"value": "$14.9 billion"},
                       "input_metadata": {"value": {"currency": "USD"}},
                       "source_inputs": {"value": {"evidence_id": "same-id", "document_id": "doc-b", "page": 12}}}
        self.st.pressed.add("View input page: value")
        self.ui.calculation_sources(self.access, {"calculation": calculation, "evidence": evidence}, "input", "view")
        self.assertEqual(self.st.session_state["viewcitation"], 1)
        self.assertEqual(self.st.session_state["viewpage"], 12)
        text = self.st.rendered_text("dataframe")
        for value in ("USD", "14.9 billion", "B.pdf", "12"):
            self.assertIn(value, text)


class PDFReleaseTests(unittest.TestCase):
    def pdf(self, width=612, height=792, pages=1, locked=False):
        return types.SimpleNamespace(is_pdf=True, needs_pass=locked, page_count=pages,
                                     load_page=lambda _: types.SimpleNamespace(rect=types.SimpleNamespace(width=width, height=height)))

    def test_letter_page_passes_but_oversized_nonfinite_or_invalid_pages_do_not(self):
        with patch.dict(os.environ, {"CUSTOMER_MAX_PAGES": "500", "CUSTOMER_MAX_PAGE_PIXELS": "25000000", "CUSTOMER_MAX_RENDER_DIMENSION": "10000"}):
            check_pages(self.pdf())
            for values in ({"width": 10000}, {"width": float("nan")}, {"height": -1}, {"pages": 501}, {"pages": 0}, {"locked": True}):
                with self.subTest(values=values), self.assertRaises(PDFLimitError):
                    check_pages(self.pdf(**values))


class WorkerPDFReleaseTests(unittest.TestCase):
    def setUp(self):
        tasks.CustomerWorkerTests.setUp(self)

    def test_worker_rechecks_pdf_limits_before_processing(self):
        pipeline = types.ModuleType("pipelines.document_pipeline")
        pipeline.process_document = MagicMock()
        with store.db() as c:
            c.execute("UPDATE jobs SET kind='process',payload=? WHERE id=?",
                      (json.dumps({"document_id": self.doc, "options": {}}), self.job))
        with patch.dict(sys.modules, {"pipelines.document_pipeline": pipeline}), patch.object(self.task, "check_pdf", side_effect=PDFLimitError("oversized")):
            row = tasks.CustomerWorkerTests.run_job(self)
        pipeline.process_document.assert_not_called()
        self.assertEqual(row["status"], "failed")
        self.assertNotIn("verdict", row["result"])


class DeploymentConfigurationTests(unittest.TestCase):
    def test_audit_uses_exact_runtime_versions_including_cpu_torch(self):
        self.assertEqual(normalized_requirements("torch==2.14.0+cpu\nhttpx==0.28.1\n"), "httpx==0.28.1\ntorch==2.14.0\n")
        for unsafe in ("", "httpx>=0.28", "package @ file:///somewhere"):
            with self.subTest(snapshot=unsafe), self.assertRaises(ValueError):
                normalized_requirements(unsafe)
    def config(self):
        return {"SUPABASE_URL": "https://fixture.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture",
                "SUPABASE_SITE_URL": "http://localhost:8501", "GOOGLE_API_KEY": "fixture-key",
                "CUSTOMER_KEY_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"x"*32).decode(),
                "RERANK_MODEL_REVISION": "a"*40}

    def test_local_configuration_passes_and_public_production_requires_https(self):
        env = self.config()
        self.assertEqual(preflight.configuration_errors(env), [])
        env["BETA_PRODUCTION"] = "1"
        self.assertTrue(preflight.configuration_errors(env))
        env["SUPABASE_SITE_URL"] = "https://beta.example.com"
        self.assertEqual(preflight.configuration_errors(env), [])

    def test_operator_key_service_role_bad_limits_and_unpinned_model_are_rejected_without_echo(self):
        for name, value in (("OPENROUTER_API_KEY", "private-server-key"), ("SUPABASE_PUBLISHABLE_KEY", "private-service-role"),
                            ("CUSTOMER_KEY_ENCRYPTION_KEY", "private-invalid"), ("CUSTOMER_MAX_PAGES", "0"),
                            ("CUSTOMER_MAX_PAGE_PIXELS", "bad-limit"), ("RERANK_MODEL_REVISION", "main")):
            with self.subTest(name=name):
                errors = preflight.configuration_errors({**self.config(), name: value})
                self.assertTrue(errors)
                self.assertNotIn(value, "\n".join(errors))

    def test_worker_health_requires_recent_heartbeat_and_never_creates_a_missing_database(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": temp}):
            path = Path(temp)/"control.sqlite3"
            self.assertFalse(healthcheck.healthy("worker"))
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as c:
                c.execute("CREATE TABLE worker_status(id INTEGER, heartbeat REAL)")
                c.execute("INSERT INTO worker_status VALUES(1,?)", (time.time(),)); c.commit()
            self.assertTrue(healthcheck.healthy("worker"))
            with closing(sqlite3.connect(path)) as c:
                c.execute("UPDATE worker_status SET heartbeat=?", (time.time()-60,)); c.commit()
            self.assertFalse(healthcheck.healthy("worker"))

    def test_reranker_loads_pinned_safe_offline_model_once(self):
        with ExitStack() as stack:
            encoder = types.ModuleType("sentence_transformers")
            encoder.CrossEncoder = MagicMock()
            stack.enter_context(patch.dict(sys.modules, {"sentence_transformers": encoder}))
            stack.enter_context(patch.dict(os.environ, {"RERANK_MODEL_NAME": "public/model", "RERANK_MODEL_REVISION": "a"*40, "HF_HUB_OFFLINE": "1"}))
            reranker = financial.load_module(stack, "_release_reranker", PROJECT/"app/retrieval/reranker.py")
            self.assertIs(reranker._get_model(), reranker._get_model())
            encoder.CrossEncoder.assert_called_once_with("public/model", revision="a"*40, trust_remote_code=False,
                                                        device="cpu", local_files_only=True, model_kwargs={"use_safetensors": True})


class WorkerShutdownTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": self.temp}))
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = MagicMock()
        self.stack.enter_context(patch.dict(sys.modules, {"dotenv": dotenv}))
        self.worker = financial.load_module(self.stack, "_release_worker", PROJECT/"app/pilot_worker.py")
        store.initialize()
        self.job = "c"*32
        workspace = "a"*32
        store.workspace_dir(workspace).mkdir(parents=True, exist_ok=True)
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", (workspace, "Fixture"))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,created,updated) VALUES(?,?,?,'verify','{}','queued','Waiting',?,?)",
                      (self.job, workspace, "fixture", time.time(), time.time()))

    def test_sigterm_stops_child_pauses_claim_and_restores_handlers(self):
        stop = threading.Event()
        child = MagicMock()
        child.poll.return_value = None
        handlers = {}
        def register(signum, handler):
            handlers[signum] = handler
            return signal.SIG_DFL
        def launch(*args, **kwargs):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return child
        with patch.object(self.worker.signal, "signal", side_effect=register) as registered, patch.object(self.worker.subprocess, "Popen", side_effect=launch):
            self.worker.main(stop)
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)
        with store.db() as c:
            row = c.execute("SELECT status,stage,attempts FROM jobs WHERE id=?", (self.job,)).fetchone()
        self.assertEqual(row["status"], "paused")
        self.assertIn("explicit retry", row["stage"])
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(registered.call_count, 4)
        # A new exclusive lock proves normal shutdown released the old one.
        self.worker.exclusive_lock().close()

    def test_child_launch_failure_pauses_job_before_propagating(self):
        with patch.object(self.worker.subprocess, "Popen", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.worker.main(threading.Event())
        with store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0], "paused")


if __name__ == "__main__":
    unittest.main()
