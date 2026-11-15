"""Desired-behaviour regressions from reported failures; intentionally expose open bugs.

Model responses are controlled. No provider calls or document uploads.
"""
import unittest
import test_claim_parser as parser_fixture
from test_claim_pipeline import fixture, DATES


class ReportedParserFailures(unittest.TestCase):
    def test_northstar_neutral_change_reaches_verification_or_clarification(self):
        for value in ('18%', '80%'):
            text = f'For Northstar, did revenue change by {value} over the reported periods?'
            with self.subTest(value=value), fixture(parser_fixture.ClaimParserTests) as case:
                atom = case.growth(claim_text=text, company='Northstar', subject='revenue', metric='revenue',
                                  claimed_literal=value, period_quote='reported periods', start_period=None,
                                  end_period=None, direction='unspecified', claim_type='question')
                case.respond([atom], [atom])
                parsed = case.parser.parse_claims(text)[0]
                self.assertNotIn('parsing_error', parsed, parsed)
                self.assertEqual(parsed['claim_type'], 'assertion')
                self.assertIsNone(parsed['start_period'])
                self.assertIsNone(parsed['end_period'])
                self.assertEqual(parsed['direction'], 'unspecified')

    def test_leading_dates_do_not_hide_yes_no_question(self):
        text = 'Between 2025 and 2026, did Northstar revenue increase by 18%?'
        with fixture(parser_fixture.ClaimParserTests) as case:
            atom = case.growth(claim_text=text, company='Northstar', metric='revenue', subject='revenue',
                              claimed_literal='18%', period_quote='2025 and 2026', claim_type='question')
            case.respond([atom], [atom])
            parsed = case.parser.parse_claims(text)[0]
            self.assertNotIn('parsing_error', parsed, parsed)
            self.assertEqual(parsed['claim_type'], 'assertion')

    def test_not_more_than_is_threshold_not_proposition_negation(self):
        text = parser_fixture.C.replace('grew 8.8%', 'grew not more than 8.8%')
        with fixture(parser_fixture.ClaimParserTests) as case:
            atom = case.growth(claim_text=text, negated=False)
            case.respond([atom], [atom])
            parsed = case.parser.parse_claims(text)[0]
            self.assertNotIn('parsing_error', parsed, parsed)
            self.assertEqual(parsed['comparison_operator'], 'at_most')
            self.assertFalse(parsed['negated'])

    def test_reported_assets_compound_repairs_misclassified_share_clause(self):
        original = (f'Between {DATES}, Apple’s total assets grew about 6.7%, while current assets grew about 1.3%, '
                    'and current assets’ share of total assets fell from about 41.2% to 39.1%.')
        with fixture(parser_fixture.ClaimParserTests) as case:
            atoms = [case.growth(claim_text=f'Between {DATES}, Apple’s {metric} grew about {value}.',
                     subject=metric, metric=metric, claimed_literal=value, period_quote=DATES)
                     for metric, value in (('total assets','6.7%'),('current assets','1.3%'))]
            atoms.append(case.atom('current assets’ share of total assets fell from about 41.2% to 39.1%.',
                metric='current assets',subject='current assets',denominator_metric='total assets',
                operation='level',unit='percent',claimed_literal='41.2%',start_period=2025,end_period=2026,
                period_quote=DATES))
            case.respond(atoms, atoms)
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not p.get('parsing_error') for p in parsed), parsed)
            ratios = [p for p in parsed if p['operation'] == 'ratio']
            self.assertEqual([(p['claimed_literal'],p['end_period']) for p in ratios], [('41.2%',2025),('39.1%',2026)])

    def test_reported_open_question_remains_distinct_from_yes_no_claim(self):
        text = 'For Apple, how much higher were inventories in June 2026 than in September 2025, percentage-wise?'
        with fixture(parser_fixture.ClaimParserTests) as case:
            atom = case.growth(claim_text=text,metric='inventories',subject='inventories',
                              claimed_literal=None,period_quote=None,claim_type='question')
            case.respond([atom], [atom])
            parsed = case.parser.parse_claims(text)[0]
            self.assertNotIn('parsing_error', parsed, parsed)
            self.assertEqual(parsed['claim_type'], 'question')
            self.assertIsNone(parsed['claimed_literal'])


if __name__ == '__main__':
    unittest.main()
