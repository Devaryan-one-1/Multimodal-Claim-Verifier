"""Vision table/graph source integrity and scoped refresh; no paid provider calls."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import test_financial_integrity as financial
import test_customer_access as access_tests

APP = Path(__file__).resolve().parents[1] / "app"


def load_visual(stack):
    gateway = types.ModuleType("services.model_gateway")
    gateway.configuration_snapshot = lambda: {"vision": "fixture-vision"}
    gateway.model_name = lambda task="vision": "fixture-vision"
    gateway.structured = MagicMock(side_effect=AssertionError("Unexpected model call"))
    gateway.last_call = lambda: {"model": "fixture-vision"}
    gateway.cache_file = lambda doc, name, **kw: Path("cache")/doc/name
    modules = {"pdfplumber": types.ModuleType("pdfplumber"), "services.model_gateway": gateway}
    try:
        import pydantic
        if not hasattr(pydantic.BaseModel, "model_validate"):
            raise ImportError("Requires Pydantic v2")
    except ImportError:
        pydantic = types.ModuleType("pydantic")
        pydantic.BaseModel = object
        pydantic.Field = lambda **kw: None
        modules["pydantic"] = pydantic
    stack.enter_context(patch.dict(sys.modules, modules))
    # Keep the real JSON/digest helpers without loading credentials or SDKs.
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **kw: None
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = types.ModuleType("google.genai.types")
    google.genai = genai
    stack.enter_context(patch.dict(sys.modules, {"dotenv": dotenv, "google": google,
                                                "google.genai": genai, "google.genai.types": genai.types}))
    financial.load_module(stack, "services.runtime", APP/"services/runtime.py")
    visual = financial.load_module(stack, "_visual_tables_tests", APP/"extraction/visual_tables.py")
    visual.pdfplumber.open = MagicMock()
    return visual


def balance_table():
    # Controlled transcription of the already source-reviewed Apple page 6.
    return {"title": "Condensed Consolidated Balance Sheets",
            "unit": "(In millions, except number of shares, which are reflected in thousands, and par value)",
            "currency_quote": "$", "columns": ["Metric", "June 27, 2026", "September 27, 2025"],
            "rows": [["Cash and cash equivalents", "$31,178", "$35,934"],
                     ["Total current assets", "149,818", "147,957"],
                     ["Inventories", "11,092", "5,718"]]}


def response(tables):
    return types.SimpleNamespace(tables=tables, model_dump=lambda **kw: {"tables": tables})


class VisualTableTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.visual = load_visual(self.stack)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        previous = Path.cwd()
        os.chdir(self.temp)
        self.addCleanup(os.chdir, previous)

    def normalize(self, table=None):
        return self.visual.normalize_table(table or balance_table(), document_id="apple", page=6,
                                           bbox=[10, 20, 600, 700], image_path="table.png")

    def test_blank_cells_and_full_dates_stay_in_their_columns(self):
        table = balance_table()
        table["rows"][1][1] = ""
        normalized = self.normalize(table)
        self.assertEqual(normalized["rows"][1], ["Total current assets", "", "147,957"])
        self.assertEqual([(v["col"], v["year"], v["period"]) for v in normalized["column_map"]],
                         [(1, 2026, "point_in_time"), (2, 2025, "point_in_time")])
        self.assertEqual(normalized["page"], 6)

    def test_ragged_rows_are_rejected_instead_of_realigned(self):
        table = balance_table()
        table["rows"][1].pop(1)
        with self.assertRaisesRegex(ValueError, "different widths"):
            self.normalize(table)

    def test_currency_must_be_present_in_the_transcription(self):
        table = balance_table()
        table["currency_quote"] = "USD"
        with self.assertRaisesRegex(ValueError, "currency quote"):
            self.normalize(table)

    def test_mixed_durations_are_mapped_per_column_without_guessing(self):
        mapping = self.visual.column_map(["Metric", "Three months ended June 27, 2026",
                                         "Nine months ended June 27, 2026", "2025 / 2024"])
        self.assertEqual([v["period"] for v in mapping], ["three_months", "nine_months", None])
        self.assertIsNone(mapping[2]["year"])

    def test_amount_share_count_and_per_share_units_remain_separate(self):
        table = balance_table()
        table["rows"] += [["Shares used in computing earnings per share", "", ""],
                          ["Basic", "10,000", "11,000"], ["Earnings per share", "", ""],
                          ["Basic", "1.25", "1.05"]]
        units = self.normalize(table)["row_units"]
        self.assertEqual(units[:3], ["$ millions"]*3)
        self.assertEqual(units[4], "thousands")
        self.assertEqual(units[6], "$")

    def test_likely_pages_include_scans_and_numeric_tables_but_not_normal_prose(self):
        self.assertTrue(self.visual.likely_table_page({"text": ""}))
        self.assertTrue(self.visual.likely_table_page({"text": "Narrative", "regions": [{"type": "table"}]}))
        self.assertTrue(self.visual.likely_table_page({"text": "In millions " + "1 2 3 4 5 6 7 8 "*10}))
        self.assertFalse(self.visual.likely_table_page({"text": "A narrative page with no tabular data. "*10}))

    def test_detection_coordinates_convert_to_pdf_points_and_reject_invalid_boxes(self):
        self.assertEqual(self.visual._box_to_pdf([100, 200, 800, 900], [0, 0, 600, 800]),
                         (120, 80, 540, 640))
        for box in ([0, 0, 0, 50], [-1, 0, 10, 20], [0, 0, float("nan"), 20]):
            with self.assertRaises(ValueError):
                self.visual._box_to_pdf(box, [0, 0, 600, 800])

    def test_detection_fallback_reads_a_crop_and_native_tables_are_all_vision_read(self):
        native = types.SimpleNamespace(bbox=[20, 120, 580, 500], extract=lambda: balance_table()["rows"])
        page = types.SimpleNamespace(width=600, bbox=[0, 0, 600, 800], find_tables=lambda: [native])
        missed = types.SimpleNamespace(width=600, bbox=[0, 0, 600, 800], find_tables=lambda: [])
        pdf = MagicMock()
        pdf.__enter__.return_value = types.SimpleNamespace(pages=[page, missed])
        self.visual.pdfplumber.open.return_value = pdf
        calls = []
        def cached(schema, doc, image, task, prompt, force):
            calls.append(task)
            if task == "table-detection":
                return response([types.SimpleNamespace(box_2d=[150, 30, 700, 970])]), {}
            return response([balance_table()]), {"model": "fixture-vision"}
        with patch.object(self.visual, "_render") as render, patch.object(self.visual, "_cached", side_effect=cached):
            result = self.visual.extract_visual_tables("apple", [{"page_number": 2, "text": ""}])
        self.assertEqual(calls, ["table-read", "table-detection", "table-read"])
        self.assertEqual([table["page"] for table in result["tables"]], [1, 2])
        self.assertEqual(render.call_count, 3)
        self.assertEqual(render.call_args_list[0].args[1], (8, 30, 592, 506))
        self.assertEqual(result["visual_pages_scanned"], 1)

    def test_fallback_detection_limit_is_explicit_and_does_not_limit_native_table_reads(self):
        missed = types.SimpleNamespace(width=600, bbox=[0, 0, 600, 800], find_tables=lambda: [])
        pdf = MagicMock()
        pdf.__enter__.return_value = types.SimpleNamespace(pages=[missed])
        self.visual.pdfplumber.open.return_value = pdf
        with patch.object(self.visual, "_cached") as cached:
            result = self.visual.extract_visual_tables("apple", [{"page_number": 1, "text": ""}], visual_limit=0)
        cached.assert_not_called()
        self.assertIn("page 1 was not scanned", result["warnings"][0])

    def test_small_native_numeric_table_is_also_vision_read(self):
        native = types.SimpleNamespace(bbox=[20, 120, 100, 145], extract=lambda: [["Metric", "2026"], ["Value", "1"]])
        page = types.SimpleNamespace(width=600, bbox=[0, 0, 600, 800], find_tables=lambda: [native])
        pdf = MagicMock()
        pdf.__enter__.return_value = types.SimpleNamespace(pages=[page])
        self.visual.pdfplumber.open.return_value = pdf
        with patch.object(self.visual, "_render"), patch.object(self.visual, "_cached", return_value=(response([balance_table()]), {})) as cached:
            result = self.visual.extract_visual_tables("apple", [{"page_number": 1, "text": "Ordinary prose. "*20}], visual_limit=0)
        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(cached.call_args.args[3], "table-read")

    def test_bad_alignment_gets_one_correction_and_only_valid_responses_are_cached(self):
        image = Path("table.png")
        image.write_bytes(b"image-fixture")
        bad = balance_table()
        bad["rows"][0].pop()
        self.visual.structured = MagicMock(side_effect=[response([bad]), response([balance_table()])])
        self.visual._cached(self.visual.TablesRead, "apple", image, "table-read", "Read table", False)
        self.assertEqual(self.visual.structured.call_count, 2)
        self.assertIn("LOCAL VALIDATION ERROR", self.visual.structured.call_args.args[1])
        saved = json.loads(next(Path("cache/apple").glob("*.json")).read_text())
        self.assertEqual(saved["result"]["tables"][0]["rows"], balance_table()["rows"])

    def test_valid_cache_reuse_and_model_change_invalidation(self):
        image = Path("table.png")
        image.write_bytes(b"image-fixture")
        self.visual.structured = MagicMock(return_value=response([balance_table()]))
        with patch.object(self.visual.TablesRead, "model_validate", side_effect=lambda data: response(data["tables"]), create=True):
            for _ in range(2):
                self.visual._cached(self.visual.TablesRead, "apple", image, "table-read", "Read", False)
        self.assertEqual(self.visual.structured.call_count, 1)
        with patch.object(self.visual, "configuration_snapshot", return_value={"vision": "another-model"}):
            self.visual._cached(self.visual.TablesRead, "apple", image, "table-read", "Read", False)
        self.assertEqual(self.visual.structured.call_count, 2)

    def test_provider_failure_is_not_cached_as_an_empty_table(self):
        from services.verification_errors import VerificationFailure
        image = Path("table.png")
        image.write_bytes(b"image-fixture")
        self.visual.structured = MagicMock(side_effect=VerificationFailure("Quota exceeded", stage="vision"))
        with self.assertRaises(VerificationFailure):
            self.visual._cached(self.visual.TablesRead, "apple", image, "table-read", "Read", False)
        self.assertEqual(list(Path("cache").rglob("*.json")), [])

    def test_visual_rows_remain_searchable_with_headers_units_and_source_addresses(self):
        builder = financial.load_module(self.stack, "_visual_evidence_tests", APP/"extraction/build_evidence.py")
        chunker = financial.load_module(self.stack, "_visual_chunk_tests", APP/"extraction/chunker.py")
        self.stack.enter_context(patch.dict(sys.modules, {"extraction.build_evidence": builder}))
        table = self.normalize()
        table["rows"] *= 25
        folder = Path("data/processed/apple")
        folder.mkdir(parents=True)
        (folder/"pages.json").write_text(json.dumps({"pages": []}))
        (folder/"tables.json").write_text(json.dumps({"tables": [table]}))
        evidence = builder.build_evidence("apple")["evidence"][0]
        chunks = chunker.chunk_document("apple")["chunks"]
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c["rows"] == table["rows"] and c["source_evidence_id"] == evidence["id"] for c in chunks))
        self.assertTrue(all("June 27, 2026" in c["text"] and "In millions" in c["text"] for c in chunks))
        self.assertIn("Inventories", " ".join(c["text"] for c in chunks))

    def test_graph_chunks_use_only_explicit_labels(self):
        chunker = financial.load_module(self.stack, "_visual_graph_chunk_tests", APP/"extraction/chunker.py")
        chart = {"type": "chart", "title": "Revenue", "unit": "$ millions", "facts": [
            {"metric": "Revenue", "period": "2026", "value": 20, "value_source": "explicit_label"},
            {"metric": "Revenue", "period": "2025", "value": 17.36, "value_source": "estimated"}]}
        text = " ".join(piece for _, piece in chunker._visual_pieces(chart))
        self.assertIn("2026", text)
        self.assertNotIn("17.36", text)

    def test_deleting_a_document_removes_its_table_crops_and_cache_only(self):
        embeddings = types.ModuleType("retrieval.embeddings")
        collection = MagicMock()
        collection.get.return_value = {"ids": []}
        embeddings.collection = lambda: collection
        vectors = types.ModuleType("retrieval.vector_store")
        vectors._bm25_cache = {}
        self.stack.enter_context(patch.dict(sys.modules, {"retrieval.embeddings": embeddings, "retrieval.vector_store": vectors}))
        documents = financial.load_module(self.stack, "_visual_delete_tests", APP/"services/documents.py")
        for directory in ("table_images", "model_cache"):
            for doc in ("selected", "other"):
                folder = Path("data")/directory/doc
                folder.mkdir(parents=True)
                (folder/"private-fixture").write_bytes(b"fixture")
        documents.delete_document("selected")
        self.assertFalse(Path("data/table_images/selected").exists())
        self.assertFalse(Path("data/model_cache/selected").exists())
        self.assertTrue(Path("data/table_images/other/private-fixture").exists())
        self.assertTrue(Path("data/model_cache/other/private-fixture").exists())


class VisualNumericalTests(unittest.TestCase):
    def setUp(self):
        financial.NumericalTypeTests.setUp(self)
        self.visual = load_visual(self.stack)

    def evaluate(self, literal, *, metric="Total current assets", operation="level", columns=(1,), row=1,
                 year=2026, period="point_in_time", table=None):
        item = self.visual.normalize_table(table or balance_table(), document_id="apple", page=6,
                                           bbox=[0, 0, 600, 700], image_path="table.png")
        item.update(id="source", type="table")
        names = ["old", "new"] if operation == "growth" else ["value"]
        operands = [financial.SourceOperand(name=name, column_index=col) for name, col in zip(names, columns)]
        for operand in operands:
            operand.row_index = row
        claim = {"operation": operation, "metric": metric, "claimed_literal": literal,
                 "unit": "percent" if operation == "growth" or "%" in literal else "amount",
                 "end_period": year, "period_type": period}
        if operation == "growth":
            claim["start_period"] = 2025
        selection = types.SimpleNamespace(applicable=True, reason="Controlled cell addresses", operands=operands)
        with patch.object(self.numeric, "_select", return_value=selection):
            return self.numeric.verify_numeric(claim, [item])

    def test_t1_correct_current_assets_and_t2_wrong_amount(self):
        for literal, verdict in (("$149,818 million", "SUPPORTED"), ("$147,957 million", "CONTRADICTED")):
            calculation, comparison, reason, _ = self.evaluate(literal)
            self.assertIsNotNone(calculation, reason)
            self.assertEqual(comparison["verdict"], verdict)
            self.assertEqual(calculation["inputs"]["value"], 149818e6)
            self.assertIsNone(calculation["currency"])
            self.assertEqual(calculation["source_inputs"]["value"]["page"], 6)

    def test_t3_inventories_growth_uses_both_correct_date_columns(self):
        calculation, comparison, reason, _ = self.evaluate("94.0%", metric="Inventories", operation="growth",
                                                         columns=(2, 1), row=2, period="unspecified")
        self.assertIsNotNone(calculation, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertAlmostEqual(calculation["value"], (11092-5718)/5718*100)
        self.assertEqual(calculation["raw_inputs"], {"old": "5,718", "new": "11,092"})

    def test_t4_cashflow_ytd_column_is_not_an_annual_value(self):
        table = {"title": "Condensed Consolidated Statements of Cash Flows", "unit": "(In millions)",
                 "currency_quote": "$", "columns": ["Metric", "Nine months ended June 27, 2026", "Nine months ended June 28, 2025"],
                 "rows": [["Net income", "$80,000", "$70,000"], ["Depreciation and amortization", "9,973", "8,000"]]}
        calc, comp, reason, _ = self.evaluate("$9,973 million", metric="Depreciation and amortization", table=table, period="nine_months")
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comp["verdict"], "SUPPORTED")
        self.assertIsNone(self.evaluate("$9,973 million", metric="Depreciation and amortization", table=table, period="annual")[0])

    def test_percent_cannot_match_a_plain_monetary_table_value(self):
        self.assertIsNone(self.evaluate("149818%") [0])


class VisualRefreshTests(unittest.TestCase):
    def setUp(self):
        access_tests.CustomerAccessTests.setUp(self)
        self.addCleanup(access_tests.CustomerAccessTests.tearDown, self)
        self.snapshot = patch("pilot.model_connections.snapshot", return_value={"connection_id": "fixture-owned-connection"}).start()
        self.addCleanup(patch.stopall)

    def test_refresh_queues_only_selected_document_with_owned_connection_and_preserves_reviews(self):
        store = access_tests.store
        before = store.get_job(self.a, "d"*32)
        with store.db() as c:
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      ("e"*32, self.a.workspace, "other.pdf", "other-hash", "ready", 1))
        job = store.refresh_visuals(self.a, "c"*32)
        queued = store.get_job(self.a, job)
        payload = json.loads(queued["payload"])
        self.assertEqual(queued["kind"], "process")
        self.assertTrue(payload["options"]["refresh_visuals"])
        self.assertEqual(payload["openrouter"]["connection_id"], "fixture-owned-connection")
        self.assertEqual(store.get_job(self.a, "d"*32)["result"], before["result"])
        with store.db() as c:
            self.assertEqual(store.document(c, self.a, "e"*32)["status"], "ready")
            self.assertEqual(store.document(c, self.a, "c"*32)["sha"], "hash")
        self.snapshot.assert_called_once()

    def test_cross_workspace_refresh_is_denied(self):
        with self.assertRaises(PermissionError):
            access_tests.store.refresh_visuals(self.b, "c"*32)
        self.snapshot.assert_not_called()

    def test_no_connection_rolls_back_status_and_job(self):
        self.snapshot.side_effect = ValueError("Save a connection first")
        with self.assertRaises(ValueError):
            access_tests.store.refresh_visuals(self.a, "c"*32)
        self.assertEqual(access_tests.store.documents(self.a)[0]["status"], "ready")
        self.assertEqual(len(access_tests.store.jobs(self.a)), 1)

    def test_active_review_blocks_refresh(self):
        store = access_tests.store
        with store.db() as c:
            c.execute("UPDATE jobs SET status='running',payload=? WHERE id=?", (json.dumps({"documents": ["c"*32]}), "d"*32))
        with self.assertRaisesRegex(ValueError, "active processing/review"):
            store.refresh_visuals(self.a, "c"*32)
        self.snapshot.assert_not_called()


class VisualRefreshUITests(unittest.TestCase):
    def setUp(self):
        import test_customer_beta as beta
        self.beta = beta
        beta.CustomerExportTests.setUp(self)
        self.st = beta.StreamlitFixture()
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw = MagicMock(), MagicMock()
        self.stack.enter_context(patch.dict(sys.modules, {"streamlit": self.st, "PIL": pil,
                                                          "pymupdf": types.ModuleType("pymupdf")}))
        self.ui = beta.load_module(self.stack, "_visual_refresh_ui", APP/"pilot/ui.py")
        with access_tests.store.db() as c:
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      ("b"*32, self.access.workspace, "Apple.pdf", "fixture-hash", "ready", 1))

    def test_selected_document_button_queues_refresh_and_reruns(self):
        self.st.pressed.add("Refresh tables and graphs")
        with patch.object(access_tests.store, "refresh_visuals") as refresh:
            with self.assertRaises(self.beta.RerunPage):
                self.ui.documents_page(self.access, "customer_", connected=True)
        refresh.assert_called_once_with(self.access, "b"*32)

    def test_refresh_is_disabled_without_a_saved_connection(self):
        self.st.pressed.add("Refresh tables and graphs")
        with patch.object(access_tests.store, "refresh_visuals") as refresh:
            self.ui.documents_page(self.access, "customer_", connected=False)
        refresh.assert_not_called()
        button = next(options for kind, args, options in self.st.events
                      if kind == "button" and args[0] == "Refresh tables and graphs")
        self.assertTrue(button["disabled"])


if __name__ == "__main__":
    unittest.main()
