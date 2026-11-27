"""Offline replay of the 26 November Aster Vale and Red Tarn failures.

Run directly: python -B tests/repro_source_resolution.py -v
This file is outside default test discovery. Retrieval and model replies are
simulated; production numeric validation and verdict reconciliation execute.
The two expected failures assert the desired rollout verdicts on the broken
inputs. Other tests isolate each cause using explicitly modified fixtures.
No database, provider, embedding or customer credentials are used.
"""
import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import test_workflow_comparison as fixtures


# Relevant fields copied from the indexed chart of review d8a5b216. IDs are
# shortened; numbers, labels, missing currency and point units are preserved.
ASTER_CHART = {
    "id": "chart", "document_id": fixtures.DOC, "type": "chart", "page": 2,
    "title": "nsolidated revenue", "unit": "millions",
    "x_axis": {"label": None, "unit": None, "values": ["2025", "2026"]},
    "y_axis": {"label": "millions", "unit": None, "values": []},
    "extraction_method": "chart-vision-v1",
    "facts": [
        {"series_name": "nsolidated revenue", "metric": None, "segment": None,
         "period": year, "category": None, "value": amount,
         "display_value": str(amount), "unit": "millions",
         "value_source": "explicit_label", "page": 2}
        for year, amount in (("2025", 92), ("2026", 141))
    ],
}


