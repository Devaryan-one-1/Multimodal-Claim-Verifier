"""Production interpretation adapter and real source arithmetic; no paid/API calls."""
import ast
import copy
import json
import re
import time
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

import test_claim_parser as parser_fixture
import test_source_selection as source_fixture
from services.verification_errors import ParseValidationError
from services.verification_errors import VerificationFailure, quota_pause
from verification.evidence_context import enrich_candidate

ROOT = Path(__file__).resolve().parents[1]


class EvidenceInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixture.SourceSelectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.numeric = self.fixture.numeric
        self.fixture.stack.enter_context(patch.dict('sys.modules', {'verification.grounded_numeric': self.numeric}))
        self.module = parser_fixture.load_module(self.fixture.stack, 'verification.evidence_interpreter',
                                                 ROOT / 'app/verification/evidence_interpreter.py')
        self.evidence = self.fixture.table()
        self.evidence.update(company='Apple', title='Balance sheet',
                             rows=[['Inventories', '970', '500']], row_units=['$ millions'])
        self.record = self.module.candidate(self.evidence)
        self.records = {self.evidence['id']: self.record}
        self.old = next(k for k in self.record['periods'] if '2025' in k)
        self.new = next(k for k in self.record['periods'] if '2026' in k)
        self.text = 'Did Apple inventories increase by about 94% over the reported periods?'

    def atom(self, **updates):
        value = dict(metric='inventories', denominator_metric=None, literal='94%', operation='growth',
                     unit='percent', start=self.old, end=self.new, sources=[self.evidence['id']],
                     direction='up', comparison='equal', negated=False, approximate=True)
        return dict(value, **updates)

    def response(self, *atoms):
        return dict(status='ready', atoms=list(atoms or [self.atom()]), clarification=None, options=[])

    def adapt(self, response=None, text=None):
        return self.module.adapt_response(text or self.text, response or self.response(), self.records)

    def test_yes_no_and_reported_pair_reach_real_python_calculation(self):
        pc = self.adapt()['claims'][0]
        calc, comparison, reason, used = self.numeric.verify_numeric(pc, [self.evidence])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(comparison['verdict'], 'SUPPORTED')
        self.assertEqual(comparison['actual'], 94)
        self.assertEqual(used, [self.evidence['id']])
        self.numeric.structured.assert_not_called()

    def test_wrong_claim_is_preserved_and_contradicted(self):
        pc = self.adapt(self.response(self.atom(literal='18%')), self.text.replace('94%', '18%'))['claims'][0]
        _, comparison, reason, _ = self.numeric.verify_numeric(pc, [self.evidence])
        self.assertIsNotNone(comparison, reason)
        self.assertEqual(comparison['claimed'], 18)
        self.assertEqual(comparison['verdict'], 'CONTRADICTED')

    def test_half_is_preserved_and_normalized_only_at_calculation(self):
        self.evidence['rows'][0][1] = '750'
        pc = self.adapt(self.response(self.atom(literal='half', approximate=False)),
                        'Apple inventories went up by half over the reported periods.')['claims'][0]
        calc, comparison, reason, _ = self.numeric.verify_numeric(pc, [self.evidence])
        self.assertIsNotNone(calc, reason)
        self.assertEqual(pc['claimed_literal'], 'half')
        self.assertEqual(comparison['normalized_claimed_literal'], '50%')
        self.assertEqual(comparison['verdict'], 'SUPPORTED')

    def test_neutral_change_and_negation_keep_their_meaning(self):
        self.evidence['rows'][0][1] = '250'
        pc = self.adapt(self.response(self.atom(literal='50%', direction='either')),
                        'Apple inventories changed by 50% over the reported periods.')['claims'][0]
        _, comparison, reason, _ = self.numeric.verify_numeric(pc, [self.evidence])
        self.assertIsNotNone(comparison, reason)
        self.assertEqual(comparison['actual'], -50)
        self.assertEqual(comparison['verdict'], 'SUPPORTED')
        pc['negated'] = True
        _, comparison, _, _ = self.numeric.verify_numeric(pc, [self.evidence])
        self.assertEqual(comparison['verdict'], 'CONTRADICTED')

    def test_unknown_sources_and_periods_are_execution_errors(self):
        for atom in (self.atom(sources=['foreign::private']), self.atom(end='annual:2030')):
            with self.subTest(atom=atom), self.assertRaises(ParseValidationError):
                self.adapt(self.response(atom))

    def test_changed_literal_and_substring_quantity_are_rejected(self):
        for literal in ('50%', '4%'):
            with self.subTest(literal=literal), self.assertRaises(ParseValidationError):
                self.adapt(self.response(self.atom(literal=literal)))

    def test_omitted_compound_quantity_or_explicit_date_is_rejected(self):
        for text in (self.text + ' Revenue grew 12%.',
                     'At June 28, 2026, inventories had grown about 94% since September 27, 2025.'):
            with self.subTest(text=text), self.assertRaises(ParseValidationError):
                self.adapt(text=text)

    def test_cross_document_sources_are_rejected(self):
        foreign = copy.deepcopy(self.record)
        foreign.update(id='other::table', document_id='other')
        self.records[foreign['id']] = foreign
        with self.assertRaises(ParseValidationError):
            self.adapt(self.response(self.atom(sources=[self.evidence['id'], foreign['id']])))

    def test_clarification_lists_actual_source_period_choices(self):
        response = dict(status='clarification_required', atoms=[], clarification='Which reporting pair do you mean?',
                        options=[dict(sources=[self.evidence['id']], start=self.old, end=self.new, duration='point_in_time')])
        result = self.adapt(response)
        self.assertEqual(result['status'], 'clarification_required')
        self.assertIn('2025', result['options'][0])
        self.assertIn('2026', result['options'][0])
        self.assertEqual(result['claims'], [])

    def test_nonready_response_cannot_smuggle_claims(self):
        response = self.response()
        response['status'] = 'insufficient_evidence'
        with self.assertRaises(ParseValidationError):
            self.adapt(response)

    def test_period_duration_and_exact_dates_survive_adaptation(self):
        pc = self.adapt()['claims'][0]
        self.assertEqual(pc['period_type'], 'point_in_time')
        self.assertEqual(pc['inferred_periods']['old']['date'], '2025-09-27')
        self.assertEqual(pc['inferred_periods']['new']['date'], '2026-06-27')
        wrong = copy.deepcopy(self.evidence)
        wrong['column_map'][0]['raw'] = 'June 28, 2026'
        with self.assertRaises(ValueError):
            self.numeric._period_check(pc, 'new', wrong, 1, 3)

    def test_text_and_chart_candidates_keep_original_evidence(self):
        for source in (
            dict(id='doc::text', document_id='doc', type='text', content='Revenue in 2025 was $80 million; in 2026 it was $120 million.'),
            dict(id='doc::chart', document_id='doc', type='chart', title='Annual customers', x_axis={'label':'Year'},
                 facts=[{'category':'2025', 'value':80}, {'category':'2026', 'value':100}]),
        ):
            record = self.module.candidate(source)
            self.assertEqual(len(record['periods']), 2)
            self.assertEqual(record[source['type'] == 'text' and 'content' or 'facts'], source.get('content', source.get('facts')))

    def test_interpreter_calls_only_the_separate_role_with_tested_prompt(self):
        with patch.object(self.module, 'structured', return_value=types.SimpleNamespace(data=self.response())) as call:
            parsed = self.module.interpret_claims(self.text, [self.evidence])
        self.assertEqual(parsed['status'], 'ready')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['purpose'], 'claim_interpretation')
        self.assertEqual(call.call_args.kwargs['examples'], self.module.EXAMPLES)

    def test_production_prompt_contract_and_examples_match_the_experiment(self):
        checker = runpy.run_path(str(ROOT / 'scripts/check_claim_interpretation.py'))
        self.assertEqual(self.module.SYSTEM, checker['PROMPTS']['v3'])
        self.assertEqual(self.module.SCHEMA, checker['SCHEMA'])
        self.assertEqual(self.module.FIELD_GUIDE, checker['FIELD_GUIDE'])
        self.assertEqual(json.dumps(self.module.EXAMPLES), json.dumps(checker['EXAMPLES']))

    def test_all_39_expected_interpretations_fit_production_adapter(self):
        checker = runpy.run_path(str(ROOT / 'scripts/check_claim_interpretation.py'))
        for case in checker['CASES']:
            with self.subTest(case=case['id']):
                records = {}
                for item in case['evidence']:
                    periods = {}
                    for key in item.get('values', {}):
                        desc = self.module.period_descriptor(key, kind=item['duration'], document_id=item['company'])
                        if desc:
                            periods[key] = desc
                    records[item['id']] = dict(item, document_id=item['company'], periods=periods)
                response = checker['expected_output'](case)
                response['clarification'] = 'Which pair do you mean?' if response['status'] == 'clarification_required' else None
                adapted = self.module.adapt_response(case['claim'], response, records)
                self.assertEqual(adapted['status'], response['status'])

    def pipeline(self):
        tree = ast.parse((ROOT / 'app/pipelines/verification_pipeline.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {'prepare_verification', '_verify_one'}]
        self.document = {'document_id': self.evidence['document_id'], 'status': 'ready', 'company_name': 'Apple'}
        self.source = dict(self.evidence, id=self.evidence['source_evidence_id'])
        self.storage = MagicMock(side_effect=lambda path: self.document if path.endswith('document.json') else {'evidence': [self.source]})
        self.search = MagicMock(return_value=[dict(document_id=self.evidence['document_id'], source_evidence_id=self.source['id'])])
        gateway = sys.modules['services.model_gateway']
        gateway.config = lambda: types.SimpleNamespace(provider='openrouter')
        namespace = dict(json=json, re=re, time=time, copy=copy, capture_calls=parser_fixture.capture_calls,
                         VerificationFailure=VerificationFailure, quota_pause=quota_pause, enrich_candidate=enrich_candidate,
                         event=lambda *a, **k: None, read=self.storage, search=self.search,
                         rerank=lambda query, hits, **kw: hits, verify_numeric=self.numeric.verify_numeric,
                         fuse_evidence=lambda *a, **k: {}, parse_claims=MagicMock(side_effect=AssertionError('Legacy parser called')))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<production pipeline>', 'exec'), namespace)
        return namespace

    def test_full_preparation_and_calculation_reuse_selected_sources(self):
        ns = self.pipeline()
        with patch.object(self.module, 'structured', return_value=types.SimpleNamespace(data=self.response())) as model:
            parsed, prepared = ns['prepare_verification'](self.text, [self.document['document_id']])
        self.assertNotIn('parsing_error', parsed[0])
        self.search.assert_called_once()
        result = ns['_verify_one'](parsed[0], [self.document['document_id']], prepared=prepared)
        self.assertEqual(result['verdict'], 'SUPPORTED', result)
        self.assertEqual(result['evidence_ids'], [self.evidence['id']])
        self.search.assert_called_once()  # No second retrieval or parser request.
        model.assert_called_once()

    def test_provider_failure_and_bad_response_do_not_become_factual_verdicts(self):
        ns = self.pipeline()
        for error in (RuntimeError('HTTP 429 rate limit'), ParseValidationError('Invalid source', reason='invalid_structure')):
            with self.subTest(error=error), patch.object(self.module, 'structured', side_effect=error):
                parsed, prepared = ns['prepare_verification'](self.text, [self.document['document_id']])
                self.assertIn('parsing_error', parsed[0])
                self.assertIsNone(prepared)
                result = ns['_verify_one'](parsed[0], [self.document['document_id']])
                self.assertEqual(result['status'], 'error')

    def test_clarification_exits_before_calculation(self):
        ns = self.pipeline()
        pc = dict(claim_text=self.text, interpretation_status='clarification_required',
                  clarification={'question': 'Annual or quarterly?', 'options': ['Annual 2025 to 2026', 'Q1 2025 to Q1 2026']})
        ns['verify_numeric'] = MagicMock(side_effect=AssertionError('Clarification is not a factual verdict'))
        result = ns['_verify_one'](pc, [self.document['document_id']])
        self.assertEqual(result['status'], 'clarification_required')
        self.assertEqual(result['verdict'], '')
        ns['verify_numeric'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
