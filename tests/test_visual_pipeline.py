"""Production processing flow over temporary sources, with PDF/model boundaries controlled."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import test_visual_extraction as fixtures
from services.verification_errors import VerificationFailure


class VisualPipelineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        previous = Path.cwd()
        os.chdir(self.temp)
        self.addCleanup(os.chdir, previous)
        visual = fixtures.load_visual(self.stack)
        self.table = visual.normalize_table(fixtures.balance_table(), document_id="selected", page=1,
                                             bbox=[10, 20, 590, 700], image_path="table.png")
        visual.extract_visual_tables = MagicMock(return_value={"tables": [self.table], "visual_pages_scanned": 0})
        self.visual = visual
        pdf = MagicMock()
        pdf.__enter__.return_value = [types.SimpleNamespace(rect=types.SimpleNamespace(width=600, height=800), rotation=0)]
        pymupdf = types.ModuleType("pymupdf")
        pymupdf.open = MagicMock(return_value=pdf)
        pil = types.ModuleType("PIL")
        pil.Image = MagicMock()
        observe = types.ModuleType("services.observability")
        observe.event = MagicMock()
        observe.operation = lambda *a, **kw: lambda fn: fn
        parser = types.ModuleType("extraction.pdf_parser")
        parser.parsepdf = MagicMock(return_value={"pages": [{"page_number": 1, "text": "Source text. "*30}],
                                                   "page_count": 1, "metadata": {}})
        layout = types.ModuleType("extraction.layout_analyzer")
        layout.analyze_layout = MagicMock(return_value={"pages": [{"page_number": 1, "regions": []}]})
        self.layout = layout
        embeddings = types.ModuleType("retrieval.embeddings")
        embeddings.COLLECTION_NAME = "fixture-001-768"
        def embed(doc, **kw):
            folder = Path("data/processed")/doc
            (folder/"index.json").write_text(json.dumps({"collection": embeddings.COLLECTION_NAME}))
            return len(json.loads((folder/"chunks.json").read_text())["chunks"])
        embeddings.embed_document = MagicMock(side_effect=embed)
        self.embeddings = embeddings
        modules = {"pymupdf": pymupdf, "PIL": pil, "services.observability": observe,
                   "extraction.pdf_parser": parser, "extraction.layout_analyzer": layout,
                   "extraction.visual_tables": visual, "retrieval.embeddings": embeddings}
        self.stack.enter_context(patch.dict(fixtures.sys.modules, modules))
        guards = fixtures.financial.load_module(self.stack, "services.document_guards", fixtures.APP/"services/document_guards.py")
        guards.validate_pdf = MagicMock()
        fixtures.financial.load_module(self.stack, "extraction.build_evidence", fixtures.APP/"extraction/build_evidence.py")
        fixtures.financial.load_module(self.stack, "extraction.chunker", fixtures.APP/"extraction/chunker.py")
        self.pipeline = fixtures.financial.load_module(self.stack, "_visual_pipeline_tests", fixtures.APP/"pipelines/document_pipeline.py")

    def run_document(self, **options):
        return self.pipeline.process_document(b"fixture-source", "selected.pdf", "selected", enable_ocr=False, **options)

    def graph(self, *, success=True, facts=None):
        self.layout.analyze_layout.return_value["pages"][0]["regions"] = [{"type": "figure", "bbox": [1, 1, 500, 500]}]
        chart = {"chart_id": "graph-1", "evidence": {"id": "graph-source", "document_id": "selected", "page": 1,
                                                        "type": "chart", "content": "Revenue chart", "bbox": [1, 1, 500, 500]}}
        detector = types.ModuleType("extraction.chart_detector")
        detector.detect_charts = MagicMock(return_value={"charts": [chart], "results": [{"success": True}]})
        analysis = types.ModuleType("reasoning.vision_client")
        analysis.analyze_charts = MagicMock(return_value={"results": [{"chart_id": "graph-1", "success": success,
            "facts": facts or [], "analysis": {"title": "Revenue", "display_unit": "$ millions"}}]})
        self.stack.enter_context(patch.dict(fixtures.sys.modules, {"extraction.chart_detector": detector, "reasoning.vision_client": analysis}))
        return detector, analysis

    def test_new_upload_uses_vision_tables_and_indexes_complete_source_records(self):
        result = self.run_document()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["table_extraction"], "table-vision-v1")
        evidence = json.loads(Path("data/processed/selected/evidence.json").read_text())["evidence"][0]
        self.assertEqual(evidence["row_units"], ["$ millions"]*3)
        self.assertEqual(evidence["bbox_space"], "pdf_points")
        self.assertEqual(evidence["column_map"][0]["raw"], "June 27, 2026")
        self.visual.extract_visual_tables.assert_called_once()
        self.embeddings.embed_document.assert_called_once()

    def test_ready_index_reuses_old_processing_until_explicit_refresh(self):
        self.run_document()
        self.visual.extract_visual_tables.reset_mock()
        original = Path("data/processed/selected/tables.json").read_bytes()
        self.run_document()
        self.visual.extract_visual_tables.assert_not_called()
        self.assertEqual(Path("data/processed/selected/tables.json").read_bytes(), original)
        self.run_document(refresh_visuals=True)
        self.visual.extract_visual_tables.assert_called_once()

    def test_refresh_leaves_other_document_artifacts_untouched(self):
        other = Path("data/processed/internal-3m")
        other.mkdir(parents=True)
        marker = b'{"chunk_count":4854,"embedding_model":"gemini-embedding-001","dimensions":768}'
        (other/"index.json").write_bytes(marker)
        self.run_document(refresh_visuals=True)
        self.assertEqual((other/"index.json").read_bytes(), marker)

    def test_table_provider_failure_marks_processing_error_and_never_indexes(self):
        self.visual.extract_visual_tables.side_effect = VerificationFailure("Provider quota", stage="vision")
        with self.assertRaises(VerificationFailure):
            self.run_document()
        saved = json.loads(Path("data/processed/selected/document.json").read_text())
        self.assertEqual(saved["status"], "error")
        self.assertEqual(saved["error_stage"], "vision")
        self.embeddings.embed_document.assert_not_called()

    def test_graph_facts_are_indexed_with_units_and_estimates_are_excluded(self):
        self.graph(facts=[{"metric": "Revenue", "period": "2026", "display_value": "20", "value": 20,
                           "value_source": "explicit_label", "unit": "$ millions"},
                          {"metric": "Revenue", "period": "2025", "value": 18, "value_source": "estimated"}])
        result = self.run_document()
        self.assertEqual(result["chart_count"], 1)
        evidence = json.loads(Path("data/processed/selected/evidence.json").read_text())["evidence"]
        graph = next(item for item in evidence if item["type"] == "chart")
        self.assertEqual(graph["extraction_method"], "chart-vision-v1")
        self.assertEqual(len(graph["facts"]), 1)
        self.assertEqual(graph["unit"], "$ millions")

    def test_graph_with_no_readable_labels_warns_but_does_not_invent_values(self):
        self.graph()
        result = self.run_document()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(any("no explicit readable numeric labels" in warning for warning in result["warnings"]))

    def test_graph_provider_failure_remains_a_processing_failure(self):
        self.graph(success=False)
        with self.assertRaises(VerificationFailure):
            self.run_document()
        self.embeddings.embed_document.assert_not_called()
        saved = json.loads(Path("data/processed/selected/document.json").read_text())
        self.assertEqual(saved["error_stage"], "chart_reading")


if __name__ == "__main__":
    unittest.main()
