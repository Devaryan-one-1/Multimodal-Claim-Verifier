"""Compound-claim and failure regressions; real normalization/math, isolated I/O."""
from contextlib import contextmanager
import ast
import copy
import json
from pathlib import Path
import re
import time
import types
import unittest
from unittest.mock import patch

import test_claim_parser as parser_fixture
import test_source_selection as source_fixture
import test_processing_diagnostics as diagnostics_fixture
from services.failure_details import describe_failure
from services.verification_errors import VerificationFailure
from services.verification_status import execution_failed
from pilot.benchmark import score

PROJECT=Path(__file__).resolve().parents[1]
DATES="September 27, 2025 and June 27, 2026"
INVENTORY=(f"Between {DATES}, Apple's total inventories increased approximately 94.0%, "
           "from $5.718 billion to $11.092 billion, as a $5.721 billion increase in components "
           "more than offset a $347 million decrease in finished goods.")
BALANCE=(f"Between {DATES}, Apple's total assets grew approximately 6.7%, while current assets grew only 1.3%. "
         "Consequently, current assets' share of total assets fell from approximately 41.2% to 39.1%.")


@contextmanager
def fixture(kind):
    case=kind(); case.setUp()
    try: yield case
    finally: case.doCleanups()


def active_pipeline(pc,calculate,*,fail_stage=None):
    """Run the actual pipeline function, replacing only storage/search boundaries."""
    tree=ast.parse((PROJECT/'app/pipelines/verification_pipeline.py').read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_verify_one')
    @contextmanager
    def calls(): yield [],0
    def boundary(stage,value):
        if fail_stage==stage: raise RuntimeError('PRIVATE_RUNTIME_DETAIL')
        return value
    namespace={'capture_calls':calls,'VerificationFailure':VerificationFailure,'time':time,'re':re,
        'event':lambda *a,**k:None,
        '_documents':lambda *a:boundary('document',[{'document_id':'fixture-document','filename':'Fixture'}]),
        'search':lambda *a,**k:boundary('retrieval',[{'document_id':'fixture-document','source_evidence_id':'source'}]),
        'rerank':lambda q,h,**k:boundary('reranking',h),
        'read':lambda p:boundary('source_resolution',{'evidence':[{'id':'source','type':'text','page':9,'content':'Fixture'}]}),
        'verify_numeric':calculate,'fuse_evidence':lambda *a,**k:boundary('evidence_fusion',{'requires_unknown':False}),
        'quota_pause':lambda *a:None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(PROJECT/'app/pipelines/verification_pipeline.py'),'exec'),namespace)
    return namespace['_verify_one'](pc,['fixture-document'])


