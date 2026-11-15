import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from ui.claim_interpretation import interpretation_lines, render_claim_interpretation


class InterpretationTests(unittest.TestCase):
    def test_shows_original_metric_search_terminology_operation_and_value(self):
        result = {'parsed_claim': dict(original_input='Original wording', claim_text='Atomic wording',
                  metric='money owed by customers', interpretation={'metric':'accounts receivable'},
                  claimed_literal='$12 million', operation='level', end_period=2026, period_quote='June 27, 2026')}
        lines = interpretation_lines(result)
        for value in ('Original wording', 'Atomic wording', 'money owed by customers',
                      'accounts receivable', '$12 million', 'Reported value', 'June 27, 2026'):
            self.assertIn(value, '\n'.join(lines))

    def test_final_source_periods_override_unresolved_parser_snapshot(self):
        result = {'parsed_claim': {'metric':'inventory','interpretation':{'period_resolution':'source_required'}},
                  'calculation': {'inferred_periods': {'old':{'raw':'September 27, 2025'},'new':{'raw':'June 27, 2026'}}}}
        lines = '\n'.join(interpretation_lines(result))
        self.assertIn('September 27, 2025 to June 27, 2026 (resolved from source)', lines)
        self.assertNotIn('Awaiting', lines)

    def test_unresolved_periods_do_not_invent_dates(self):
        lines = interpretation_lines({'parsed_claim':{'metric':'revenue','period_quote':'reported periods'}})
        self.assertIn('Periods: Awaiting source resolution or clarification', lines)

    def test_missing_and_invalid_snapshots_show_unavailable(self):
        for result in ({}, {'parsed_claim':{'parsing_error':'failure'}}):
            st = MagicMock()
            render_claim_interpretation(st, result)
            st.text.assert_not_called()
            st.caption.assert_called_once_with('No validated interpretation is available for this saved result.')

    def test_literal_text_rendering_and_collapsed_expander(self):
        st = MagicMock()
        render_claim_interpretation(st, {'parsed_claim':{'claim_text':'<script>sample</script>', 'metric':'sales'}})
        st.expander.assert_called_once_with('How your claim was understood', expanded=False)
        st.text.assert_any_call('This claim: <script>sample</script>')
        st.markdown.assert_not_called()


if __name__ == '__main__':
    unittest.main()
