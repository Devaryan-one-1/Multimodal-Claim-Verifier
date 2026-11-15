"""Exact user-reported question with realistic parser response variations."""
import unittest
import test_claim_parser as fixtures
import test_source_selection as source_fixtures
from test_claim_pipeline import fixture

TEXT = 'Did Apple’s inventories increase by about 94% from September 27, 2025 to June 27, 2026?'


class YesNoRegressionTests(unittest.TestCase):
    def atom(self, case, text=TEXT, **extra):
        return case.growth(claim_text=text, metric='inventories', subject='inventories',
                           claimed_literal=extra.pop('claimed_literal', '94%'),
                           period_quote='September 27, 2025 to June 27, 2026', claim_type='question', **extra)

    def test_retained_and_omitted_question_prefix_reach_verification(self):
        for text in (TEXT, TEXT.removeprefix('Did ').rstrip('?')+'.'):
            with self.subTest(text=text), fixture(fixtures.ClaimParserTests) as case:
                atom = self.atom(case, text)
                case.respond([atom], [atom])
                parsed = case.parser.parse_claims(TEXT)[0]
                self.assertNotIn('parsing_error', parsed, parsed)
                self.assertEqual(parsed['claim_type'], 'assertion')
                self.assertEqual(parsed['claimed_literal'], '94%')
                self.assertEqual((parsed['start_period'], parsed['end_period']), (2025,2026))

    def test_missing_quantity_gets_correction_instead_of_completed_unknown(self):
        with fixture(fixtures.ClaimParserTests) as case:
            case.respond([self.atom(case, claimed_literal=None)], [self.atom(case)])
            parsed = case.parser.parse_claims(TEXT)[0]
            self.assertNotIn('parsing_error', parsed, parsed)
            self.assertEqual(parsed['claim_type'], 'assertion')
            self.assertEqual(case.gateway.structured.call_count, 2)

    def test_reported_percentage_point_misclassification_is_relative_growth(self):
        with fixture(fixtures.ClaimParserTests) as case, fixture(source_fixtures.SourceSelectionTests) as source:
            atom = self.atom(case, TEXT.removeprefix('Did ').rstrip('?'),
                             operation='percentage_points', unit='percentage_points')
            case.respond([atom], [atom])
            pc = case.parser.parse_claims(TEXT)[0]
            self.assertNotIn('parsing_error', pc, pc)
            self.assertEqual(pc['claim_type'], 'assertion')
            self.assertEqual(pc['operation'], 'growth')
            self.assertEqual(pc['unit'], 'percent')
            self.assertEqual(pc['interpretation']['operation'], 'growth')
            calc, comparison, reason, used = source.numeric.verify_numeric(pc, [source.evidence])
            self.assertIsNotNone(calc, reason)
            self.assertEqual(comparison['verdict'], 'SUPPORTED')

    def test_explicit_percentage_points_remain_percentage_points(self):
        text = "Did Cedar's profit margin increase by 5 percentage points between 2025 and 2026?"
        with fixture(fixtures.ClaimParserTests) as case:
            atom = case.growth(claim_text=text, company='Cedar', metric='profit margin',
                               subject='profit margin', claimed_literal='5',
                               operation='percentage_points', unit='percentage_points',
                               period_quote='between 2025 and 2026', claim_type='question')
            case.respond([atom], [atom])
            pc = case.parser.parse_claims(text)[0]
            self.assertNotIn('parsing_error', pc, pc)
            self.assertEqual(pc['operation'], 'percentage_points')
            self.assertEqual(pc['unit'], 'percentage_points')

    def test_yes_no_claim_reaches_cited_calculation_for_true_and_false_percentages(self):
        for value, verdict in (('94%', 'SUPPORTED'), ('10%', 'CONTRADICTED')):
            text = TEXT.replace('94%', value)
            with self.subTest(value=value), fixture(fixtures.ClaimParserTests) as case, fixture(source_fixtures.SourceSelectionTests) as source:
                atom = self.atom(case, text, claimed_literal=value)
                case.respond([atom], [atom])
                pc = case.parser.parse_claims(text)[0]
                self.assertNotIn('parsing_error', pc, pc)
                calc, comparison, reason, used = source.numeric.verify_numeric(pc, [source.evidence])
                self.assertIsNotNone(calc, reason)
                self.assertEqual(comparison['verdict'], verdict)
                self.assertEqual(calc['inputs'], {'old':5718e6, 'new':11092e6})
                self.assertEqual(used, [source.evidence['id']])

    def test_open_question_cannot_be_promoted_by_numeric_literal(self):
        text = 'How much did Apple’s inventories increase from September 27, 2025 to June 27, 2026?'
        with fixture(fixtures.ClaimParserTests) as case:
            atom = self.atom(case, text, claimed_literal=None)
            case.respond([atom], [atom])
            parsed = case.parser.parse_claims(text)[0]
            self.assertEqual(parsed['claim_type'], 'question', parsed)


if __name__ == '__main__':
    unittest.main()