class SourceResolutionReproduction(unittest.TestCase):
    def setUp(self):
        self.harness = fixtures.WorkflowComparisonTests()
        # Keep the user's temporary parser prints out of this focused report.
        with redirect_stdout(StringIO()):
            self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.numeric = self.harness.env.numeric
        self.scope_mode = "comparable"
        self.harness.label_model.side_effect = self.label_reply

    def label_reply(self, schema, prompt, **kwargs):
        """Replay the captured Red Tarn exclusion, or comparable-source control."""
        readings = []
        for packet in json.loads(prompt)["findings"]:
            labels = []
            for subject in packet["subjects"]:
                wording = subject["wording"]
                labels.append(re.split(r"\s+was\s+", wording, flags=re.I)[0])
            excluded = self.scope_mode == "captured_red_tarn" and packet["kind"] == "text"
            readings.append(SimpleNamespace(
                index=packet["index"], labels=labels,
                scope="different" if excluded else "matches",
                scope_quote=packet["subjects"][0]["wording"] if excluded else "",
            ))
        return SimpleNamespace(readings=readings, same_meaning=True)

    def verify(self, chart, *, claimed=118, text_label="Consolidated revenue"):
        chart = copy.deepcopy(chart)
        text = dict(id="text", document_id=fixtures.DOC, type="text", page=1,
                    content=f"{text_label} was USD 118 million in 2026.")
        return self.harness.verify_sources(
            [text, chart], metric="consolidated revenue", unit="amount",
            period_type="unspecified", claimed_literal=f"USD {claimed} million",
            claim_text=f"Consolidated revenue was USD {claimed} million in 2026.",
        )

    def corrected_aster(self, *, fix_label=False):
        chart = copy.deepcopy(ASTER_CHART)
        chart["unit"] = "USD millions"
        for fact in chart["facts"]:
            fact["unit"] = "USD millions"
        if fix_label:
            chart["title"] = "Consolidated revenue"
            for fact in chart["facts"]:
                fact["series_name"] = "Consolidated revenue"
        return chart

    def red_tarn(self):
        chart = self.corrected_aster(fix_label=True)
        chart["facts"][1].update(value=133, display_value="133")
        return chart

    def test_aster_original_units_reproduce_supported_with_chart_unknown(self):
        result = self.verify(ASTER_CHART)
        chart = result["evidence_checks"]["chart"]
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["verdict"], "SUPPORTED")
        self.assertEqual(chart["verdict"], "UNKNOWN")
        self.assertIn("incompatible quantity types", chart["reason"])
        self.assertEqual(chart["evidence_ids"], [])
        self.harness.label_model.assert_not_called()
        print("Aster original: SUPPORTED; chart UNKNOWN; reconciliation never called.")

    def test_aster_missing_currency_is_plain_number_despite_million_scale(self):
        value, reason = self.numeric.parse_number("141", context="millions")
        self.assertIsNone(reason)
        self.assertEqual(value["value"], 141_000_000)
        self.assertIsNone(value["currency"])
        self.assertEqual(self.numeric._quantity_kind(value), "number")
        monetary, _ = self.numeric.parse_number("141", context="USD millions")
        self.assertEqual(self.numeric._quantity_kind(monetary), "amount")
        self.assertEqual(monetary["currency"], "USD")
        print("Aster: millions supplies scale only; USD millions supplies monetary type.")

    def test_aster_point_unit_shadows_currency_in_figure_unit(self):
        chart = copy.deepcopy(ASTER_CHART)
        chart["unit"] = "USD millions"  # Counterfactual header-only intervention.
        operand = self.numeric.Operand(name="value", evidence_id="chart", fact_index=1)
        value = self.numeric._value(dict(operation="level", metric="consolidated revenue",
                                        end_period=2026, period_type="unspecified"), operand, chart)
        self.assertEqual(operand.unit_quote, "millions")
        self.assertEqual(value["unit_context"], "millions")
        self.assertIsNone(value["currency"])
        result = self.verify(chart)
        self.assertEqual(result["evidence_checks"]["chart"]["verdict"], "UNKNOWN")
        print("Aster: fact.unit='millions' shadows figure.unit='USD millions'.")

    def test_aster_fixing_only_units_exposes_truncated_label(self):
        result = self.verify(self.corrected_aster())
        self.assertEqual(result["evidence_checks"]["chart"]["verdict"], "CONTRADICTED")
        self.assertEqual(result["evidence_checks"]["chart"]["calculation"]["value"], 141_000_000)
        self.assertEqual(result["verdict"], "SUPPORTED")
        self.assertEqual(result["source_resolution"]["status"], "possible_source_conflict")
        print("Aster units-only control: chart now CONTRADICTED; truncated label triggers priority.")

    def test_aster_verified_units_and_full_label_produce_source_conflict(self):
        result = self.verify(self.corrected_aster(fix_label=True))
        self.assertEqual(result["verdict"], "SOURCE_CONFLICT", result["reason"])
        self.assertEqual({item["page"] for item in result["evidence"]}, {1, 2})
        print("Aster units-and-label control: SOURCE_CONFLICT with pages 1 and 2.")

    def test_red_tarn_saved_scope_reply_reproduces_wrong_supported_verdict(self):
        self.scope_mode = "captured_red_tarn"
        result = self.verify(self.red_tarn(), claimed=133, text_label="Consolidated total revenue")
        self.assertEqual(result["evidence_checks"]["text"]["verdict"], "CONTRADICTED")
        self.assertEqual(result["evidence_checks"]["chart"]["verdict"], "SUPPORTED")
        self.assertEqual(result["source_resolution"]["findings"][0]["scope"], "different")
        self.assertEqual(result["source_resolution"]["status"], "scope_difference")
        self.assertEqual(result["verdict"], "SUPPORTED")
        print("Red Tarn captured reply: quoted sentence accepted as scope exclusion; SUPPORTED.")

    def test_red_tarn_comparable_scope_control_applies_text_priority(self):
        result = self.verify(self.red_tarn(), claimed=133, text_label="Consolidated total revenue")
        self.assertEqual(result["verdict"], "CONTRADICTED", result["reason"])
        self.assertEqual(result["source_resolution"]["status"], "possible_source_conflict")
        self.assertEqual(result["calculation"]["value"], 118_000_000)
        self.assertEqual({item["page"] for item in result["evidence"]}, {1, 2})
        print("Red Tarn comparable-scope control: CONTRADICTED; possible conflict; both pages.")

    @unittest.expectedFailure
    def test_rollout_aster_original_evidence_should_establish_source_conflict(self):
        result = self.verify(ASTER_CHART)
        self.assertEqual(result["verdict"], "SOURCE_CONFLICT")

    @unittest.expectedFailure
    def test_rollout_red_tarn_wording_alone_should_not_exclude_text(self):
        self.scope_mode = "captured_red_tarn"
        result = self.verify(self.red_tarn(), claimed=133, text_label="Consolidated total revenue")
        self.assertEqual(result["verdict"], "CONTRADICTED")
        self.assertEqual(result["source_resolution"]["status"], "possible_source_conflict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