class CompoundClaimTests(unittest.TestCase):
    def test_inventory_claim_keeps_all_five_quantities_and_calculates_from_table(self):
        with fixture(parser_fixture.ClaimParserTests) as parser, fixture(source_fixture.SourceSelectionTests) as source:
            def atom(text,metric,literal,**kw):
                return parser.atom(text,subject=metric,metric=metric,claimed_literal=literal,**kw)
            comparative=dict(start_period=2025,end_period=2026,period_quote=DATES,period_type='unspecified')
            atoms=[atom(f"Between {DATES}, Apple's total inventories increased approximately 94.0%.",
                        'total inventories','94.0%',operation='growth',unit='percent',direction='up',**comparative),
                   atom("September 27, 2025, Apple's total inventories were $5.718 billion.",
                        'total inventories','$5.718 billion',end_period=2025,period_quote='September 27, 2025'),
                   atom("June 27, 2026, Apple's total inventories were $11.092 billion.",
                        'total inventories','$11.092 billion'),
                   atom(f"Between {DATES}, Apple's $5.721 billion increase in components.",
                        'components','$5.721 billion',operation='absolute_change',direction='up',**comparative),
                   atom(f"Between {DATES}, Apple's $347 million decrease in finished goods.",
                        'finished goods','$347 million',operation='absolute_change',direction='down',**comparative)]
            parser.respond(atoms)
            parsed=parser.parser.parse_claims(INVENTORY)
            self.assertTrue(all(not p.get('parsing_error') for p in parsed),parsed)
            self.assertEqual(len(parsed),5)
            table=source.evidence
            table.update(page=12,title='Inventories',context='Inventories',
                         rows=[['Components','7,845','2,124'],['Finished goods','3,247','3,594'],
                               ['Total inventories','11,092','5,718']],row_units=['$ millions']*3)
            for pc in parsed:
                calc,comparison,reason,_=source.numeric.verify_numeric(pc,[copy.deepcopy(table)])
                self.assertIsNotNone(calc,reason)
                self.assertEqual(comparison['verdict'],'SUPPORTED')
                self.assertTrue(all(a['page']==12 for a in calc['source_inputs'].values()))
            source.numeric.structured.assert_not_called()

    def test_balance_claim_keeps_growth_and_two_dated_asset_shares(self):
        with fixture(parser_fixture.ClaimParserTests) as parser, fixture(source_fixture.SourceSelectionTests) as source:
            atoms=[]
            for metric,literal in (('total assets','6.7%'),('current assets','1.3%')):
                text=f"Between {DATES}, Apple's {metric} grew approximately {literal}."
                atoms.append(parser.atom(text,subject=metric,metric=metric,operation='growth',
                    claimed_literal=literal,unit='percent',start_period=2025,period_quote=DATES,period_type='unspecified',direction='up'))
            for date,year,literal in (('September 27, 2025',2025,'41.2%'),('June 27, 2026',2026,'39.1%')):
                text=f"{date}, Apple's current assets' share of total assets was approximately {literal}."
                atoms.append(parser.atom(text,subject='current assets',metric='current assets',denominator_metric='total assets',
                    operation='ratio',claimed_literal=literal,unit='percent',end_period=year,period_quote=date))
            parser.respond(atoms)
            parsed=parser.parser.parse_claims(BALANCE)
            self.assertEqual(len(parsed),4,parsed)
            self.assertTrue(all(not p.get('parsing_error') for p in parsed),parsed)
            table=source.evidence
            table.update(rows=[['Total current assets','149,818','147,957'],['Total assets','383,266','359,241']],
                         row_units=['$ millions']*2)
            for pc in parsed:
                calc,comparison,reason,_=source.numeric.verify_numeric(pc,[copy.deepcopy(table)])
                self.assertIsNotNone(calc,reason)
                self.assertEqual(comparison['verdict'],'SUPPORTED')
                if pc['operation']=='ratio':
                    self.assertEqual(calc['formula'],'numerator / denominator * 100')
                    self.assertEqual(calc['source_inputs']['numerator']['row_index'],0)
                    self.assertEqual(calc['source_inputs']['denominator']['row_index'],1)
            source.numeric.structured.assert_not_called()

    def test_compound_dates_are_recovered_from_realistic_compact_model_shapes(self):
        original=BALANCE.replace("'", "\u2019")
        for shape in ('missing_shared_dates','combined_ratio','inherited_ratio_range','formatted_quote','year_quote','iso_atom_dates','null_years','omitted_growth_literal'):
            with self.subTest(shape=shape), fixture(parser_fixture.ClaimParserTests) as parser, fixture(source_fixture.SourceSelectionTests) as source:
                atoms=[]
                for metric,literal in (('total assets','6.7%'),('current assets','1.3%')):
                    text=f"Apple\u2019s {metric} grew approximately {literal}."
                    quote=None if shape in {'missing_shared_dates','null_years'} else DATES
                    if shape=='formatted_quote': quote='2025-09-27 to 2026-06-27'
                    if shape=='year_quote': quote='2025 to 2026'
                    if shape=='iso_atom_dates': text=f'Between 2025-09-27 and 2026-06-27, {text}'
                    if shape=='omitted_growth_literal' and metric=='current assets':
                        text=text.replace(literal,'')
                    atoms.append(parser.atom(text,subject=metric,metric=metric,operation='growth',claimed_literal=literal,
                        unit='percent',start_period=None if shape=='null_years' else 2025,
                        end_period=None if shape=='null_years' else 2026,period_quote=quote,period_type='unspecified',direction='up'))
                if shape=='combined_ratio':
                    text='Consequently, current assets\u2019 share of total assets fell from approximately 41.2% to 39.1%.'
                    atoms.append(parser.atom(text,subject='current assets',metric='current assets',denominator_metric='total assets',
                        operation='ratio',claimed_literal='41.2%',unit='percent',start_period=2025,end_period=2026,period_quote=DATES))
                else:
                    for literal in ('41.2%','39.1%'):
                        text=f'current assets\u2019 share of total assets was approximately {literal}.'
                        atoms.append(parser.atom(text,subject='current assets',metric='current assets',denominator_metric='total assets',
                            operation='ratio',claimed_literal=literal,unit='percent',start_period=2025,end_period=2026,period_quote=DATES))
                parser.respond(atoms,atoms)
                parsed=parser.parser.parse_claims(original)
                self.assertTrue(all(not p.get('parsing_error') for p in parsed),parsed)
                self.assertEqual(len(parsed),4)
                self.assertEqual(parser.gateway.structured.call_count,1)
                self.assertEqual([(p['end_period'],p['period_quote']) for p in parsed if p['operation']=='ratio'],
                                 [(2025,'September 27, 2025'),(2026,'June 27, 2026')])
                source.evidence.update(rows=[['Total current assets','149,818','147,957'],['Total assets','383,266','359,241']],
                                       row_units=['$ millions']*2)
                for pc in parsed:
                    calc,comparison,reason,_=source.numeric.verify_numeric(pc,[copy.deepcopy(source.evidence)])
                    self.assertIsNotNone(calc,reason)
                    self.assertEqual(comparison['verdict'],'SUPPORTED')

    def test_date_repairs_reject_invented_dates_and_period_qualifiers(self):
        for quote in ('2024-09-27 to 2026-06-27','annual 2025 to 2026','Q1 2025 to Q1 2026',
                      '2025-09-27 to 2026-06-28'):
            with self.subTest(quote=quote), fixture(parser_fixture.ClaimParserTests) as case:
                atom=case.atom('current assets\u2019 share of total assets was approximately 41.2%.',
                    subject='current assets',metric='current assets',denominator_metric='total assets',operation='ratio',
                    claimed_literal='41.2%',unit='percent',start_period=2025,end_period=2026,period_quote=quote)
                case.respond([atom],[atom])
                result=case.parser.parse_claims(BALANCE.replace("'", "\u2019"))[0]
                self.assertEqual(result['error_kind'],'ParseValidationError')
                self.assertEqual(result['parsing_context']['validation_rule'],'quote_not_grounded')
                self.assertNotIn('verdict',result)

    def test_ratio_dates_are_not_inferred_with_additional_or_reversed_periods(self):
        for original in (BALANCE+' In 2027, current assets changed.',
                         BALANCE.replace(DATES,'June 27, 2026 and September 27, 2025'),
                         BALANCE.replace('Consequently,','In the next year,'),
                         BALANCE.replace('Consequently,','For the fiscal year,')):
            with self.subTest(original=original), fixture(parser_fixture.ClaimParserTests) as case:
                atom=case.atom('current assets\u2019 share of total assets fell from approximately 41.2% to 39.1%.',
                    subject='current assets',metric='current assets',denominator_metric='total assets',operation='ratio',
                    claimed_literal='41.2%',unit='percent',start_period=2025,end_period=2026,period_quote=None)
                case.respond([atom],[atom])
                result=case.parser.parse_claims(original.replace("'", "\u2019"))[0]
                self.assertEqual(result['error_kind'],'ParseValidationError')
                self.assertNotIn('verdict',result)

    def test_local_ratio_split_preserves_missing_growth_quantity_failure(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            atom=case.atom(BALANCE,subject='current assets',metric='current assets',denominator_metric='total assets',operation='ratio',
                          claimed_literal='41.2%',unit='percent',start_period=2025,end_period=2026,period_quote=DATES)
            case.respond([atom],[atom])
            result=case.parser.parse_claims(BALANCE)[0]
            self.assertEqual(result['parsing_context']['validation_reason'],'coverage')

    def test_distinct_reporting_durations_are_valid_when_already_separate(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original=f"Between {DATES}, Apple's annual revenue grew 6.7%, while quarterly current assets grew only 1.3%."
            atoms=[]
            for metric,qualifier,literal in (('revenue','annual','6.7%'),('current assets','quarterly','1.3%')):
                atoms.append(case.atom(f"Between {DATES}, Apple's {qualifier} {metric} grew {literal}.",
                    subject=metric,metric=metric,operation='growth',claimed_literal=literal,unit='percent',
                    start_period=2025,end_period=2026,period_quote=DATES))
            case.respond(atoms)
            parsed=case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed),parsed)
            self.assertEqual([pc['period_type'] for pc in parsed],['annual','quarter'])

    def test_non_ratio_two_year_level_still_rejected_with_precise_safe_diagnostic(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            text="Between September 27, 2025 and June 27, 2026, Apple's revenue was $100 million."
            atom=case.atom(text,metric='revenue',subject='revenue',claimed_literal='$100 million',period_quote=DATES)
            case.respond([atom],[atom])
            result=case.parser.parse_claims(text)[0]
            context=result['parsing_context']
            self.assertEqual(context['validation_rule'],'multiple_periods')
            self.assertEqual(context['atom_index'],1)
            detail=describe_failure(kind=result['error_kind'],stage='claim_parser',message='PRIVATE_CLAIM',metadata=context)
            self.assertEqual((detail['validation_rule'],detail['atom_index']),('multiple_periods',1))
            self.assertNotIn('PRIVATE_CLAIM',json.dumps(detail))

    def test_omitted_component_clauses_are_rejected_even_when_years_retained(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            atom=case.atom(INVENTORY.split(', as a ')[0]+'.',subject='total inventories',metric='total inventories',
                operation='growth',claimed_literal='94.0%',unit='percent',start_period=2025,period_quote=DATES)
            case.respond([atom],[atom])
            result=case.parser.parse_claims(INVENTORY)[0]
            self.assertEqual(result['error_kind'],'ParseValidationError')
            self.assertEqual(result['parsing_context']['validation_reason'],'coverage')
            self.assertNotIn('verdict',result)

    def test_metric_amount_swap_is_rejected_and_one_correction_can_recover(self):
        original="In 2026, Apple's revenue was $100 million and profit $20 million."
        with fixture(parser_fixture.ClaimParserTests) as case:
            def atoms(swapped):
                return [case.atom(f"In 2026, Apple's {metric} was {literal}.",subject=metric,metric=metric,
                            claimed_literal=literal,period_quote='2026')
                        for metric,literal in [('profit' if swapped else 'revenue','$100 million'),
                                               ('revenue' if swapped else 'profit','$20 million')]]
            case.respond(atoms(True),atoms(True))
            result=case.parser.parse_claims(original)[0]
            self.assertEqual(result['parsing_context']['validation_reason'],'binding')
            case.respond(atoms(True),atoms(False))
            result=case.parser.parse_claims(original)
            self.assertEqual([p['metric'] for p in result],['revenue','profit'])
            self.assertTrue(all(not p.get('parsing_error') for p in result))

    def test_missing_amount_cannot_be_hidden_in_unclear_atom(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original="In 2026, Apple's revenue was $100 million and profit $20 million."
            good=case.atom("In 2026, Apple's revenue was $100 million.",metric='revenue',subject='revenue',
                           claimed_literal='$100 million',period_quote='2026')
            unclear=case.atom('profit $20 million',metric='profit',subject='profit',claimed_literal=None,
                              claim_type='unclear',operation='qualitative',period_quote=None)
            case.respond([good,unclear],[good,unclear])
            self.assertEqual(case.parser.parse_claims(original)[0]['error_kind'],'ParseValidationError')

    def test_ratio_cannot_invent_its_denominator(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            text="In 2026, Apple's current assets were 39.1%."
            atom=case.atom(text,subject='current assets',metric='current assets',operation='ratio',
                           denominator_metric='total assets',claimed_literal='39.1%',period_quote='2026')
            case.respond([atom],[atom])
            self.assertEqual(case.parser.parse_claims(text)[0]['parsing_context']['validation_reason'],'operation')


class OriginalAssertionTests(unittest.TestCase):
    def test_swapped_date_amounts_are_rejected_and_correction_checks_original_values(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = parser_fixture.A.replace('$14.9 billion', '$13.7 billion')+' '+parser_fixture.B
            swapped = [case.atom(), case.atom(parser_fixture.B.replace('$14.9 billion', '$13.7 billion'),
                       claimed_literal='$13.7 billion', end_period=2025, period_quote='September 27, 2025')]
            correct = [case.atom(original.split('. ')[0]+'.', claimed_literal='$13.7 billion'),
                       case.atom(parser_fixture.B, end_period=2025, period_quote='September 27, 2025')]
            case.respond(swapped, swapped)
            failure = case.parser.parse_claims(original)[0]
            self.assertEqual(failure.get('error_kind'), 'ParseValidationError', failure)
            self.assertNotIn('verdict', failure)
            case.respond(swapped, correct)
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)
            self.assertEqual([case.calculate(pc)[1]['verdict'] for pc in parsed], ['CONTRADICTED', 'CONTRADICTED'])

    def test_same_year_different_dates_cannot_exchange_amounts(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            second = parser_fixture.A.replace('June 27', 'March 28').replace('$14.9 billion', '$13.7 billion')
            original = parser_fixture.A+' '+second
            atoms = [case.atom(parser_fixture.A.replace('$14.9 billion', '$13.7 billion'), claimed_literal='$13.7 billion'),
                     case.atom(second.replace('$13.7 billion', '$14.9 billion'), period_quote='March 28, 2026')]
            case.respond(atoms, atoms)
            self.assertEqual(case.parser.parse_claims(original)[0].get('error_kind'), 'ParseValidationError')

    def test_inline_year_amount_pairs_preserve_shared_metric(self):
        for swapped in (False, True):
            with self.subTest(swapped=swapped), fixture(parser_fixture.ClaimParserTests) as case:
                original = "Apple's revenue was $100 million in 2025 and $120 million in 2026."
                atoms = [case.atom(f"Apple's revenue was {literal} in {year}.", metric='revenue', subject='revenue',
                         claimed_literal=literal, end_period=year, period_quote=str(year), period_type='annual')
                         for year, literal in zip((2025, 2026), ('$120 million', '$100 million') if swapped else ('$100 million', '$120 million'))]
                case.respond(atoms, atoms)
                parsed = case.parser.parse_claims(original)
                if swapped:
                    self.assertEqual(parsed[0].get('error_kind'), 'ParseValidationError', parsed)
                else:
                    self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)

    def test_direction_flags_follow_the_original_clause_and_actual_arithmetic(self):
        for verb, wrong, expected, verdict in (('declined', 'up', 'down', 'CONTRADICTED'),
                                              ('grew', 'down', 'up', 'SUPPORTED')):
            with self.subTest(verb=verb), fixture(parser_fixture.ClaimParserTests) as case:
                original = parser_fixture.C.replace('grew', verb)
                case.respond([case.growth(claim_text=original, direction=wrong)])
                parsed = case.parser.parse_claims(original)[0]
                self.assertNotIn('parsing_error', parsed, parsed)
                self.assertEqual(parsed['direction'], expected)
                self.assertEqual(case.calculate(parsed)[1]['verdict'], verdict)
                self.assertEqual(case.gateway.structured.call_count, 1)

    def test_signed_changes_and_direction_only_wording_are_retained(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            for wording, literal, operation, expected in (('fell', None, 'direction', 'down'),
                    ('rose', None, 'direction', 'up'), ('changed by -8.8%', '-8.8%', 'growth', 'down')):
                text = parser_fixture.C.replace('grew 8.8%', wording)
                atom = case.growth(claim_text=text, claimed_literal=literal, direction='unspecified', operation=operation)
                case.respond([atom], [atom])
                parsed = case.parser.parse_claims(text)[0]
                self.assertNotIn('parsing_error', parsed, parsed)
                self.assertEqual(parsed['direction'], expected)

    def test_conflicting_explicit_sign_or_ambiguous_direction_requires_correction(self):
        for wording, literal in (('grew -8.8%', '-8.8%'), ('declined +8.8%', '+8.8%'),
                                ('increased or decreased 8.8%', '8.8%')):
            with self.subTest(wording=wording), fixture(parser_fixture.ClaimParserTests) as case:
                original = parser_fixture.C.replace('grew 8.8%', wording)
                atom = case.growth(claim_text=original, claimed_literal=literal)
                case.respond([atom], [atom])
                parsed = case.parser.parse_claims(original)[0]
                self.assertEqual(parsed.get('error_kind'), 'ParseValidationError', parsed)
                self.assertNotIn('verdict', parsed)

    def test_repeated_amounts_do_not_cover_opposite_direction_clauses(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            prefix = "Between 2025 and 2026, Apple's revenue "
            original = prefix+'increased $10 million and revenue decreased $10 million.'
            def atom(verb, direction):
                return case.atom(prefix+verb+' $10 million.', subject='revenue', metric='revenue',
                    operation='absolute_change', claimed_literal='$10 million', start_period=2025,
                    end_period=2026, period_quote='Between 2025 and 2026', period_type='unspecified', direction=direction)
            increase, decrease = atom('increased', 'up'), atom('decreased', 'down')
            case.respond([increase], [increase])
            failure = case.parser.parse_claims(original)[0]
            self.assertEqual(failure.get('error_kind'), 'ParseValidationError', failure)
            self.assertEqual(failure['parsing_context']['validation_reason'], 'coverage')
            case.respond([increase], [increase, decrease])
            parsed = case.parser.parse_claims(original)
            self.assertEqual([pc.get('direction') for pc in parsed], ['up', 'down'], parsed)

    def test_genuinely_identical_restatement_is_not_a_missing_distinct_assertion(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            case.respond([case.atom()], [case.atom()])
            parsed = case.parser.parse_claims(parser_fixture.A+' '+parser_fixture.A)
            self.assertNotIn('parsing_error', parsed[0], parsed)

    def test_repeated_metric_values_with_different_qualifiers_need_both_targets(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "In 2026, Apple's annual revenue was $10 million and quarterly revenue was $10 million."
            annual = case.atom("In 2026, Apple's annual revenue was $10 million.", subject='revenue', metric='revenue',
                        claimed_literal='$10 million', period_quote='2026', period_type='annual')
            quarterly = case.atom("In 2026, Apple's quarterly revenue was $10 million.", subject='revenue', metric='revenue',
                        claimed_literal='$10 million', period_quote='2026', period_type='quarter')
            case.respond([annual], [annual])
            self.assertEqual(case.parser.parse_claims(original)[0].get('error_kind'), 'ParseValidationError')
            case.respond([annual, quarterly], [annual, quarterly])
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)

    def test_repeated_amounts_with_different_thresholds_need_distinct_targets(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            prefix = "Between 2025 and 2026, Apple's revenue "
            original = prefix+'increased more than $10 million and revenue increased less than $10 million.'
            atoms = [case.atom(prefix+f'increased {qualifier} $10 million.', subject='revenue', metric='revenue',
                     operation='absolute_change', claimed_literal='$10 million', start_period=2025, end_period=2026,
                     period_quote='Between 2025 and 2026', period_type='unspecified', direction='up')
                     for qualifier in ('more than', 'less than')]
            case.respond(atoms[:1], atoms[:1])
            self.assertEqual(case.parser.parse_claims(original)[0].get('error_kind'), 'ParseValidationError')
            case.respond(atoms, atoms)
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)
            self.assertEqual([pc['comparison_operator'] for pc in parsed], ['greater_than', 'less_than'])

    def test_explicit_from_to_values_and_respective_values_keep_their_dates(self):
        for form in ('from_to', 'respectively'):
            with self.subTest(form=form), fixture(parser_fixture.ClaimParserTests) as case:
                if form=='from_to':
                    original = f"Between {DATES}, Apple's total deferred revenue increased from $13.7 billion to $14.9 billion."
                    values = [('September 27, 2025', 2025, '$13.7 billion'), ('June 27, 2026', 2026, '$14.9 billion')]
                else:
                    original = "As of June 27, 2026 and September 27, 2025, Apple's total deferred revenue was $14.9 billion and $13.7 billion, respectively."
                    values = [('June 27, 2026', 2026, '$14.9 billion'), ('September 27, 2025', 2025, '$13.7 billion')]
                atoms = [case.atom(f"{date}, Apple's total deferred revenue was {literal}.", claimed_literal=literal,
                         end_period=year, period_quote=date) for date, year, literal in values]
                case.respond(atoms, atoms)
                parsed = case.parser.parse_claims(original)
                self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)
                self.assertEqual([case.calculate(pc)[1]['verdict'] for pc in parsed], ['SUPPORTED', 'SUPPORTED'])

    def test_terminal_shared_years_and_metric_names_with_and_are_preserved(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "Apple's research and development grew 8.8% while revenue fell 8.8% between 2025 and 2026."
            atoms = [case.growth(claim_text=f"Apple's {metric} {verb} 8.8% between 2025 and 2026.",
                     subject=metric, metric=metric, direction=direction, period_quote='2025 and 2026')
                     for metric, verb, direction in (('research and development', 'grew', 'up'), ('revenue', 'fell', 'down'))]
            case.respond(atoms, atoms)
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)

    def test_cagr_positive_rate_keeps_its_explicit_numeric_direction(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            text = "Apple's total deferred revenue CAGR was 8.8% between 2025 and 2026."
            atom = case.growth(claim_text=text, operation='cagr', direction='unspecified', period_quote='2025 and 2026')
            case.respond([atom], [atom])
            parsed = case.parser.parse_claims(text)[0]
            self.assertNotIn('parsing_error', parsed, parsed)
            self.assertEqual(parsed['direction'], 'up')

    def test_change_wording_cannot_escape_direction_checks_as_a_level(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "In 2026, Apple's total deferred revenue grew 8.8%."
            atom = case.growth(claim_text=original, operation='level', start_period=None,
                               period_quote='2026', period_type='annual')
            case.respond([atom], [atom])
            failure = case.parser.parse_claims(original)[0]
            self.assertEqual(failure.get('error_kind'), 'ParseValidationError', failure)
            self.assertEqual(failure['parsing_context']['validation_reason'], 'operation')
            corrected = case.growth(claim_text=original, start_period=None, period_quote='2026', period_type='annual')
            case.respond([atom], [corrected])
            self.assertEqual(case.parser.parse_claims(original)[0]['operation'], 'growth')

    def test_respective_values_cannot_be_assigned_to_each_others_dates(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "As of June 27, 2026 and September 27, 2025, Apple's total deferred revenue was $14.9 billion and $13.7 billion, respectively."
            atoms = [case.atom(parser_fixture.A.replace('$14.9 billion', '$13.7 billion'), claimed_literal='$13.7 billion'),
                     case.atom(parser_fixture.B, end_period=2025, period_quote='September 27, 2025')]
            case.respond(atoms, atoms)
            failure = case.parser.parse_claims(original)[0]
            self.assertEqual(failure.get('error_kind'), 'ParseValidationError', failure)

    def test_relative_year_clause_cannot_borrow_an_explicit_period_from_another_clause(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "In 2026, Apple's revenue was $100 million. Next year, profit was $20 million."
            atoms = [case.atom("In 2026, Apple's revenue was $100 million.", subject='revenue', metric='revenue',
                         claimed_literal='$100 million', period_quote='2026', period_type='annual'),
                     case.atom('Next year, profit was $20 million.', subject='profit', metric='profit',
                         claimed_literal='$20 million', period_quote='2026', period_type='annual')]
            case.respond(atoms, atoms)
            failure = case.parser.parse_claims(original)[0]
            self.assertEqual(failure.get('error_kind'), 'ParseValidationError', failure)

    def test_repeated_equal_changes_for_different_segments_need_both_targets(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "In 2026, Apple's European revenue grew 8.8% and US revenue grew 8.8%."
            atoms = [case.growth(claim_text=f"In 2026, Apple's {segment} revenue grew 8.8%.",
                         subject='revenue', metric='revenue', segment=segment,
                         start_period=None, period_quote='2026', period_type='annual') for segment in ('European', 'US')]
            case.respond(atoms[:1], atoms[:1])
            failure = case.parser.parse_claims(original)[0]
            self.assertEqual(failure.get('error_kind'), 'ParseValidationError', failure)
            case.respond(atoms, atoms)
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)
            self.assertEqual([pc['segment'] for pc in parsed], ['European', 'US'])

    def test_different_quarters_in_the_same_year_cannot_exchange_amounts(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "In Q1 2026, Apple's revenue was $100 million. In Q2 2026, Apple's revenue was $120 million."
            def atoms(swapped):
                return [case.atom(f"In Q{quarter} 2026, Apple's revenue was {literal}.", subject='revenue', metric='revenue',
                         claimed_literal=literal, period_quote=f'Q{quarter} 2026', period_type='quarter', quarter=quarter)
                         for quarter,literal in zip((1,2),('$120 million','$100 million') if swapped else ('$100 million','$120 million'))]
            case.respond(atoms(True), atoms(True))
            self.assertEqual(case.parser.parse_claims(original)[0].get('error_kind'), 'ParseValidationError')
            case.respond(atoms(False), atoms(False))
            parsed = case.parser.parse_claims(original)
            self.assertTrue(all(not pc.get('parsing_error') for pc in parsed), parsed)

    def test_explicit_quarter_recovers_a_missing_or_wrong_quarter_flag(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            original = "In the first fiscal quarter of 2026, Apple's revenue was $100 million."
            for quarter in (None, 2):
                atom = case.atom(original, subject='revenue', metric='revenue', claimed_literal='$100 million',
                                 period_quote='first fiscal quarter of 2026', period_type='quarter', quarter=quarter)
                case.respond([atom], [atom])
                parsed = case.parser.parse_claims(original)[0]
                self.assertNotIn('parsing_error', parsed, parsed)
                self.assertEqual(parsed['quarter'], 1)


class RatioSafetyTests(unittest.TestCase):
    def prepare(self,case):
        case.pc.update(operation='ratio',metric='current assets',denominator_metric='total assets',
                       claimed_literal='39.1%',unit='percent')
        case.evidence.update(rows=[['Total current assets','149,818','147,957'],['Total assets','383,266','359,241']],
                             row_units=['$ millions']*2)

    def test_incorrect_share_is_contradicted_using_the_same_source_cells(self):
        with fixture(source_fixture.SourceSelectionTests) as case:
            self.prepare(case)
            case.pc['claimed_literal']='49.1%'
            calc,comparison,reason,_=case.numeric.verify_numeric(case.pc,[case.evidence])
            self.assertIsNotNone(calc,reason)
            self.assertEqual(comparison['verdict'],'CONTRADICTED')
            self.assertAlmostEqual(comparison['difference'],49.1-149818/383266*100)
            case.numeric.structured.assert_not_called()

    def test_zero_denominator_is_expected_unavailability_not_execution_error(self):
        with fixture(source_fixture.SourceSelectionTests) as case:
            self.prepare(case)
            case.evidence['rows'][1][1]='0'
            calc,comparison,reason,_=case.numeric.verify_numeric(case.pc,[case.evidence])
            self.assertIsNone(calc)
            self.assertIsNone(comparison)
            self.assertIn('denominator is zero',reason)
            case.numeric.structured.assert_not_called()

    def test_ratio_rejects_mixed_currencies_types_and_documents(self):
        for scenario in ('currency','type','document','percent'):
            with self.subTest(scenario=scenario), fixture(source_fixture.SourceSelectionTests) as case:
                self.prepare(case)
                other=copy.deepcopy(case.evidence)
                other['id']='other::source'
                if scenario=='currency': other['row_units'][1]='EUR millions'
                if scenario=='type': other['row_units'][1]='count'
                if scenario=='document': other['document_id']='other-document'
                if scenario=='percent':
                    case.evidence['rows'][0][1]='50%'
                    other['rows'][1][1]='100%'
                selection=case.selection(case.operand(name='numerator',row_index=0),
                                         case.operand(name='denominator',row_index=1,evidence_id=other['id']))
                with patch.object(case.numeric,'_select',return_value=selection):
                    calc,comparison,reason,_=case.numeric.verify_numeric(case.pc,[case.evidence,other])
                self.assertIsNone(calc,reason)
                self.assertIsNone(comparison)

    def test_each_ratio_role_must_match_its_own_row_and_date(self):
        with fixture(source_fixture.SourceSelectionTests) as case:
            self.prepare(case)
            for name,row,column in (('numerator',1,1),('denominator',0,1),('denominator',1,2)):
                with self.subTest(name=name,row=row,column=column), self.assertRaises(ValueError):
                    case.numeric._value(case.pc,case.operand(name=name,row_index=row,column_index=column),copy.deepcopy(case.evidence))

    def test_table_title_cannot_make_an_unrelated_row_a_ratio_denominator(self):
        with fixture(source_fixture.SourceSelectionTests) as case:
            self.prepare(case)
            case.evidence.update(title='Total assets',context='Total assets',rows=[['Current assets','149,818','147,957']],row_units=['$ millions'])
            with self.assertRaises(ValueError):
                case.numeric._value(case.pc,case.operand(name='denominator',row_index=0),case.evidence)

    def test_prose_ratio_cannot_swap_nearby_source_metrics(self):
        with fixture(source_fixture.SourceSelectionTests) as case:
            self.prepare(case)
            item={'id':'text','type':'text','document_id':'doc-a','page':6,
                  'content':'In 2026, current assets were $149,818 million and total assets were $383,266 million.'}
            bank=case.numeric._bank(item)
            for name,raw,valid in (('numerator','$149,818 million',True),('denominator','$383,266 million',True),
                                   ('numerator','$383,266 million',False),('denominator','$149,818 million',False)):
                with self.subTest(name=name,raw=raw):
                    operand=case.operand(name=name,evidence_id='text',number_index=next(n['index'] for n in bank if n['raw']==raw))
                    pc=dict(case.pc,period_type='unspecified',period_quote='2026')
                    if valid:
                        self.assertGreater(case.numeric._value(pc,operand,copy.deepcopy(item))['value'],0)
                    else:
                        with self.assertRaises(ValueError): case.numeric._value(pc,operand,copy.deepcopy(item))


class FailureFlowTests(unittest.TestCase):
    def test_runtime_fault_is_an_execution_failure_and_excluded_from_accuracy(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            pc=case.parser._normalize(case.growth(),parser_fixture.C)
            for kind in (RuntimeError,ValueError,TypeError,KeyError):
                with self.subTest(error=kind.__name__), patch.object(case.numeric,'percentage_growth',side_effect=kind('PRIVATE_RUNTIME_DETAIL')):
                    result=active_pipeline(pc,lambda *a:case.calculate(pc))
                    self.assertEqual((result['status'],result['error_stage']),('error','numeric_calculation'))
                    self.assertTrue(execution_failed(result))
                    scored=score([{'status':result['status'],'gold':'SUPPORTED','predicted':result['verdict']}])
                    self.assertEqual(scored['completed'],0)
                    details=describe_failure(kind=result['error_kind'],message=result['error'],stage=result['error_stage'])
                    self.assertEqual(details['code'],'numeric_execution')
                    self.assertNotIn('PRIVATE_RUNTIME_DETAIL',json.dumps(details))

    def test_source_faults_report_the_stage_and_still_fail_closed(self):
        pc={'claim_type':'assertion','claim_text':'Fixture','operation':'growth'}
        for stage in ('document','retrieval','reranking','source_resolution','evidence_fusion'):
            with self.subTest(stage=stage):
                result=active_pipeline(pc,lambda *a:(None,None,'Missing evidence',[]),fail_stage=stage)
                self.assertEqual((result['status'],result['error_stage']),('error',stage))
                self.assertTrue(execution_failed(result))

    def test_validation_failure_keeps_its_exact_successful_parser_call_context(self):
        with fixture(parser_fixture.ClaimParserTests) as case:
            @contextmanager
            def calls():
                yield [{'status':'error','requested_model':'wrong/older-model','http_status':503},
                       {'status':'ok','requested_model':'vendor/parser','http_status':200,
                        'output_token_limit':16384,'finish_reason':'stop','request_id':'gen-fixture',
                        'prompt':'PRIVATE_CLAIM','api_key':'sk-private-secret'}],1
            case.parser.capture_calls=calls
            case.respond([case.atom('assertion')],[case.atom('assertion')])
            failure=case.parser.parse_claims("At June 27, 2026, Apple's total deferred revenue was $14.9 billion.")[0]
            details=describe_failure(kind=failure['error_kind'],stage='claim_parser',metadata=failure['parsing_context'])
            self.assertEqual(details['code'],'parse_validation')
            self.assertEqual((details['model'],details['http_status'],details['output_token_limit']),('vendor/parser',200,16384))
            self.assertEqual(details['provider_request_id'],'gen-fixture')
            self.assertEqual(details['validation_reason'],'wording')
            for secret in ('PRIVATE_CLAIM','sk-private-secret','wrong/older-model'):
                self.assertNotIn(secret,json.dumps(details))


class ParserPolicyTests(unittest.TestCase):
    def test_only_compatible_text_parser_disables_reasoning_in_http_payload(self):
        with fixture(diagnostics_fixture.VisionBudgetTests) as case:
            gateway=case.gateway
            schema=types.SimpleNamespace(model_json_schema=lambda:{'type':'object'},model_validate=lambda v:v)
            gateway.httpx.Timeout=lambda *a,**kw:None
            gateway.httpx.post.side_effect=None
            gateway.httpx.post.return_value=types.SimpleNamespace(status_code=200,is_error=False,headers={},
                json=lambda:{'id':'gen-fixture','model':'nvidia/nemotron-3.5-super-vl:free',
                             'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})
            for model,purpose,task,disabled in (
                ('nvidia/nemotron-3.5-super-vl:free','claim_parser','text',True),
                ('nvidia/nemotron-3.5-super-vl:free',None,'text',False),
                ('nvidia/nemotron-3.5-super-vl:free','claim_parser','vision',False),
                ('vendor/mandatory-thinking','claim_parser','text',False)):
                with self.subTest(model=model,purpose=purpose,task=task):
                    gateway.configure(gateway.ModelConfig(provider='openrouter',text_model=model,vision_model=model,draft_model=model,
                                                        text_fallbacks=(),allow_fallbacks=False,max_output_tokens=16384))
                    gateway.structured(schema,'fixture',purpose=purpose,task=task)
                    payload=gateway.httpx.post.call_args.kwargs['json']
                    self.assertEqual(payload.get('reasoning'),{'enabled':False} if disabled else None)
                    self.assertEqual(payload['max_tokens'],32768 if task=='vision' else 16384)


if __name__=='__main__':
    unittest.main()
