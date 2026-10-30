"""Source-column and quantity regressions; no PDFs, model calls, or indexes written."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))


def load_module(stack, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    stack.enter_context(patch.dict(sys.modules, {name: module}))
    spec.loader.exec_module(module)
    return module


class SourceOperand:
    """A controlled model-selected address; numerical resolution stays real."""

    def __init__(self, name="value", evidence_id="source", column_index=1):
        self.name = name
        self.evidence_id = evidence_id
        self.row_index = 0
        self.column_index = column_index
        self.number_index = None
        self.fact_index = None
        self.unit_quote = ""
        self.scope_quote = ""

    def model_dump(self, **kwargs):
        return dict(vars(self))


class TableColumnTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        pdfplumber = types.ModuleType("pdfplumber")
        pdfplumber.open = MagicMock()
        self.stack.enter_context(patch.dict(sys.modules, {"pdfplumber": pdfplumber}))
        self.tables = load_module(self.stack, "_tables_under_test", APP / "extraction/table_extractor.py")

    def test_missing_values_keep_leading_middle_and_trailing_columns(self):
        raw = [["Metric", "2025", "2024", "2023"],
               ["Revenue", None, "200", "150"],
               ["Profit", "100", "", "80"],
               ["Assets", "300", "250", None]]
        self.assertEqual(self.tables._clean_table(raw), [
            ["Metric", "2025", "2024", "2023"],
            ["Revenue", "", "200", "150"],
            ["Profit", "100", "", "80"],
            ["Assets", "300", "250", ""],
        ])

    def test_blank_label_and_zero_are_preserved(self):
        self.assertEqual(self.tables._clean_table([["", "0", 0]]), [["", "0", "0"]])

    def test_empty_rows_are_removed_without_moving_cells(self):
        raw = [[None, " "], ["Revenue\nnet", "", " 100 "], [None, None]]
        self.assertEqual(self.tables._clean_table(raw), [["Revenue net", "", "100"]])

    def test_empty_input(self):
        for raw in (None, []):
            with self.subTest(raw=raw):
                self.assertEqual(self.tables._clean_table(raw), [])

    def test_currency_columns_merge_for_every_row(self):
        raw = [["Metric", "", "2025", "", "2024"],
               ["Revenue", "$", "100", "$", "90"],
               ["Profit", "", "20", "", "10"]]
        self.assertEqual(self.tables._clean_table(raw), [
            ["Metric", "2025", "2024"], ["Revenue", "$100", "$90"], ["Profit", "20", "10"]])

    def test_currency_merge_preserves_year_in_the_left_header_cell(self):
        raw = [["Metric", "2025", "", "2024", ""], ["Revenue", "$", "100", "$", "90"]]
        self.assertEqual(self.tables._clean_table(raw), [
            ["Metric", "2025", "2024"], ["Revenue", "$100", "$90"]])

    def test_currency_column_with_missing_amount_keeps_blank_value(self):
        raw = [["Metric", "", "2025", "", "2024"],
               ["Revenue", "$", "", "$", "90"], ["Profit", "$", "20", "$", "10"]]
        self.assertEqual(self.tables._clean_table(raw)[1], ["Revenue", "", "$90"])

    def test_all_blank_period_column_is_not_removed(self):
        raw = [["Metric", "2025", "2024"], ["Revenue", "", "90"], ["Profit", "", "10"]]
        self.assertEqual(self.tables._clean_table(raw), raw)

    def test_mixed_numeric_currency_column_is_not_removed(self):
        raw = [["Metric", "2025", "2024"], ["Revenue", "$", "90"], ["Profit", "20", "10"]]
        self.assertEqual(self.tables._clean_table(raw), raw)

    def test_conflicting_nonempty_headers_are_not_merged(self):
        raw = [["Metric", "2025", "2024"], ["Revenue", "$", "90"]]
        self.assertEqual(self.tables._clean_table(raw), raw)

    def test_ragged_rows_are_not_compacted_or_filled_with_values(self):
        raw = [["Metric", "2025", "2024"], ["Revenue", "100"]]
        self.assertEqual(self.tables._clean_table(raw), raw)

    def test_period_count_mismatch_has_no_guessed_positional_mapping(self):
        for columns in (1, 3):
            with self.subTest(columns=columns):
                years, periods, mapping = self.tables._parse_periods("Years ended 2025 2024", columns)
                self.assertEqual(years, [2025, 2024])
                self.assertEqual(mapping, [])

    def test_exact_period_count_still_maps_each_column(self):
        _, _, mapping = self.tables._parse_periods("Years ended 2025 2024", 2)
        self.assertEqual([(m["col"], m["year"]) for m in mapping], [(1, 2025), (2, 2024)])

    def test_extraction_keeps_empty_2025_cell_and_correct_period_mapping(self):
        raw = [["Metric", "2025", "2024"], ["Revenue", "", "200"], ["Profit", "10", "9"]]
        table = types.SimpleNamespace(bbox=(0, 20, 300, 100), extract=lambda: raw)
        words = [{"text": "Years ended 2025 2024", "top": 5, "bottom": 10, "x0": 0, "x1": 200}]
        page = types.SimpleNamespace(extract_words=lambda: words, find_tables=lambda: [table])
        pdf = MagicMock()
        pdf.__enter__.return_value = types.SimpleNamespace(pages=[page])
        self.tables.pdfplumber.open.return_value = pdf
        with patch.object(self.tables.os.path, "exists", return_value=True):
            result = self.tables.extract_tables("synthetic-document")["tables"][0]
        self.assertEqual(result["rows"][0], ["Revenue", "", "200"])
        self.assertEqual(result["data_columns"], 2)
        self.assertEqual([(m["col"], m["year"]) for m in result["column_map"]], [(1, 2025), (2, 2024)])


class NumericalTypeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Response-schema validation and provider calls are outside these tests.
        # Actual source resolution, parsing, arithmetic, and comparison run below.
        pydantic = types.ModuleType("pydantic")
        pydantic.BaseModel = object
        pydantic.Field = lambda **kwargs: None
        gateway = types.ModuleType("services.model_gateway")
        gateway.structured = MagicMock(side_effect=AssertionError("Unexpected model call"))
        gateway.capture_calls = MagicMock(side_effect=AssertionError("Unexpected model call"))
        self.stack.enter_context(patch.dict(sys.modules, {"pydantic": pydantic, "services.model_gateway": gateway}))
        self.numeric = load_module(self.stack, "_grounded_numeric_under_test", APP / "verification/grounded_numeric.py")

    def verify(self, literal, values, *, operation="level", claim_unit="unspecified", context=""):
        periods = [2025] if len(values) == 1 else [2024, 2025]
        evidence = {"id": "source", "type": "table", "document_id": "synthetic-document", "page": 1,
                    "headers": ["Metric", *map(str, periods)], "rows": [["Revenue", *values]],
                    "column_map": [{"col": i + 1, "year": year, "period": "year"} for i, year in enumerate(periods)],
                    "column_headers": {"raw": "Years ended " + " ".join(map(str, periods))}, "context": context}
        claim = {"operation": operation, "claimed_literal": literal, "metric": "Revenue", "unit": claim_unit,
                 "end_period": 2025, "period_type": "annual"}
        if len(values) == 2:
            claim["start_period"] = 2024
        if operation == "direction":
            claim["direction"] = "up"
        names = ["old", "new"] if operation in {"growth", "absolute_change", "percentage_points", "cagr", "direction"} else ["value"]
        columns = list(range(1, len(values) + 1)) if len(names) == 2 else [len(values)]
        selection = types.SimpleNamespace(applicable=True, reason="Controlled source addresses",
                                         operands=[SourceOperand(name=name, column_index=column) for name, column in zip(names, columns)])
        with patch.object(self.numeric, "_select", return_value=selection):
            return self.numeric.verify_numeric(claim, [evidence])

    def assert_unavailable(self, result):
        calculation, comparison, reason, used = result
        self.assertIsNone(calculation)
        self.assertIsNone(comparison)
        self.assertTrue(reason)
        self.assertEqual(used, [])

    def test_percent_claim_cannot_match_plain_number(self):
        self.assert_unavailable(self.verify("100%", ["100"], claim_unit="percent"))

    def test_literal_types_are_checked_without_declared_unit_metadata(self):
        for literal, source in (("100%", "100"), ("100%", "USD 100"),
                                ("$100", "100"), ("100", "USD 100")):
            with self.subTest(literal=literal, source=source):
                self.assert_unavailable(self.verify(literal, [source]))

    def test_percent_claim_cannot_match_monetary_amount(self):
        self.assert_unavailable(self.verify("100%", ["USD 100"], claim_unit="percent"))

    def test_amount_claim_cannot_match_percent(self):
        self.assert_unavailable(self.verify("USD 100", ["100%"], claim_unit="amount"))

    def test_count_claim_cannot_match_monetary_amount(self):
        self.assert_unavailable(self.verify("100", ["USD 100"], claim_unit="number"))

    def test_amount_claim_cannot_match_plain_count(self):
        for literal in ("USD 100", "$100"):
            with self.subTest(literal=literal):
                self.assert_unavailable(self.verify(literal, ["100"], claim_unit="amount"))

    def test_currency_symbol_without_currency_code_is_still_an_amount(self):
        self.assert_unavailable(self.verify("100", ["$100"], claim_unit="number"))

    def test_matching_amounts_counts_and_percentages_still_verify(self):
        for literal, source, unit in (("USD 100", "USD 100", "amount"),
                                      ("100", "100", "number"),
                                      ("100%", "100%", "percent"),
                                      ("$100", "$100", "amount")):
            with self.subTest(literal=literal):
                calculation, comparison, _, _ = self.verify(literal, [source], claim_unit=unit)
                self.assertIsNotNone(calculation)
                self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_explicit_currency_mismatch_remains_unavailable(self):
        self.assert_unavailable(self.verify("EUR 100", ["USD 100"], claim_unit="amount"))

    def test_wrong_compatible_value_remains_contradicted(self):
        _, comparison, _, _ = self.verify("USD 200", ["USD 100"], claim_unit="amount")
        self.assertEqual(comparison["verdict"], "CONTRADICTED")

    def test_growth_on_counts_and_amounts_still_produces_percent(self):
        for values in (["100", "200"], ["USD 100", "USD 200"], ["$100", "$200"]):
            with self.subTest(values=values):
                calculation, comparison, _, _ = self.verify("100%", values, operation="growth", claim_unit="percent")
                self.assertEqual(calculation["result_unit"], "percent")
                self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_growth_rejects_count_and_amount_operands_mixed_together(self):
        self.assert_unavailable(self.verify("100%", ["$100", "200"], operation="growth", claim_unit="percent"))

    def test_relative_growth_of_percentages_still_verifies(self):
        calculation, comparison, _, _ = self.verify("100%", ["10%", "20%"], operation="growth", claim_unit="percent")
        self.assertEqual(calculation["value"], 100)
        self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_direction_does_not_require_a_monetary_zero_literal(self):
        for values in (["100", "200"], ["$100", "$200"], ["10%", "20%"]):
            with self.subTest(values=values):
                _, comparison, _, _ = self.verify(None, values, operation="direction")
                self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_explicit_currency_and_scale_context_still_verifies(self):
        calculation, comparison, _, _ = self.verify("USD 100 million", ["100"], claim_unit="amount", context="USD millions")
        self.assertEqual(calculation["value"], 100000000)
        self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_percentage_points_require_percentage_sources(self):
        self.assert_unavailable(self.verify("10 percentage points", ["10", "20"],
                                            operation="percentage_points", claim_unit="percentage_points"))

    def test_percentage_point_literal_is_not_a_relative_percent(self):
        self.assert_unavailable(self.verify("10%", ["10%", "20%"],
                                            operation="percentage_points", claim_unit="percent"))

    def test_percentage_point_change_still_verifies(self):
        calculation, comparison, _, _ = self.verify("10 percentage points", ["10%", "20%"],
                                                   operation="percentage_points", claim_unit="percentage_points")
        self.assertEqual(calculation["result_unit"], "percentage points")
        self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_absolute_count_change_still_verifies(self):
        calculation, comparison, _, _ = self.verify("100", ["100", "200"], operation="absolute_change", claim_unit="number")
        self.assertEqual(calculation["result_unit"], "number")
        self.assertEqual(comparison["verdict"], "SUPPORTED")

    def test_absolute_percent_change_cannot_be_reported_as_plain_number(self):
        self.assert_unavailable(self.verify("10", ["10%", "20%"], operation="absolute_change", claim_unit="number"))

    def test_missing_2025_value_cannot_use_the_2024_cell(self):
        calculation, comparison, _, _ = self.verify("200", ["200", ""], operation="level", claim_unit="number")
        self.assertIsNone(calculation)
        self.assertIsNone(comparison)

    def test_margin_rejects_money_and_count_operands(self):
        self.assert_unavailable(self.verify_margin("$10", "100"))

    def test_margin_with_explicit_monetary_units_still_verifies(self):
        calculation, comparison, _, _ = self.verify_margin("10", "100", context="USD millions")
        self.assertEqual(calculation["value"], 10)
        self.assertEqual(comparison["verdict"], "SUPPORTED")

    def verify_margin(self, profit, revenue, *, context=""):
        evidence = {"id": "source", "type": "table", "document_id": "synthetic-document", "page": 1,
                    "headers": ["Metric", "2025"], "rows": [["Operating profit", profit], ["Revenue", revenue]],
                    "column_map": [{"col": 1, "year": 2025, "period": "year"}],
                    "column_headers": {"raw": "Years ended 2025"}, "context": context}
        claim = {"operation": "margin", "claimed_literal": "10%", "metric": "Operating margin", "unit": "percent",
                 "end_period": 2025, "period_type": "annual"}
        profit_address = SourceOperand(name="profit")
        revenue_address = SourceOperand(name="revenue")
        revenue_address.row_index = 1
        selection = types.SimpleNamespace(applicable=True, reason="Controlled source addresses",
                                         operands=[profit_address, revenue_address])
        with patch.object(self.numeric, "_select", return_value=selection):
            return self.numeric.verify_numeric(claim, [evidence])

    def test_legacy_ragged_row_is_declined_before_using_shifted_value(self):
        evidence = {"id": "source", "type": "table", "document_id": "synthetic-document", "page": 1,
                    "headers": ["Metric", "2025", "2024"], "rows": [["Revenue", "200"], ["Profit", "10", "9"]],
                    "column_map": [{"col": 1, "year": 2025, "period": "year"}, {"col": 2, "year": 2024, "period": "year"}],
                    "column_headers": {"raw": "Years ended 2025 2024"}}
        claim = {"operation": "level", "claimed_literal": "200", "metric": "Revenue", "unit": "number",
                 "end_period": 2025, "period_type": "annual"}
        selection = types.SimpleNamespace(applicable=True, reason="Controlled source address", operands=[SourceOperand()])
        with patch.object(self.numeric, "_select", return_value=selection):
            self.assert_unavailable(self.numeric.verify_numeric(claim, [copy.deepcopy(evidence)]))


if __name__ == "__main__":
    unittest.main()
