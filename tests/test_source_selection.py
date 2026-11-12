"""Source address/period validation with real arithmetic and controlled model output."""
from contextlib import contextmanager
import copy
import json
import types
import unittest
from unittest.mock import MagicMock, patch

import test_financial_integrity as financial
import test_visual_extraction as visual_fixture


@contextmanager
def captured():
    yield [], 0


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        financial.NumericalTypeTests.setUp(self)
        self.visual = visual_fixture.load_visual(self.stack)
        if not hasattr(self.numeric.Operand, "model_validate"):
            class Operand(financial.SourceOperand):
                def __init__(self, **values):
                    super().__init__(name=values.get("name", "value"), evidence_id=values.get("evidence_id", ""),
                                     column_index=values.get("column_index"))
                    self.__dict__.update(values)
            self.numeric.Operand = Operand
            self.numeric.Selection = lambda **values: types.SimpleNamespace(**values)
        self.numeric.capture_calls = captured
        self.numeric.structured = MagicMock(side_effect=AssertionError("Unexpected provider call"))
        self.evidence = self.table()
        self.pc = {"claim_text": "At June 27, 2026, Apple's total current assets were $149,818 million.",
                   "operation": "level", "metric": "total current assets", "claimed_literal": "$149,818 million",
                   "unit": "amount", "end_period": 2026, "period_type": "point_in_time", "period_quote": "June 27, 2026"}

    def table(self, doc="doc-a"):
        item = self.visual.normalize_table(visual_fixture.balance_table(), document_id=doc, page=6,
                                           bbox=[0, 0, 600, 700], image_path="fixture.png")
        item.update(id=f"{doc}::evidence_843", evidence_id=f"{doc}::evidence_843", source_evidence_id="evidence_843", type="table")
        return item

    def operand(self, **updates):
        values = {"name": "value", "evidence_id": self.evidence["id"], "row_index": 1, "column_index": 1}
        values.update(updates)
        return self.numeric.Operand(**values)

    def selection(self, *operands, applicable=True, reason="Source selected"):
        return self.numeric.Selection(applicable=applicable, reason=reason, operands=list(operands))

    def model_result(self, *responses, evidence=None):
        self.numeric.structured.side_effect = list(responses)
        with patch.object(self.numeric, "_local_selection", return_value=None):
            return self.numeric.verify_numeric(self.pc, evidence or [self.evidence])

    def test_unique_total_row_uses_local_tools_and_never_calls_model(self):
        self.evidence["rows"].append(["Other current assets", "17,420", "14,585"])
        self.evidence["row_units"].append("$ millions")
        self.evidence["rows"].append(["Total non-current assets", "233,448", "211,284"])
        self.evidence["row_units"].append("$ millions")
        for literal, verdict in (("$149,818 million", "SUPPORTED"), ("$147,957 million", "CONTRADICTED")):
            self.pc["claimed_literal"] = literal
            calc, comparison, reason, _ = self.numeric.verify_numeric(self.pc, [self.evidence])
            self.assertIsNotNone(calc, reason)
            self.assertEqual(comparison["verdict"], verdict)
            self.assertEqual(calc["source_inputs"]["value"]["row_index"], 1)
        self.numeric.structured.assert_not_called()

    def test_inventory_heading_does_not_make_components_match_total_inventory(self):
        self.evidence["title"] = self.evidence["context"] = "Inventories"
        self.evidence["rows"] = [["Inventories", "", ""], ["Components", "7,645", "2,124"],
                                 ["Finished goods", "3,447", "3,594"], ["Total inventories", "11,092", "5,718"]]
        self.evidence["row_units"] = ["$ millions"]*4
        self.pc.update(metric="inventories", operation="growth", claimed_literal="94.0%", unit="percent", start_period=2025,
                       period_quote=None, claim_text="Inventories grew 94.0% between September 27, 2025 and June 27, 2026.")
        calc, comparison, reason, _ = self.numeric.verify_numeric(self.pc, [self.evidence])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(calc["source_inputs"]["old"]["row_index"], 3)
        self.assertAlmostEqual(calc["value"], 93.9839104582)
        self.numeric.structured.assert_not_called()

    def reported_period_claim(self, **updates):
        return {"claim_text": "Apple's inventories grew 94.0% over the reported periods.",
                "operation": "growth", "metric": "inventories", "claimed_literal": "94.0%",
                "unit": "percent", "start_period": None, "end_period": None,
                "period_type": "unspecified", **updates}

    def test_reported_periods_use_the_only_pair_and_preserve_verdict_comparison(self):
        for literal, expected in (("94.0%", "SUPPORTED"), ("10%", "CONTRADICTED")):
            with self.subTest(literal=literal):
                claim = self.reported_period_claim(claimed_literal=literal)
                original = copy.deepcopy(claim)
                calc, comparison, reason, _ = self.numeric.verify_numeric(claim, [self.evidence])
                self.assertIsNotNone(calc, reason)
                self.assertEqual(comparison["verdict"], expected)
                self.assertEqual(calc["inputs"], {"old": 5718e6, "new": 11092e6})
                self.assertEqual(calc["inferred_periods"]["old"]["date"], "2025-09-27")
                self.assertEqual(calc["inferred_periods"]["new"]["date"], "2026-06-27")
                self.assertIn("September 27, 2025", calc["note"])
                self.assertEqual(claim, original)
        self.numeric.structured.assert_not_called()

    def test_reported_periods_reject_a_third_period_outside_retrieved_evidence(self):
        other = self.table()
        other["id"] = "doc-a::older-table"
        other["headers"] = ["Metric", "September 27, 2025", "September 28, 2024"]
        other["column_map"] = self.visual.column_map(other["headers"])
        calc, comparison, reason, used = self.numeric.verify_numeric(
            self.reported_period_claim(), [self.evidence], period_evidence=[self.evidence, other])
        self.assertIsNone(calc)
        self.assertIsNone(comparison)
        self.assertEqual(used, [])
        self.assertIn("Specify the two reporting periods", reason)
        self.numeric.structured.assert_not_called()

    def test_unrelated_metric_periods_do_not_make_the_requested_pair_ambiguous(self):
        other = self.table()
        other["id"] = "doc-a::unrelated"
        other["headers"] = ["Metric", "September 27, 2025", "September 28, 2024"]
        other["column_map"] = self.visual.column_map(other["headers"])
        other["rows"] = [["Revenue", "100", "90"]]
        other["row_units"] = ["$ millions"]
        calc, comparison, reason, _ = self.numeric.verify_numeric(
            self.reported_period_claim(), [self.evidence], period_evidence=[self.evidence, other])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.numeric.structured.assert_not_called()

    def test_reported_periods_reject_mixed_durations_and_honor_explicit_duration(self):
        table = self.visual.normalize_table(visual_fixture.compensation_table(), document_id="doc-a", page=14,
                                            bbox=[0, 0, 600, 700], image_path="fixture.png")
        table.update(id="doc-a::compensation", type="table")
        claim = self.reported_period_claim(metric="share-based compensation expense", claimed_literal="8.7%")
        calc, comparison, reason, _ = self.numeric.verify_numeric(claim, [table])
        self.assertIsNone(calc)
        self.assertIn("Specify the two reporting periods", reason)
        nine_months = dict(claim, period_type="nine_months")
        calc, comparison, reason, _ = self.numeric.verify_numeric(nine_months, [table])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(calc["inputs"], {"old": 9680e6, "new": 10523e6})
        self.numeric.structured.assert_not_called()

    def test_reported_periods_keep_full_dates_when_both_columns_are_in_one_year(self):
        self.evidence["headers"] = ["Metric", "June 27, 2026", "March 28, 2026"]
        self.evidence["column_map"] = self.visual.column_map(self.evidence["headers"])
        calc, comparison, reason, _ = self.numeric.verify_numeric(self.reported_period_claim(), [self.evidence])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(calc["inferred_periods"]["old"]["date"], "2026-03-28")
        self.numeric.structured.assert_not_called()

    def test_explicit_periods_bypass_inference_despite_other_report_periods(self):
        claim = self.reported_period_claim(start_period=2025, end_period=2026)
        other = self.table("doc-b")
        calc, comparison, reason, _ = self.numeric.verify_numeric(
            claim, [self.evidence], period_evidence=[self.evidence, other])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertNotIn("inferred_periods", calc)
        self.numeric.structured.assert_not_called()

    def test_reported_periods_do_not_choose_between_multiple_documents(self):
        calc, comparison, reason, _ = self.numeric.verify_numeric(
            self.reported_period_claim(), [self.evidence], period_evidence=[self.evidence, self.table("doc-b")])
        self.assertIsNone(calc)
        self.assertIn("Specify the two reporting periods", reason)
        self.numeric.structured.assert_not_called()

    def test_blank_third_period_is_not_discarded_to_create_an_apparent_pair(self):
        self.evidence["headers"].append("September 28, 2024")
        self.evidence["column_map"] = self.visual.column_map(self.evidence["headers"])
        for row in self.evidence["rows"]:
            row.append("")
        calc, comparison, reason, _ = self.numeric.verify_numeric(self.reported_period_claim(), [self.evidence])
        self.assertIsNone(calc)
        self.assertIn("Specify the two reporting periods", reason)
        self.numeric.structured.assert_not_called()

    def test_repeated_headers_in_other_source_tables_keep_the_same_period_pair(self):
        repeated = self.table()
        repeated["id"] = "doc-a::repeated"
        calc, comparison, reason, _ = self.numeric.verify_numeric(
            self.reported_period_claim(), [self.evidence], period_evidence=[self.evidence, repeated])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.numeric.structured.assert_not_called()

    def test_year_only_headers_resolve_comparable_annual_revenue_periods(self):
        table = {"id": "annual", "type": "table", "document_id": "doc-a", "page": 3,
                 "headers": ["Metric", "2026", "2025"], "rows": [["Net sales", "110", "100"]],
                 "column_headers": {"raw": "Years ended 2026 2025"}, "unit": "$ millions"}
        claim = self.reported_period_claim(metric="revenue", claimed_literal="10%")
        calc, comparison, reason, _ = self.numeric.verify_numeric(claim, [table])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(calc["inferred_periods"]["old"]["period_type"], "annual")
        self.assertEqual(calc["inputs"], {"old": 100e6, "new": 110e6})
        self.numeric.structured.assert_not_called()

    def test_conflicting_period_metadata_and_printed_duration_require_clarification(self):
        self.evidence["column_map"][0].update(period="year", raw="Three months ended June 27, 2026")
        calc, comparison, reason, _ = self.numeric.verify_numeric(self.reported_period_claim(), [self.evidence])
        self.assertIsNone(calc)
        self.assertIn("Specify the two reporting periods", reason)
        self.numeric.structured.assert_not_called()

    def test_inferred_dates_remain_bound_to_their_own_respective_prose_values(self):
        self.evidence["headers"] = ["Metric", "June 27, 2026", "March 28, 2026"]
        self.evidence["column_map"] = self.visual.column_map(self.evidence["headers"])
        claim = self.numeric._resolve_reported_periods(self.reported_period_claim(), [self.evidence])
        item = {"type": "text", "document_id": "doc-a", "content":
                "On March 28, 2026 and June 27, 2026, inventories were $5,718 million and $11,092 million, respectively."}
        for name, raw in (("old", "$5,718 million"), ("new", "$11,092 million")):
            operand = self.operand(name=name)
            chosen = next(number for number in self.numeric._bank(item) if number["raw"] == raw)
            self.numeric._text_year_check(claim, operand, item, chosen)
        item["content"] = item["content"].replace("March 28", "January 28")
        chosen = next(number for number in self.numeric._bank(item) if number["raw"] == "$5,718 million")
        with self.assertRaisesRegex(ValueError, "different reporting date"):
            self.numeric._text_year_check(claim, self.operand(name="old"), item, chosen)

    def test_inferred_source_scope_and_full_chart_dates_cannot_be_dropped(self):
        claim = self.numeric._resolve_reported_periods(self.reported_period_claim(), [self.evidence])
        with self.assertRaisesRegex(ValueError, "different document"):
            self.numeric._value(claim, self.operand(name="new"), self.table("doc-b"))
        chart = {"type": "chart", "document_id": "doc-a", "title": "Inventories",
                 "facts": [{"value_source": "explicit_label", "period": "2026"}]}
        with self.assertRaisesRegex(ValueError, "inferred reporting date"):
            self.numeric._value(claim, self.operand(name="new", fact_index=0), chart)

    def test_short_source_id_is_resolved_only_when_unique(self):
        calc, comparison, reason, used = self.model_result(self.selection(self.operand(evidence_id="evidence_843")))
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(used, [self.evidence["id"]])
        self.assertEqual(calc["source_inputs"]["value"]["evidence_id"], self.evidence["id"])

    def test_ambiguous_short_id_is_not_guessed_across_documents(self):
        bad = self.selection(self.operand(evidence_id="evidence_843"))
        with self.assertRaises(self.numeric.VerificationFailure):
            self.model_result(bad, bad, evidence=[self.evidence, self.table("doc-b")])
        self.assertEqual(self.numeric.structured.call_count, 2)

    def test_bad_id_gets_one_correction_then_real_source_values_are_calculated(self):
        bad = self.selection(self.operand(evidence_id="invented-source"))
        calc, comparison, reason, _ = self.model_result(bad, self.selection(self.operand()))
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        request = json.loads(self.numeric.structured.call_args.args[1])
        self.assertIn("local_validation_error", request)
        self.assertEqual(self.numeric.structured.call_count, 2)

    def test_fabricated_unit_quote_is_corrected_without_inventing_usd(self):
        bad = self.selection(self.operand(unit_quote="USD millions"))
        calc, comparison, reason, _ = self.model_result(bad, self.selection(self.operand(unit_quote="")))
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertIsNone(calc["currency"])
        self.assertEqual(calc["input_metadata"]["value"]["unit_context"], "$ millions")

    def test_duplicate_operand_names_require_one_validated_source_address(self):
        bad = self.selection(self.operand(), self.operand(evidence_id="doc-b::evidence_843"))
        calc, comparison, reason, _ = self.model_result(bad, self.selection(self.operand()), evidence=[self.evidence, self.table("doc-b")])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(self.numeric.structured.call_count, 2)

    def test_exact_duplicate_address_is_collapsed_without_another_request(self):
        calc, comparison, reason, _ = self.model_result(self.selection(self.operand(), self.operand()))
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(self.numeric.structured.call_count, 1)

    def test_persistent_bad_selection_is_operational_failure_not_unknown_verdict(self):
        bad = self.selection(self.operand(column_index=99))
        with self.assertRaises(self.numeric.VerificationFailure) as failure:
            self.model_result(bad, bad)
        self.assertEqual(failure.exception.stage, "numeric_selection")
        self.assertEqual(self.numeric.structured.call_count, 2)

    def test_provider_failure_is_not_retried_as_address_validation(self):
        self.numeric.structured.side_effect = RuntimeError("quota unavailable")
        with patch.object(self.numeric, "_local_selection", return_value=None):
            with self.assertRaises(self.numeric.VerificationFailure):
                self.numeric.verify_numeric(self.pc, [self.evidence])
        self.assertEqual(self.numeric.structured.call_count, 1)

    def test_explicit_counterevidence_recheck_keeps_contradiction_calculation(self):
        self.pc["claimed_literal"] = "$147,957 million"
        rejected = self.selection(applicable=False, reason="Actually the source amount differs from the claim")
        calc, comparison, reason, _ = self.model_result(rejected, self.selection(self.operand()))
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "CONTRADICTED")
        self.assertEqual(calc["inputs"]["value"], 149818e6)

    def test_genuinely_missing_year_returns_unavailable_without_invalid_model_error(self):
        self.pc.update(end_period=2040, period_quote="June 27, 2040")
        calc, comparison, reason, used = self.model_result(self.selection(applicable=False, reason="Requested year absent"))
        self.assertIsNone(calc)
        self.assertIsNone(comparison)
        self.assertEqual(used, [])
        self.assertEqual(reason, "Requested year absent")
        self.assertEqual(self.numeric.structured.call_count, 1)

    def test_exact_as_of_date_distinguishes_two_columns_in_same_year(self):
        self.evidence["headers"][2] = "September 27, 2026"
        self.evidence["column_map"][1].update(year=2026, raw="September 27, 2026")
        calc, comparison, reason, _ = self.numeric.verify_numeric(self.pc, [self.evidence])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(calc["source_inputs"]["value"]["column_index"], 1)
        self.numeric.structured.assert_not_called()

    def test_period_and_total_scope_mismatches_are_still_rejected(self):
        self.evidence["rows"].append(["Other current assets", "17,420", "14,585"])
        self.evidence["row_units"].append("$ millions")
        for operand, update in ((self.operand(row_index=3), {}), (self.operand(), {"period_type": "annual"})):
            with self.subTest(update=update):
                claim = {**self.pc, **update}
                with self.assertRaises(ValueError):
                    self.numeric._validated_selection(claim, [self.evidence], self.selection(operand))

    def chart(self):
        # Actual saved shape from the beta chart: printed years landed in category.
        return {"id": "doc-a::chart", "source_evidence_id": "chart", "document_id": "doc-a", "page": 2,
                "type": "chart", "title": "Figure 1. Annual customer count", "unit": "customers",
                "facts": [{"metric": None, "period": None, "category": year, "value": value,
                           "display_value": str(value), "unit": "customers", "value_source": "explicit_label"}
                          for year, value in (("2025", 80), ("2026", 120))]}

    def chart_claim(self, **updates):
        return {"claim_text": "Annual customer count was 120 in 2026.", "metric": "customer count",
                "operation": "level", "claimed_literal": "120", "unit": "number", "end_period": 2026,
                "period_type": "annual", **updates}

    def test_chart_year_categories_keep_correct_and_incorrect_amount_verdicts(self):
        chart = self.chart()
        original = copy.deepcopy(chart)
        for literal, expected in (("120", "SUPPORTED"), ("80", "CONTRADICTED")):
            with self.subTest(literal=literal):
                self.pc = self.chart_claim(claimed_literal=literal)
                operand = self.operand(evidence_id=chart["id"], row_index=None, column_index=None, fact_index=1)
                self.numeric.structured.reset_mock()
                calc, comparison, reason, _ = self.model_result(self.selection(operand), evidence=[chart])
                self.assertIsNotNone(calc, reason)
                self.assertEqual(comparison["verdict"], expected)
                self.assertEqual(calc["value"], 120)
                self.assertEqual(calc["source_inputs"]["value"]["page"], 2)
                self.assertEqual(calc["source_inputs"]["value"]["fact_index"], 1)
                self.assertEqual(calc["source_inputs"]["value"]["period_interpretation"], "explicit_chart_year_category")
                self.assertEqual(self.numeric.structured.call_count, 1)
        self.assertEqual(chart["facts"], original["facts"])

    def test_chart_year_categories_support_growth_from_both_printed_values(self):
        chart = self.chart()
        self.pc = self.chart_claim(operation="growth", claimed_literal="50%", unit="percent", start_period=2025)
        operands = [self.operand(name=name, evidence_id=chart["id"], row_index=None, column_index=None, fact_index=index)
                    for index, name in enumerate(("old", "new"))]
        calc, comparison, reason, _ = self.model_result(self.selection(*operands), evidence=[chart])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison["verdict"], "SUPPORTED")
        self.assertEqual(calc["inputs"], {"old": 80, "new": 120})
        self.assertEqual(calc["value"], 50)

    def test_chart_conflicting_period_and_year_category_are_rejected(self):
        chart = self.chart()
        chart["facts"][1]["period"] = "2025"
        claim = self.chart_claim(end_period=2025)
        operand = self.operand(evidence_id=chart["id"], row_index=None, column_index=None, fact_index=1)
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.numeric._validated_selection(claim, [chart], self.selection(operand))

    def test_chart_category_requires_unambiguous_time_context_and_printed_value(self):
        variants = [({"title": "Customer count by model"}, {}),
                    ({"title": "Annual customer count by product model"}, {}),
                    ({"x_axis": {"label": "Product model"}}, {}),
                    ({}, {"category": "Model 2026"}),
                    ({}, {"category": "2025 / 2026"}),
                    ({}, {"value_source": "estimated_from_axis"})]
        for chart_update, fact_update in variants:
            with self.subTest(chart_update=chart_update, fact_update=fact_update):
                chart = self.chart()
                chart.update(chart_update); chart["facts"][1].update(fact_update)
                operand = self.operand(evidence_id=chart["id"], row_index=None, column_index=None, fact_index=1)
                with self.assertRaises(ValueError):
                    self.numeric._validated_selection(self.chart_claim(), [chart], self.selection(operand))

    def test_chart_category_does_not_supply_wrong_year_or_missing_quarter(self):
        chart = self.chart()
        operand = self.operand(evidence_id=chart["id"], row_index=None, column_index=None, fact_index=1)
        for updates in ({"end_period": 2025}, {"period_type": "quarter", "quarter": 1}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.numeric._validated_selection(self.chart_claim(**updates), [chart], self.selection(operand))

    def test_chart_explicit_period_still_works_without_category_inference(self):
        chart = self.chart()
        chart.update(title="Customer count")
        chart["facts"][1].update(period="2026", category="All customers")
        operand = self.operand(evidence_id=chart["id"], row_index=None, column_index=None, fact_index=1)
        selected = self.numeric._validated_selection(self.chart_claim(), [chart], self.selection(operand))
        self.assertEqual(selected.operands[0].fact_index, 1)
        chart["facts"][1].update(period=None, category="2026")
        chart["x_axis"] = {"label": "Year"}
        self.numeric._validated_selection(self.chart_claim(), [chart], self.selection(operand))

    def test_unique_chart_is_matched_locally_independently_of_claimed_value(self):
        for literal, expected, difference in (("120", "SUPPORTED", 0), ("100", "CONTRADICTED", -20),
                                               ("80", "CONTRADICTED", -40)):
            with self.subTest(literal=literal):
                chart = self.chart()
                original_facts = copy.deepcopy(chart["facts"])
                calc, comp, reason, _ = self.numeric.verify_numeric(
                    self.chart_claim(claimed_literal=literal), [chart])
                self.assertIsNotNone(calc, reason)
                self.assertEqual(comp["verdict"], expected)
                self.assertEqual(comp["actual"], 120)
                self.assertEqual(comp["difference"], difference)
                self.assertEqual(calc["source_inputs"]["value"]["fact_index"], 1)
                self.assertEqual(calc["source_inputs"]["value"]["page"], 2)
                self.assertEqual(chart["facts"], original_facts)
        self.numeric.structured.assert_not_called()

    def test_unique_chart_growth_is_local_for_correct_and_incorrect_percentages(self):
        for literal, expected in (("50%", "SUPPORTED"), ("25%", "CONTRADICTED")):
            with self.subTest(literal=literal):
                pc = self.chart_claim(operation="growth", claimed_literal=literal, unit="percent", start_period=2025)
                calc, comp, reason, _ = self.numeric.verify_numeric(pc, [self.chart()])
                self.assertIsNotNone(calc, reason)
                self.assertEqual(comp["verdict"], expected)
                self.assertEqual(calc["inputs"], {"old": 80, "new": 120})
                self.assertEqual(calc["value"], 50)
        self.numeric.structured.assert_not_called()

    def test_unique_chart_metric_excludes_unrelated_chart_with_claimed_value(self):
        unrelated = self.chart()
        unrelated.update(id="other-chart", title="Annual operating profit")
        unrelated["facts"][1].update(value=100, display_value="100")
        calc, comp, reason, _ = self.numeric.verify_numeric(
            self.chart_claim(claimed_literal="100"), [unrelated, self.chart()])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comp["verdict"], "CONTRADICTED")
        self.assertEqual(calc["value"], 120)
        self.numeric.structured.assert_not_called()

    def test_ambiguous_chart_facts_keep_model_source_selection_fallback(self):
        chart = self.chart()
        conflicting = copy.deepcopy(chart["facts"][1])
        conflicting.update(value=100, display_value="100")
        chart["facts"].append(conflicting)
        pc = self.chart_claim(claimed_literal="100")
        self.assertIsNone(self.numeric._local_selection(pc, [chart]))
        operand = self.operand(evidence_id=chart["id"], row_index=None, column_index=None, fact_index=1)
        self.numeric.structured.side_effect = [self.selection(operand)]
        calc, comp, reason, _ = self.numeric.verify_numeric(pc, [chart])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comp["verdict"], "CONTRADICTED")
        self.assertEqual(calc["value"], 120)
        self.numeric.structured.assert_called_once()

    def test_missing_chart_year_keeps_absent_evidence_fallback(self):
        self.numeric.structured.side_effect = [self.selection(applicable=False, reason="Requested year absent")]
        calc, comp, reason, _ = self.numeric.verify_numeric(self.chart_claim(end_period=2040), [self.chart()])
        self.assertIsNone(calc)
        self.assertIsNone(comp)
        self.assertEqual(reason, "Requested year absent")
        self.numeric.structured.assert_called_once()

    def test_partial_chart_growth_never_selects_an_incomplete_operand_set(self):
        chart = self.chart()
        chart["facts"] = chart["facts"][1:]
        pc = self.chart_claim(operation="growth", claimed_literal="50%", unit="percent", start_period=2025)
        self.assertIsNone(self.numeric._local_selection(pc, [chart]))

    def test_local_chart_matching_rejects_estimates_and_conflicting_periods(self):
        for changes in ({"value_source": "estimated_from_axis"}, {"period": "2025"}, {"category": "Model 2026"}):
            with self.subTest(changes=changes):
                chart = self.chart()
                chart["facts"][1].update(changes)
                self.assertIsNone(self.numeric._local_selection(self.chart_claim(), [chart]))

    def test_table_and_chart_competing_matches_remain_ambiguous(self):
        table = {"id": "table", "document_id": "doc-a", "page": 1, "type": "table",
                 "headers": ["Metric", "Year ended 2026"], "rows": [["Customer count", "100"]],
                 "column_map": [{"col": 1, "year": 2026, "period": "year", "raw": "Year ended 2026"}],
                 "column_headers": {"raw": "Annual customer count"}, "unit": "customers"}
        # Each source is locally valid, but together they cannot be selected uniquely.
        self.assertIsNotNone(self.numeric._local_selection(self.chart_claim(), [table]))
        self.assertIsNone(self.numeric._local_selection(self.chart_claim(), [table, self.chart()]))


if __name__ == "__main__": unittest.main()
