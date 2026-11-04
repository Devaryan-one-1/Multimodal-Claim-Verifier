"""Checker scoring/transport checks; temporary fixtures and no API calls."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("table_method_check", ROOT/"check_table_methods.py")
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


class TableMethodChecks(unittest.TestCase):
    def table(self):
        return {"page": 6, "unit": "In millions", "columns": ["Metric", "June 27, 2026", "September 27, 2025"],
                "rows": [["Total current assets", "149,818", "147,957"], ["Inventories", "11,092", "5,718"]]}

    def test_source_values_and_verdict_must_both_match(self):
        self.assertEqual(checker.assess(checker.CHECKS[0], [self.table()])["status"], "PASS")
        self.assertEqual(checker.assess(checker.CHECKS[1], [self.table()])["verdict"], "CONTRADICTED")
        wrong = self.table()
        wrong["rows"][0][1] = "100"
        # Still contradicted, but wrong source value cannot count as a pass.
        self.assertEqual(checker.assess(checker.CHECKS[1], [wrong])["status"], "FAIL")

    def test_growth_uses_correct_year_columns(self):
        result = checker.assess(checker.CHECKS[2], [self.table()])
        self.assertEqual((result["status"], result["verdict"]), ("PASS", "SUPPORTED"))
        self.assertAlmostEqual(float(result["actual"]), 93.9839104582)
        self.assertEqual([a["column"] for a in result["addresses"]], [2, 1])

    def test_missing_units_duplicate_values_and_swapped_years_do_not_pass(self):
        missing, swapped = self.table(), self.table()
        missing["unit"] = ""
        swapped["columns"][1:] = reversed(swapped["columns"][1:])
        for tables in ([missing], [swapped], [self.table(), self.table()]):
            with self.subTest(tables=tables):
                self.assertEqual(checker.assess(checker.CHECKS[0], tables)["status"], "FAIL")

    def test_nine_month_period_is_required(self):
        table = {"page": 8, "unit": "In millions", "columns": ["Metric", "Three months ended June 27, 2026"],
                 "rows": [["Depreciation and amortization", "9,973"]]}
        self.assertEqual(checker.assess(checker.CHECKS[3], [table])["status"], "FAIL")
        table["columns"][1] = "Nine months ended June 27, 2026"
        self.assertEqual(checker.assess(checker.CHECKS[3], [table])["status"], "PASS")
        stock = self.table()
        stock["context"] = "Nine months ended"
        self.assertEqual(checker.assess(checker.CHECKS[2], [stock])["verdict"], "UNKNOWN")

    def test_image_retrieval_is_ranked_before_reading_and_gold_is_not_embedded(self):
        images = [{"page": 6, "path": "fixture-balance.png"}, {"page": 8, "path": "fixture-cash.png"}]
        embeddings, read = [], []
        def embed(client, inputs, *, image_model=False, query=False):
            embeddings.append((inputs, image_model, query))
            return [[1., 0.], [0., 1.]] if not query else [[1., 0.]]*3+[[0., 1.]]
        def vision(image, model, cache):
            read.append(image["path"])
            return [self.table()] if image["page"] == 6 else [{"page": 8, "unit": "In millions", "columns": ["Metric", "Nine months ended June 27, 2026"], "rows": [["Depreciation and amortization", "9,973"]]}]
        with patch.object(checker, "embed", side_effect=embed), patch.object(checker, "vision", side_effect=vision):
            result = checker.compare("direct_image", object(), [], images, "fixture/vision", {})
        self.assertEqual(result["passed"], 4)
        self.assertEqual(embeddings[0], ([i["path"] for i in images], True, False))
        self.assertEqual(embeddings[1][0], [c.claim for c in checker.CHECKS])
        self.assertEqual(read, ["fixture-balance.png"]*3+["fixture-cash.png"])

    def test_multimodal_inputs_are_separate_contents_without_task_type(self):
        calls = []
        fake_types = types.SimpleNamespace(
            Content=lambda **kwargs: kwargs, Part=types.SimpleNamespace(from_bytes=lambda **kwargs: kwargs,
                from_text=lambda **kwargs: kwargs), EmbedContentConfig=lambda **kwargs: kwargs)
        google, genai = types.ModuleType("google"), types.ModuleType("google.genai")
        google.genai, genai.types = genai, fake_types
        def request(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(embeddings=[types.SimpleNamespace(values=[1., 0.]) for _ in kwargs["contents"]])
        client = types.SimpleNamespace(models=types.SimpleNamespace(embed_content=request))
        with patch.dict(sys.modules, {"google": google, "google.genai": genai, "google.genai.types": fake_types}):
            checker.embed(client, ["claim A", "claim B"], image_model=True, query=True)
            with tempfile.TemporaryDirectory() as folder:
                image = Path(folder)/"fixture.png"
                image.write_bytes(b"PNG")
                checker.embed(client, [str(image)], image_model=True)
        self.assertEqual(len(calls[0]["contents"]), 2)
        self.assertNotIn("task_type", calls[0]["config"])
        self.assertEqual(calls[1]["contents"][0]["parts"][0]["data"], b"PNG")

    def test_invalid_vectors_and_non_numeric_cells_are_refused(self):
        for a, b in (([], []), ([1.], [1., 0.]), ([float('nan')], [1.]), ([0.], [1.])):
            with self.assertRaises(ValueError):
                checker.cosine(a, b)
        self.assertEqual(checker.number("(1,234)"), -1234)
        with self.assertRaises(ValueError):
            checker.number("unreadable")

    def test_pdfplumber_crops_include_header_and_fallback_is_explicit(self):
        candidate = types.SimpleNamespace(bbox=(30, 120, 580, 700), extract=lambda: [
            ["Metric", "2026", "2025"], ["Total current assets", "149818", "147957"], ["Inventories", "11092", "5718"]])
        page = MagicMock(width=612, height=792, bbox=(0, 0, 612, 792))
        page.find_tables.return_value = [candidate]
        document = MagicMock()
        document.__enter__.return_value.pages = [page]
        pdfplumber = types.SimpleNamespace(open=lambda _: document)
        helpers = types.SimpleNamespace(_clean_table=lambda rows: rows, _header_block_above=lambda *args: "In millions 2026 2025",
                    _parse_periods=lambda *args: ([2026, 2025], [], [{"col": 1, "year": 2026}, {"col": 2, "year": 2025}]))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {
                "pdfplumber": pdfplumber, "extraction.table_extractor": helpers}):
            tables, images, detection = checker.extract("fixture.pdf", [1], Path(folder), True, "lines")
            page.crop.assert_called_once_with((18, 30, 592, 706))
            self.assertEqual(len(tables), 1)
            self.assertFalse(images[0]["fallback"])
            page.find_tables.return_value = []
            tables, images, detection = checker.extract("fixture.pdf", [1], Path(folder), True, "lines")
            self.assertFalse(tables)
            self.assertTrue(images[0]["fallback"])
            self.assertTrue(detection[0]["whole_page_fallback"])
            _, images, _ = checker.extract("fixture.pdf", [1], Path(folder), False, "lines")
            self.assertFalse(images)


if __name__ == "__main__":
    unittest.main()
