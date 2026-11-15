"""Controlled parser audit; no provider calls or customer data."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tests'))
import test_claim_parser as fixtures
from test_claim_pipeline import BALANCE, DATES

results = []
def probe(name, original, build):
    case = fixtures.ClaimParserTests()
    case.setUp()
    try:
        atoms = build(case)
        case.respond(atoms, atoms)
        parsed = case.parser.parse_claims(original)
        results.append(dict(case=name, outputs=[{k:p.get(k) for k in
            ('claim_type','metric','operation','start_period','end_period','parsing_error')} for p in parsed]))
    finally:
        case.doCleanups()

for phrase in ('For Northstar, did revenue change by 18% over the reported periods?',
               'For Northstar, did revenue increase by 18% over the reported periods?',
               'Between 2025 and 2026, did Northstar revenue increase by 18%?'):
    probe(phrase, phrase, lambda c, text=phrase: [c.growth(claim_text=text, company='Northstar',
          subject='revenue',metric='revenue',claimed_literal='18%',start_period=None,end_period=None,
          period_quote='2025 and 2026' if '2025' in text else 'reported periods',claim_type='question')])

threshold = fixtures.C.replace('grew 8.8%', 'grew not more than 8.8%')
probe('not-more-than threshold', threshold, lambda c: [c.growth(claim_text=threshold, negated=False)])

for op in ('ratio','level'):
    def build(c, op=op):
        atoms = [c.growth(claim_text=f"Between {DATES}, Apple's {metric} grew approximately {value}.",
                 subject=metric,metric=metric,claimed_literal=value,period_quote=DATES)
                 for metric,value in (('total assets','6.7%'),('current assets','1.3%'))]
        atoms.append(c.atom("Consequently, current assets' share of total assets fell from approximately 41.2% to 39.1%.",
                    subject='current assets',metric='current assets',denominator_metric='total assets',
                    operation=op, claimed_literal='41.2%',unit='percent',start_period=2025,end_period=2026,period_quote=DATES))
        return atoms
    probe('combined share classified as '+op, BALANCE, build)
print(json.dumps(results, indent=2))
