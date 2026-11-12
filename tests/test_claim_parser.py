"""Replay malformed parser responses; real normalization/arithmetic, no provider calls."""
from contextlib import ExitStack, contextmanager
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

A = "At June 27, 2026, Apple's total deferred revenue was $14.9 billion."
B = "At September 27, 2025, Apple's total deferred revenue was $14.9 billion."
C = "Apple's total deferred revenue grew 8.8% between September 27, 2025 and June 27, 2026."
D = "At June 27, 2040, Apple's total deferred revenue was $14.9 billion."
SOURCE = ("As of June 27, 2026 and September 27, 2025, the Company had total "
          "deferred revenue of $14.9 billion and $13.7 billion, respectively.")


@contextmanager
def capture_calls():
    yield [], 0


def load_module(stack, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    stack.enter_context(patch.dict(sys.modules, {name: module}))
    spec.loader.exec_module(module)
    return module


class ClaimParserTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.gateway = types.ModuleType("services.model_gateway")
        self.gateway.model_name = lambda: "fixture-model"
        self.gateway.capture_calls = capture_calls
        self.gateway.structured = MagicMock(side_effect=AssertionError("Unexpected provider call"))
        modules = {"services.model_gateway": self.gateway}
        # The operator/container run uses real Pydantic. Local Blender Python
        # lacks its native module; response shape is the only stubbed boundary.
        try:
            import pydantic
            if not hasattr(pydantic.BaseModel, "model_validate"):
                raise ImportError("Pydantic v2 unavailable")
        except ImportError:
            pydantic = types.ModuleType("pydantic")
            pydantic.BaseModel = object
            pydantic.Field = lambda **kwargs: None
            modules["pydantic"] = pydantic
        self.stack.enter_context(patch.dict(sys.modules, modules))
        self.parser = load_module(self.stack, "_claim_parser_regressions", APP / "verification/claim_parser.py")
        self.numeric = load_module(self.stack, "_claim_numeric_regressions", APP / "verification/grounded_numeric.py")

    def atom(self, text=A, **updates):
        data = dict(claim_text=text, company="Apple", subject="total deferred revenue", metric="total deferred revenue",
                    segment=None, start_period=None, end_period=2026, period_quote="June 27, 2026",
                    period_type="point_in_time", temporal_status="historical", quarter=None, operation="level",
                    claimed_literal="$14.9 billion", claimed_change=None, comparison_operator="equal",
                    direction="flat", unit="amount", claim_type="assertion", negated=False, approximate=False, notes=[])
        data.update(updates)
        if hasattr(self.parser.ParsedClaim, "model_validate"):
            return self.parser.ParsedClaim.model_validate(data)
        return types.SimpleNamespace(model_dump=lambda **kw: copy.deepcopy(data))

    def growth(self, **updates):
        data = dict(operation="growth", start_period=2025, end_period=2026, period_quote=None,
                    period_type="unspecified", claimed_literal="8.8%", direction="up", unit="percent")
        data.update(updates)
        return self.atom(C, **data)

    def respond(self, *responses):
        self.gateway.structured.side_effect = [types.SimpleNamespace(claims=atoms) for atoms in responses]

    def test_reported_periods_are_retained_for_later_source_resolution(self):
        text = "Apple's total deferred revenue grew 8.8% over the reported periods."
        self.respond([self.atom(text, operation="growth", claimed_literal="8.8%", unit="percent",
                                direction="up", start_period=None, end_period=None,
                                period_type="unspecified", period_quote="reported periods")])
        parsed = self.parser.parse_claims(text)[0]
        self.assertNotIn("parsing_error", parsed)
        self.assertIsNone(parsed["start_period"])
        self.assertIsNone(parsed["end_period"])
        self.assertEqual(parsed["period_quote"], "reported periods")
        self.assertEqual(self.gateway.structured.call_count, 1)

    def test_placeholder_text_triggers_correction_and_returns_real_statement(self):
        self.respond([self.atom("assertion")], [self.atom()])
        output = self.parser.parse_claims(A)
        self.assertEqual(output[0]["claim_text"], A)
        self.assertNotIn("parsing_error", output[0])
        self.assertEqual(self.gateway.structured.call_count, 2)
        self.assertIn("LOCAL VALIDATION ERROR", self.gateway.structured.call_args.args[1])

    def test_persistent_bad_response_is_parsing_failure_not_completed_unknown(self):
        self.respond([self.atom("assertion")], [self.atom("assertion")])
        output = self.parser.parse_claims(A)
        self.assertTrue(output[0].get("parsing_error"))
        self.assertEqual(output[0]["error_kind"], "ParseValidationError")
        self.assertEqual(output[0]["claim_text"], A)
        self.assertNotIn("verdict", output[0])
        self.assertEqual(self.gateway.structured.call_count, 2)

    def test_compound_retry_keeps_both_distinct_original_clauses(self):
        original = A + " " + B
        second = dict(end_period=2025, period_quote="September 27, 2025")
        self.respond([self.atom("assertion"), self.atom("assertion", **second)],
                     [self.atom(), self.atom(B, **second)])
        output = self.parser.parse_claims(original)
        self.assertEqual([a["claim_text"] for a in output], [A, B])
        self.assertEqual([a["end_period"] for a in output], [2026, 2025])
        self.assertTrue(all("parsing_error" not in a for a in output))
        self.assertEqual([self.calculate(a)[1]["verdict"] for a in output], ["SUPPORTED", "CONTRADICTED"])

    def test_dropped_compound_year_still_fails_validation(self):
        self.respond([self.atom()], [self.atom()])
        self.assertTrue(self.parser.parse_claims(A + " " + B)[0].get("parsing_error"))

    def test_quantity_from_other_clause_cannot_replace_atomic_claim(self):
        item = self.atom("Apple reported revenue.")
        with self.assertRaises(self.parser.ParseValidationError):
            self.parser._normalize(item, A + " Apple reported revenue.")

    def test_qualitative_placeholder_is_invalid_even_if_label_occurs_in_input(self):
        original = "The assertion about Apple's revenue was false."
        item = self.atom("assertion", operation="qualitative", claimed_literal=None, period_quote=None)
        with self.assertRaises(self.parser.ParseValidationError):
            self.parser._normalize(item, original)

    def test_invented_comparison_words_in_atomic_text_are_rejected(self):
        with self.assertRaises(self.parser.ParseValidationError):
            self.parser._normalize(self.growth(claim_text=C.replace("grew ", "grew more than ")), C)

    def test_dropped_threshold_words_trigger_retry_instead_of_changing_the_claim(self):
        original = C.replace("grew ", "grew more than ")
        self.respond([self.growth()], [self.growth(claim_text=original)])
        output = self.parser.parse_claims(original)
        self.assertEqual(output[0]["claim_text"], original)
        self.assertEqual(output[0]["comparison_operator"], "greater_than")
        self.assertEqual(self.gateway.structured.call_count, 2)

    def test_threshold_cannot_move_to_the_wrong_compound_clause(self):
        other = B.replace("was ", "was more than ")
        original = A + " " + other
        item = self.atom(claim_text=A.replace("was ", "was more than "))
        with self.assertRaises(self.parser.ParseValidationError):
            self.parser._normalize(item, original)

    def test_generic_metric_uses_only_the_financial_subject_stated_in_input(self):
        output = self.parser._normalize(self.growth(metric="growth rate", comparison_operator="greater_than"), C)
        self.assertEqual(output["metric"], "total deferred revenue")
        self.assertEqual(output["comparison_operator"], "equal")

    def test_missing_metric_is_recovered_from_grounded_subject(self):
        output = self.parser._normalize(self.atom(metric=None), A)
        self.assertEqual(output["metric"], "total deferred revenue")

    def test_unstated_metric_and_subject_are_rejected(self):
        item = self.growth(metric="growth rate", subject="operating cash flow")
        with self.assertRaises(self.parser.ParseValidationError):
            self.parser._normalize(item, C)

    def test_amount_unit_and_point_in_time_operation_follow_original_quantity(self):
        for period_type in ("point_in_time", "unspecified"):
            with self.subTest(period_type=period_type):
                output = self.parser._normalize(self.atom(B, unit="percent", operation="absolute_change", period_type=period_type,
                                                           end_period=2025, period_quote="September 27, 2025"), B)
                self.assertEqual((output["unit"], output["operation"], output["start_period"]), ("amount", "level", None))

    def test_explicit_as_of_date_overrides_annual_label_without_a_model_retry(self):
        self.respond([self.atom(period_type="annual", quarter=2)])
        result = self.parser.parse_claims(A)[0]
        self.assertEqual(result["period_type"], "point_in_time")
        self.assertIsNone(result["quarter"])
        self.assertEqual(self.gateway.structured.call_count, 1)

    def test_explicit_reporting_durations_override_wrong_point_in_time_labels(self):
        for wording, expected in (("three months", "quarter"), ("six months", "six_months"),
                                  ("nine months", "nine_months"), ("twelve months", "annual")):
            text = f"Apple's revenue was $100 million for the {wording} ended June 27, 2026."
            with self.subTest(wording=wording):
                item = self.atom(text, subject="revenue", metric="revenue", claimed_literal="$100 million",
                                 period_quote=f"{wording} ended June 27, 2026", period_type="point_in_time")
                self.assertEqual(self.parser._normalize(item, text)["period_type"], expected)

    def test_explicit_annual_period_is_preserved_instead_of_using_end_date_as_snapshot(self):
        text = "Apple's revenue was $100 million for the fiscal year ended June 27, 2026."
        item = self.atom(text, subject="revenue", metric="revenue", claimed_literal="$100 million",
                         period_quote="fiscal year ended June 27, 2026", period_type="point_in_time")
        self.assertEqual(self.parser._normalize(item, text)["period_type"], "annual")

    def test_conflicting_durations_require_separate_atomic_claims(self):
        text = "Apple's revenue was $100 million for the three months and nine months ended June 27, 2026."
        item = self.atom(text, subject="revenue", metric="revenue", claimed_literal="$100 million", period_quote="June 27, 2026")
        with self.assertRaisesRegex(self.parser.ParseValidationError, "conflicting reporting durations"):
            self.parser._normalize(item, text)

    def test_explicit_quarter_is_retained_without_inference_from_calendar_month(self):
        text = "Apple's revenue was $100 million in the third fiscal quarter of 2026."
        item = self.atom(text, subject="revenue", metric="revenue", claimed_literal="$100 million",
                         period_quote="third fiscal quarter of 2026", period_type="annual", quarter=3)
        result = self.parser._normalize(item, text)
        self.assertEqual((result["period_type"], result["quarter"]), ("quarter", 3))

    def test_plain_growth_is_equal_but_explicit_thresholds_remain_inequalities(self):
        examples = (("", "equal"), ("more than ", "greater_than"), ("at least ", "at_least"),
                    ("less than ", "less_than"), ("no more than ", "at_most"),
                    ("> ", "greater_than"), (">= ", "at_least"), ("<= ", "at_most"))
        for qualifier, expected in examples:
            with self.subTest(qualifier=qualifier):
                text = C.replace("8.8%", qualifier+"8.8%")
                item = self.growth(claim_text=text, comparison_operator="greater_than")
                self.assertEqual(self.parser._normalize(item, text)["comparison_operator"], expected)

    def test_negation_and_approximation_are_retained(self):
        text = C.replace("grew 8.8%", "did not grow approximately 8.8%")
        output = self.parser._normalize(self.growth(claim_text=text, negated=True, approximate=True), text)
        self.assertTrue(output["negated"])
        self.assertTrue(output["approximate"])

    def test_percentage_points_keep_their_distinct_unit(self):
        text = "Apple's profit margin increased by 5 percentage points between 2025 and 2026."
        item = self.growth(claim_text=text, subject="profit margin", metric="profit margin",
                           claimed_literal="5", operation="percentage_points", unit="percent")
        output = self.parser._normalize(item, text)
        self.assertEqual(output["unit"], "percentage_points")

    def test_shared_qualifiers_can_be_repeated_when_splitting_a_compound(self):
        original = "In 2026, Apple's revenue was $100 million and profit $20 million."
        text = "In 2026, Apple's profit was $20 million."
        item = self.atom(text, subject="profit", metric="profit", claimed_literal="$20 million", period_quote="2026")
        self.assertEqual(self.parser._normalize(item, original)["claim_text"], text)

    def test_questions_stay_questions(self):
        text = "What was Apple's total deferred revenue at June 27, 2026?"
        output = self.parser._normalize(self.atom(text, claimed_literal=None), text)
        self.assertEqual(output["claim_type"], "question")

    def test_claimed_quantity_still_cannot_be_invented(self):
        with self.assertRaises(self.parser.ParseValidationError):
            self.parser._normalize(self.atom(claimed_literal="$99 billion"), A)

    def test_currency_and_scale_cannot_disappear_from_literal(self):
        for literal in ("14.9", "14.9 billion", "$14.9"):
            with self.subTest(literal=literal), self.assertRaises(self.parser.ParseValidationError):
                self.parser._normalize(self.atom(claimed_literal=literal), A)

    def test_missing_display_quantity_is_restored_without_another_model_call(self):
        text = A.replace("$14.9 billion", "").replace("was .", "was.")
        for metric in ("total deferred revenue", None, "growth rate"):
            with self.subTest(metric=metric):
                self.gateway.structured.reset_mock()
                atom = self.atom(text, metric=metric)
                self.respond([atom], [atom])
                result = self.parser.parse_claims(A)[0]
                self.assertNotIn("parsing_error", result, result)
                self.assertEqual(result["claimed_literal"], "$14.9 billion")
                self.assertIn("$14.9 billion", result["claim_text"])
                self.assertEqual(self.calculate(result)[1]["verdict"], "SUPPORTED")
                self.assertEqual(self.gateway.structured.call_count, 1)

    def test_display_repair_cannot_hide_a_changed_sign_currency_or_quantity_type(self):
        for replacement in ("$-14.9 billion", "€14.9 billion", "14.9%", "2026"):
            with self.subTest(replacement=replacement):
                atom = self.atom(A.replace("$14.9 billion", replacement))
                self.respond([atom], [atom])
                result = self.parser.parse_claims(A)[0]
                self.assertEqual(result.get("error_kind"), "ParseValidationError", result)
                self.assertNotIn("verdict", result)

    def test_display_repair_cannot_hide_a_value_borrowed_from_another_clause(self):
        original = A + " At June 27, 2026, Apple's profit was $20 million."
        profit = self.atom("At June 27, 2026, Apple's profit was $20 million.",
                           metric="profit", subject="profit", claimed_literal="$20 million")
        for replacement in ("$20 million", "$14.9 million"):
            with self.subTest(replacement=replacement):
                wrong = self.atom(A.replace("$14.9 billion", replacement))
                self.respond([wrong, profit], [wrong, profit])
                result = self.parser.parse_claims(original)[0]
                self.assertEqual(result.get("error_kind"), "ParseValidationError", result)
                self.assertNotIn("verdict", result)

    def test_display_repair_does_not_guess_between_repeated_metric_values(self):
        original = A + " " + B
        missing = self.atom(A.replace("$14.9 billion", ""))
        other = self.atom(B, end_period=2025, period_quote="September 27, 2025")
        self.respond([missing, other], [missing, other])
        result = self.parser.parse_claims(original)[0]
        self.assertEqual(result.get("error_kind"), "ParseValidationError", result)

    def test_display_repair_does_not_restore_an_incomplete_structured_literal(self):
        text = A.replace("$14.9 billion", "")
        for literal in ("14.9", "14.9 billion", "$14.9"):
            with self.subTest(literal=literal):
                atom = self.atom(text, claimed_literal=literal)
                self.respond([atom], [atom])
                result = self.parser.parse_claims(A)[0]
                self.assertEqual(result.get("error_kind"), "ParseValidationError", result)

    def test_corrected_parser_output_reaches_real_growth_arithmetic(self):
        self.respond([self.growth(metric="growth rate", comparison_operator="greater_than")])
        pc = self.parser.parse_claims(C)[0]
        result = self.calculate(pc)
        self.assertEqual(result[1]["verdict"], "SUPPORTED")
        self.assertAlmostEqual(result[1]["actual"], 8.75912408759124)
        self.assertAlmostEqual(result[1]["difference"], 0.040875912408759874)
        self.assertEqual(result[0]["raw_inputs"], {"old": "$13.7 billion", "new": "$14.9 billion"})

    def test_levels_use_their_own_year_and_future_period_stays_unavailable(self):
        for text, year, verdict in ((A, 2026, "SUPPORTED"), (B, 2025, "CONTRADICTED"), (D, 2040, None)):
            with self.subTest(year=year):
                item = self.atom(text, end_period=year, period_quote=f"{'September 27' if year == 2025 else 'June 27'}, {year}")
                pc = self.parser._normalize(item, text)
                calc, comparison, reason, used = self.calculate(pc)
                self.assertEqual(comparison["verdict"] if comparison else None, verdict)
                if year == 2040:
                    self.assertIsNone(calc)
                    self.assertTrue(reason)

    def calculate(self, pc):
        evidence = dict(id="source", document_id="fixture-document", source_evidence_id="page9",
                        type="text", page=9, content=SOURCE)
        bank = self.numeric._bank(evidence)
        values = (("old", "$13.7 billion"), ("new", "$14.9 billion")) if pc["operation"] == "growth" else (
            ("value", "$13.7 billion" if pc["end_period"] == 2025 else "$14.9 billion"),)
        operands = []
        for name, raw in values:
            data = dict(name=name, evidence_id="source", row_index=None, column_index=None, fact_index=None,
                        number_index=next(n["index"] for n in bank if n["raw"] == raw), unit_quote="", scope_quote="")
            operand = types.SimpleNamespace(**data)
            operand.model_dump = lambda data=data, **kw: dict(data)
            operands.append(operand)
        selection = types.SimpleNamespace(applicable=True, reason="Controlled source addresses", operands=operands)
        with patch.object(self.numeric, "_select", return_value=selection):
            return self.numeric.verify_numeric(pc, [evidence])


if __name__ == "__main__":
    unittest.main()
