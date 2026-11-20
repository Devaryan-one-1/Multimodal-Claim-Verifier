"""Offline source-grounding tests and an optional live check on synthetic text."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from agents import text
from verification.grounded_numeric import Operand, Selection, verify_numeric


SENTENCE = "Customer count rose from 80 in 2025 to 120 in 2026."
CLAIM = dict(claim_text="Customer count grew 25% from 2025 to 2026.", metric="customer count",
             operation="growth", unit="percent", claimed_literal="25%", start_period=2025, end_period=2026)


def passage(content=SENTENCE, **updates):
    return dict({"id": "doc::p", "document_id": "doc", "type": "text", "page": 2, "content": content}, **updates)


def selection(old=0, new=2):
    return Selection(applicable=True, reason="Both reported counts are in the passage.",
                     operands=[Operand(name=k, evidence_id="doc::p", number_index=i)
                               for k,i in (("old", old), ("new", new))])


def qualitative(quote, evidence_id="doc::p"):
    return text.GroundedResult(verdict="SUPPORTED", confidence=0.9, reason="The passage reports the event.",
                               evidence_ids=[evidence_id], quotes=[text.EvidenceQuote(evidence_id=evidence_id, quote=quote)])


class TextAgentTests(unittest.TestCase):
    def test_growth_uses_exact_quote_and_original_indexes(self):
        with patch.object(text, "structured", return_value=selection()) as model:
            calc, comparison, reason, ids = verify_numeric(CLAIM, [passage(type="footnote")], use_text_agent=True)
        self.assertIsNotNone(calc, reason)
        self.assertEqual((calc["evidence_agent"], comparison["actual"], comparison["verdict"]), ("text", 50, "CONTRADICTED"))
        self.assertEqual(ids, ["doc::p"])
        self.assertEqual([calc["source_inputs"][k]["number_index"] for k in ("old", "new")], [0, 2])
        self.assertTrue(all(s["scope_quote"] == SENTENCE for s in calc["source_inputs"].values()))
        self.assertEqual(model.call_args.kwargs["model"], text.TEXT_MODEL)
        self.assertEqual(model.call_args.kwargs["purpose"], "text_verification")
        self.assertEqual([n["index"] for n in json.loads(model.call_args.args[1])["evidence"][0]["numbers"]], [0, 2])

    def test_forecast_alone_cannot_establish_historical_growth(self):
        with patch.object(text, "structured") as model:
            result = text.select_text_operands(CLAIM, [passage("Management expects customer count to grow 25% in 2026.")])
        self.assertFalse(result.applicable)
        model.assert_not_called()

    def test_corrects_date_used_as_a_value_and_keeps_ocr_citation(self):
        with patch.object(text, "structured", side_effect=[selection(old=1), selection()]) as model:
            result = text.select_text_operands(CLAIM, [passage(type="ocr")])
        self.assertTrue(result.applicable)
        self.assertIn("validation_error", json.loads(model.call_args.args[1]))

    def test_repeated_invented_address_is_an_operational_failure(self):
        with patch.object(text, "structured", return_value=selection(new=9)) as model:
            with self.assertRaises(text.VerificationFailure) as error:
                text.select_text_operands(CLAIM, [passage()])
        self.assertEqual(error.exception.stage, "text_verification")
        self.assertEqual(model.call_count, 2)

    def test_neighboring_metric_cannot_supply_the_claimed_amount(self):
        item = passage("Revenue was $80 million in 2026. Profit was $25 million in 2026.")
        claim = dict(metric="revenue", operation="level", end_period=2026, claimed_literal="$25 million",
                     unit="amount")
        def choice(index):
            return Selection(applicable=True, reason="Value in the sentence.",
                             operands=[Operand(name="value", evidence_id=item["id"], number_index=index)])
        with patch.object(text, "structured", side_effect=[choice(2), choice(0)]) as model:
            calc, comparison, reason, _ = verify_numeric(claim, [item], use_text_agent=True)
        self.assertIsNotNone(calc, reason)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(comparison["verdict"], "CONTRADICTED")
        self.assertEqual(calc["source_inputs"]["value"]["scope_quote"], "Revenue was $80 million in 2026.")

    def test_qualitative_quote_must_come_from_its_own_passage(self):
        actual = "The company opened its second office."
        evidence = [passage(actual), passage("The board approved the lease.", id="doc::q")]
        with patch.object(text, "structured", side_effect=[qualitative(actual, "doc::q"), qualitative(actual)]) as model:
            result = text.review_text_claim({"claim_text": "The company opened another office."}, evidence)
        self.assertEqual((result.verdict, result.quotes[0].quote), ("SUPPORTED", actual))
        self.assertEqual(model.call_count, 2)

    def test_short_qualitative_quote_cannot_hide_forecast_language(self):
        with patch.object(text, "structured", return_value=qualitative("open its second office")):
            result = text.review_text_claim({"claim_text": "The company opened another office."},
                                            [passage("The company expects to open its second office.")])
        self.assertEqual(result.verdict, "UNKNOWN")

    def test_unrelated_modalities_and_empty_text_need_no_model(self):
        with patch.object(text, "structured") as model:
            evidence = [passage(type="chart"), passage(content=" ")]
            self.assertFalse(text.select_text_operands(CLAIM, evidence).applicable)
            self.assertEqual(text.review_text_claim(CLAIM, evidence).verdict, "UNKNOWN")
        model.assert_not_called()


@unittest.skipUnless(os.getenv("TEXT_AGENT_LIVE") == "1", "opt-in live OpenRouter test")
class TextAgentLiveTests(unittest.TestCase):
    def test_real_model_selects_counterevidence_from_synthetic_prose(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}):
            calc, comparison, reason, _ = verify_numeric(CLAIM, [passage()], use_text_agent=True)
        self.assertIsNotNone(calc, reason)
        self.assertEqual((comparison["actual"], comparison["verdict"]), (50, "CONTRADICTED"))
        self.assertEqual(calc["source_inputs"]["new"]["scope_quote"], SENTENCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
