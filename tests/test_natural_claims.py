"""Offline paraphrase/context regressions. Provider output is controlled."""
import copy
import json
import types
import unittest
from unittest.mock import MagicMock
from test_claim_pipeline import fixture, active_pipeline
from test_claim_parser import ClaimParserTests
from test_source_selection import SourceSelectionTests
from verification.evidence_context import enrich_candidate


class NaturalClaimTests(unittest.TestCase):
    def test_paraphrases_and_yes_no_claims_across_companies(self):
        for company in ("Cedar", "Northstar", "Harbor"):
            for wording in (
                "{c}'s revenue grew 10% between 2025 and 2026.",
                "For the company {c}, revenue increased by 10% between 2025 and 2026.",
                "Did {c}'s revenue grow 10% between 2025 and 2026?",
                "Has {c}'s revenue increased by 10% between 2025 and 2026?",
            ):
                text = wording.format(c=company)
                with self.subTest(text=text), fixture(ClaimParserTests) as case:
                    atom = case.growth(claim_text=text, company=company, subject="revenue", metric="revenue",
                                       claimed_literal="10%", period_quote="2025 and 2026")
                    case.respond([atom], [atom])
                    pc = case.parser.parse_claims(text)[0]
                    self.assertNotIn("parsing_error", pc, pc)
                    self.assertEqual(pc["claim_type"], "assertion")
                    self.assertEqual(pc["claim_text"], text)
                    self.assertEqual((pc["start_period"], pc["end_period"]), (2025, 2026))

    def test_open_and_hypothetical_questions_remain_questions(self):
        for text in ("What was Cedar's revenue in 2026?", "Could Cedar's revenue grow 10% in 2026?"):
            with self.subTest(text=text), fixture(ClaimParserTests) as case:
                atom = case.atom(text, company="Cedar", metric="revenue", subject="revenue",
                                 claimed_literal=None, period_quote="2026")
                case.respond([atom], [atom])
                pc = case.parser.parse_claims(text)[0]
                self.assertEqual(pc["claim_type"], "question", pc)

    def test_compound_yes_no_preserves_two_metrics_quantities_and_shared_dates(self):
        original = "Between 2025 and 2026, did Cedar's revenue grow 10% and profit grow 20%?"
        # Shared leading context is retained in each question.
        with fixture(ClaimParserTests) as case:
            atoms = [case.growth(claim_text=f"Did Cedar's {metric} grow {value} between 2025 and 2026?",
                     company="Cedar", metric=metric, subject=metric, claimed_literal=value,
                     period_quote="2025 and 2026") for metric, value in (("revenue", "10%"), ("profit", "20%"))]
            case.respond(atoms, atoms)
            pcs = case.parser.parse_claims(original)
            self.assertEqual(len(pcs), 2, pcs)
            self.assertEqual([p["claim_type"] for p in pcs], ["assertion", "assertion"])
            self.assertEqual([p["claimed_literal"] for p in pcs], ["10%", "20%"])

    def test_context_preserves_positions_and_distinguishes_parent_rows(self):
        table = dict(id="t", page=3, type="table", title="Statement", headers=["Item", "2026", "2025"],
                     rows=[["Assets", "", ""], ["Inventory", "120", "80"],
                           ["Operating movements", "", ""], ["Inventory", "-10", "5"]])
        before = copy.deepcopy(table)
        enriched = enrich_candidate(table)
        self.assertEqual(table, before)
        self.assertEqual(enriched["rows"], before["rows"])
        self.assertEqual(enriched["candidate_context"]["row_context"], [
            dict(row_index=1, label="Inventory", parent_row_labels=["Assets"]),
            dict(row_index=3, label="Inventory", parent_row_labels=["Operating movements"])])

    def test_text_and_chart_context_keep_source_addresses_and_page_boundary(self):
        for kind in ("text", "ocr", "chart"):
            item = dict(id="a", type=kind, page=2, content="Revenue 120", heading="Results", caption="Annual sales")
            siblings = [item, dict(id="b", type="text", page=2, content="In millions"),
                        dict(id="c", type="text", page=3, content="Unrelated units")]
            result = enrich_candidate(item, siblings)
            self.assertEqual(result["content"], item["content"])
            self.assertEqual(result["candidate_context"]["heading"], "Results")
            self.assertEqual(result["candidate_context"]["same_page_background"], [dict(source_id="b", text="In millions")])

    def test_meaning_choice_is_independent_of_asserted_number(self):
        with fixture(SourceSelectionTests) as case:
            balance = enrich_candidate(case.evidence)
            flow = copy.deepcopy(balance)
            flow.update(id="other", title="Cash flow movements")
            flow = enrich_candidate(flow)
            case.numeric.structured.side_effect = None
            case.numeric.structured.return_value = types.SimpleNamespace(ambiguous=False, evidence_ids=[balance["id"]])
            for amount, verdict in (("94.0%", "SUPPORTED"), ("10%", "CONTRADICTED")):
                calc, comp, reason, used = case.numeric.verify_numeric(
                    case.reported_period_claim(claimed_literal=amount), [balance, flow])
                self.assertIsNotNone(calc, reason)
                self.assertEqual(comp["verdict"], verdict)
                self.assertEqual(used, [balance["id"]])
                request = json.loads(case.numeric.structured.call_args.args[1])
                self.assertNotIn("claimed_literal", request["intent"])
                self.assertNotIn("94.0%", json.dumps(request))

    def test_meaning_ambiguity_returns_specific_choices(self):
        with fixture(SourceSelectionTests) as case:
            first = enrich_candidate(case.evidence)
            other = enrich_candidate(dict(case.evidence, id="other", title="Operating movements"))
            case.numeric.structured.side_effect = None
            case.numeric.structured.return_value = types.SimpleNamespace(ambiguous=True, evidence_ids=[first["id"], "other"])
            calc, comp, reason, used = case.numeric.verify_numeric(case.reported_period_claim(), [first, other])
            self.assertIsNone(comp)
            self.assertEqual(len(reason.options), 2)
            self.assertTrue(any("Operating movements" in x for x in reason.options))

    def test_unknown_meaning_source_fails_instead_of_becoming_clarification(self):
        with fixture(SourceSelectionTests) as case:
            first = enrich_candidate(case.evidence)
            other = enrich_candidate(dict(case.evidence, id="other", title="Operating movements"))
            case.numeric.structured.side_effect = None
            case.numeric.structured.return_value = types.SimpleNamespace(ambiguous=False, evidence_ids=["invented"])
            with self.assertRaises(case.numeric.VerificationFailure):
                case.numeric.verify_numeric(case.reported_period_claim(), [first, other])


if __name__ == "__main__":
    unittest.main()
