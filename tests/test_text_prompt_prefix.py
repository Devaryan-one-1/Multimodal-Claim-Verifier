"""Measure shared characters in the Text Agent's user JSON, offline."""
import json
from os.path import commonprefix
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from agents import text
from verification.grounded_numeric import Operand, Selection


class TextPromptPrefixTests(unittest.TestCase):
    def compare_layouts(self, second_page):
        with pymupdf.open(ROOT / "tests/fixtures/worker_rollout/fern-quay.pdf") as pdf:
            passages = [
                dict(id=f"fern-quay-p{i}", document_id="fern-quay", type="text",
                     page=i, content=page.get_text().strip())
                for i, page in enumerate(pdf, 1)
            ]
        queries = ("What was the reported revenue?", "Does the figure cover the entire company?")
        reply = text.GroundedResult(verdict="UNKNOWN", confidence=0, reason="Offline fixture.")
        with patch.object(text, "structured", return_value=reply) as model:
            for query, page in zip(queries, (1, second_page)):
                text.review_text_claim({"claim_text": query}, [passages[page - 1]])
        self.assertEqual(model.call_count, 2)
        first, second = model.call_args_list
        self.assertEqual(first.kwargs["system"], second.kwargs["system"])
        claim_first, evidence_first = [], []
        for call in (first, second):
            original = call.args[1]
            payload = json.loads(original)
            self.assertEqual(list(payload), ["evidence", "claim"])
            baseline = json.dumps({"claim": payload["claim"], "evidence": payload["evidence"]},
                                  ensure_ascii=False)
            claim_first.append(baseline)
            evidence_first.append(original)
        before, after = commonprefix(claim_first), commonprefix(evidence_first)
        if __name__ == "__main__":
            print(f"\nPages 1 and {second_page}: claim first {len(before)}, "
                  f"evidence first {len(after)} shared characters.")
        return before, after

    def test_same_page_shares_entire_evidence_before_claim(self):
        before, after = self.compare_layouts(second_page=1)
        self.assertNotIn('"evidence"', before)
        self.assertIn('"content"', after)
        self.assertIn('"claim_text"', after)
        self.assertGreater(len(after), len(before))

    def test_different_pages_diverge_inside_first_evidence_id(self):
        _, after = self.compare_layouts(second_page=2)
        self.assertTrue(after.endswith('"id": "fern-quay-p'))
        self.assertNotIn('"content"', after)

    def test_numeric_retry_preserves_ranked_passages_and_original_addresses(self):
        ranked = [
            dict(id="z", type="text", document_id="report", page=2, content="Revenue was 173 million."),
            dict(id="a", type="text", document_id="report", page=1, content="Revenue was 180 million."),
        ]
        claim = dict(operation="level", metric="revenue", claim_text="Revenue was 173 million.")
        invalid = Selection(applicable=True, reason="Invalid address.", operands=[
            Operand(name="value", evidence_id="z", number_index=99)])
        valid = Selection(applicable=True, reason="Reported revenue.", operands=[
            Operand(name="value", evidence_id="z", number_index=0)])
        with patch.object(text, "structured", side_effect=[invalid, valid]) as model:
            result = text.select_text_operands(claim, ranked)
        first, retry = model.call_args_list
        initial = json.loads(first.args[1])
        corrected = json.loads(retry.args[1])
        self.assertEqual(list(initial), ["evidence", "claim"])
        self.assertEqual(list(corrected), ["evidence", "claim", "validation_error"])
        self.assertTrue(corrected.pop("validation_error"))
        self.assertEqual(initial, corrected)
        self.assertEqual(initial["claim"], claim)
        self.assertEqual([item["id"] for item in initial["evidence"]], ["z", "a"])
        self.assertEqual(first.kwargs["system"], retry.kwargs["system"])
        operand, = result.operands
        self.assertEqual((operand.evidence_id, operand.number_index, operand.scope_quote),
                         ("z", 0, ranked[0]["content"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
