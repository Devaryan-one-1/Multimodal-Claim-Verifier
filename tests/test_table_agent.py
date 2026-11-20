"""Table specialist tests using source-reviewed fixtures and an offline gateway."""
import copy
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

import test_financial_integrity as financial
import test_source_selection as fixtures


class TableAgentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SourceSelectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        stack = self.fixture.stack
        stack.enter_context(patch.dict(sys.modules, {"verification.grounded_numeric": self.fixture.numeric}))
        self.agent = financial.load_module(stack, "_table_agent_test", financial.APP / "agents/table.py")
        self.agent.structured = MagicMock(side_effect=AssertionError("Unexpected model call"))

    def test_wrong_amount_and_repeated_year_values_select_correct_column(self):
        claim = dict(self.fixture.pc, claimed_literal="$147,957 million")
        table = self.fixture.evidence
        for previous_year in ("147,957", "149,818"):
            table["rows"][1][2] = previous_year
            before = copy.deepcopy(table)
            result = self.agent.select_table_operands(claim, [table])
            cell, = result.operands
            self.assertEqual((cell.evidence_id, cell.row_index, cell.column_index), (table["id"], 1, 1))
            self.assertEqual(table, before)
        self.agent.structured.assert_not_called()

    def test_blank_cell_rejects_neighbor_and_accepts_missing_evidence(self):
        table = self.fixture.evidence
        table["rows"][1][1] = ""
        other = self.fixture.table("other-document")
        claim = dict(self.fixture.pc, meaning_source_ids=[table["id"]])
        self.agent.structured.side_effect = [
            self.fixture.selection(self.fixture.operand(column_index=2)),
            self.fixture.selection(applicable=False, reason="The requested cell is blank."),
        ]
        result = self.agent.select_table_operands(claim, [table, other, {"type": "chart"}])
        self.assertFalse(result.applicable)
        self.assertEqual(result.operands, [])
        first, second = self.agent.structured.call_args_list
        self.assertEqual(first.kwargs["model"], "baidu/ernie-5.5-flash-preview:free")
        self.assertEqual(first.kwargs["purpose"], "table_selection")
        self.assertEqual([t["id"] for t in json.loads(first.args[1])["tables"]], [table["id"]])
        self.assertIn("validation_error", json.loads(second.args[1]))

    def test_invalid_source_after_correction_is_execution_failure(self):
        self.fixture.evidence["rows"][1][1] = ""
        invalid = self.fixture.selection(self.fixture.operand(evidence_id="invented"))
        self.agent.structured.side_effect = [invalid, invalid]
        with self.assertRaises(self.agent.VerificationFailure) as error:
            self.agent.select_table_operands(self.fixture.pc, [self.fixture.evidence])
        self.assertEqual(error.exception.stage, "table_selection")

    def test_model_can_choose_a_complete_growth_pair_from_candidate_tables(self):
        table = self.fixture.evidence
        second = dict(table, id="another-table", title="Segment disclosure")
        claim = dict(self.fixture.pc, operation="growth", start_period=2025,
                     end_period=2026, period_quote="", period_type="unspecified")
        selected = self.fixture.selection(self.fixture.operand(name="old", column_index=2),
                                          self.fixture.operand(name="new", column_index=1))
        self.agent.structured.side_effect = [selected]
        result = self.agent.select_table_operands(claim, [table, second])
        self.assertEqual([(cell.name, cell.column_index) for cell in result.operands],
                         [("old", 2), ("new", 1)])
        self.agent.structured.assert_called_once()
