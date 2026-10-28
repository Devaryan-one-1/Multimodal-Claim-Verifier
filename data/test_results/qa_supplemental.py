import sys,os,json,copy,hashlib,shutil,logging
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];os.chdir(ROOT);sys.path.insert(0,str(ROOT/'app'));sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
OUT=ROOT/'data/test_results/full_qa_20261028'
def write(name,x):
 (OUT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def main():
 from streamlit.testing.v1 import AppTest
 from retrieval import embeddings
 doc='0000066740-26-000014-56e1ef4130'
 evidence=json.loads(Path(f'data/processed/{doc}/evidence.json').read_text(encoding='utf-8'))['evidence']
 table=next(e for e in evidence if e['id']=='evidence_4868')
 base={'claim_text':'QA DISPLAY FIXTURE - not a measured prediction','document_id':doc,'document_ids':[doc],'verdict':'CONTRADICTED','reason':'QA UI fixture: source changes from 407 to 392.','calculation':{'value':-15,'formula':'new - old','inputs':{'old':407000000,'new':392000000},'raw_inputs':{'old':'$407 million','new':'$392 million'},'input_metadata':{'old':{'currency':'USD','scale':1000000},'new':{'currency':'USD','scale':1000000}},'currency':'USD','result_unit':'USD millions','result_precision':2},'comparison':{'claimed':15,'actual':-15,'unit':'USD millions','tolerance':0.5},'evidence':[table]}
 a=AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=90).run();tests=[]
 tests.append({'name':'dashboard_load','pass':not a.exception,'exceptions':[e.message for e in a.exception]})
 before=hashlib.sha256(Path(f'data/processed/{doc}/index.json').read_bytes()).hexdigest()
 with patch.object(embeddings,'embed_document',side_effect=AssertionError('Reasoning change attempted document embedding')) as embed:
  a.text_input(key='mm_openrouter_text').set_value('minimax/minimax-m3.1-flash').run()
  tests.append({'name':'reasoning_change_no_reembedding','pass':not a.exception and not embed.called and before==hashlib.sha256(Path(f'data/processed/{doc}/index.json').read_bytes()).hexdigest(),'calls':embed.call_count,'exceptions':[e.message for e in a.exception]})
 a.session_state['latest_verification_results']=[base];a.run()
 tests.append({'name':'contradicted_expands','pass':next(e for e in a.expander if e.label=='Calculation').proto.expanded})
 tests.append({'name':'signed_difference_claimed_minus_actual','pass':any(m.label=='Signed difference' and '+30' in str(m.value) for m in a.metric),'metrics':[(m.label,m.value) for m in a.metric]})
 tests.append({'name':'original_and_normalized_values','pass':all(any(s in str(x.value) for x in a.text) for s in ['$407 million','$392 million','407,000,000','392,000,000']),'text':[x.value for x in a.text]})
 tests.append({'name':'currency_unit_tolerance_visible','pass':any('Currency: USD' in x.value for x in a.caption) and any('Comparison tolerance:' in x.value for x in a.caption),'captions':[x.value for x in a.caption if any(s in x.value for s in ['Currency','unit:','tolerance:'])]})
 check=next(c for c in a.checkbox if c.label=='Show full table');n=len(a.dataframe);check.check().run()
 tests.append({'name':'full_table_expands','pass':len(a.dataframe)>n,'before_frames':n,'after_frames':len(a.dataframe)})
 next(b for b in a.button if b.label=='View Page').click().run()
 tests.append({'name':'table_view_page','pass':next(x.value for x in a.number_input if x.label=='Page')==102 and not a.exception,'citation':a.session_state['dashboard_citation']})
 a.selectbox(key='dashboard_viewer_document').set_value('Apple_Financial_10_K').run()
 tests.append({'name':'selector_preserves_citation_identity','pass':a.session_state['dashboard_citation']['evidence'] is None and a.session_state['latest_verification_results'][0]['document_id']==doc})
 for label in ['SUPPORTED','UNKNOWN','PARTIALLY_SUPPORTED']:
  a.session_state['latest_verification_results']=[dict(base,verdict=label,calculation=None,calculation_unavailable_reason='Explicit QA fixture reason')];a.run()
  tests.append({'name':label+'_calculation_collapsed','pass':not next(e for e in a.expander if e.label=='Calculation').proto.expanded})
  tests.append({'name':label+'_unavailable_reason','pass':any(x.value=='Calculation unavailable' for x in a.warning) and any('Explicit QA fixture reason' in str(x.value) for x in a.caption)})
 for kind in ['text','chart']:
  e=next(e for e in evidence if e['type']==kind)
  a.session_state['latest_verification_results']=[dict(base,evidence=[e])];a.run();next(b for b in a.button if b.label=='View Page').click().run()
  tests.append({'name':kind+'_view_page','pass':not a.exception and next(x.value for x in a.number_input if x.label=='Page')==e['page'],'source_content_usable':bool(e.get('content') or e.get('facts'))})
 tests.append({'name':'ablation_dashboard_integration','pass':any('ablation' in b.label.lower() for b in a.button)})
 write('ui_checks.json',{'fixture_only':True,'tests':tests,'passed':sum(t['pass'] for t in tests),'failed':sum(not t['pass'] for t in tests)})
 print('UI_SUMMARY',json.dumps([(t['name'],t['pass']) for t in tests]),flush=True)

def faults():
 from evaluation.verdict_evaluation import evaluate_verdicts
 from services.model_gateway import configure,ModelConfig
 from pipelines import verification_pipeline as pipeline
 configure(ModelConfig.from_env())
 tests=[];base=OUT/'datasets/main';folder=OUT/'control_flow_fixtures';folder.mkdir(exist_ok=True)
 for name in ['claims.jsonl','ground_truth.jsonl','manifest.json']:shutil.copy2(base/name,folder/name)
 calls=[]
 def successful(claim_text,document_ids):
  calls.append(claim_text);return [{'status':'complete','verdict':'SUPPORTED','claim_text':claim_text,'reason':'Synthetic control-flow fixture. Not measured performance.','document_ids':document_ids,'evidence':[]}]
 with patch.object(pipeline,'verify_claims',side_effect=successful):
  first=evaluate_verdicts(dataset_dir=folder,cache_mode='reuse',stop_on_error=False,max_new_predictions=5)
  second=evaluate_verdicts(dataset_dir=folder,cache_mode='reuse',stop_on_error=False,max_new_predictions=5)
  cached=evaluate_verdicts(dataset_dir=folder,cache_mode='cache_only',stop_on_error=False)
 tests.append({'name':'five_prediction_pause','pass':first['paused'] and first['pause']['kind']=='prediction_budget','summary':first['summary']})
 tests.append({'name':'resume_reuses_successful_cache','pass':sum(q['prediction_source']=='cache' for q in second['queries'])>=5,'summary':second['summary']})
 tests.append({'name':'cache_only_calls_no_provider','pass':len(calls)==10,'live_fixture_calls':len(calls),'summary':cached['summary']})
 badfolder=OUT/'fault_fixtures';badfolder.mkdir(exist_ok=True)
 for name in ['claims.jsonl','ground_truth.jsonl','manifest.json']:shutil.copy2(base/name,badfolder/name)
 def quota(claim_text,document_ids):return [{'status':'error','verdict':'UNKNOWN','error':'429 daily quota exhausted','error_stage':'claim_parser','pause':{'kind':'daily_quota','reason':'Synthetic QA quota failure'}}]
 with patch.object(pipeline,'verify_claims',side_effect=quota):bad=evaluate_verdicts(dataset_dir=badfolder,cache_mode='reuse',stop_on_error=False,max_new_predictions=5)
 tests.append({'name':'quota_always_pauses','pass':bad['paused'] and sum(q['status']=='error' for q in bad['queries'])==1,'pause':bad['pause']})
 tests.append({'name':'errors_excluded_from_verdict_metrics','pass':all(s['accuracy'] is None and s['macro_f1'] is None and s['completed_queries']==0 for s in bad['summary']),'summary':bad['summary']})
 from evaluation import hallucination as h
 existing=sorted(Path('data/evaluation/hallucination/verdict_results').glob('*.json'))[-1]
 report=json.loads(existing.read_text(encoding='utf-8'));issues=[]
 for q in report['queries']:
  if q['status']=='ok':issues.append({'query_id':q['query_id'],'automatic_issues':h.local_grounding_checks(q)})
 write('grounding_automatic_checks.json',{'source_report':str(existing),'human_assessment':False,'queries':issues,'note':'Automated checks cannot replace the requested human grounding assessments.'})
 q=next((q for q in report['queries'] if q['status']=='ok'),None)
 if q:
  with patch.object(h,'ROOT',OUT/'grounding_control_fixture'):
   r=h.save_grounding_review(report,q['query_id'],reviewer='Codex QA control-flow fixture; NOT a human assessment',numbers_ok=False,quotes_ok=False,reasoning_ok=False,notes='Persistence test only; source prediction is not approved as grounded.')
   files=list((h.ROOT/'grounding_reviews').glob('*.json'));tests.append({'name':'grounding_review_persists_in_isolation','pass':bool(files) and r['grounding_pass'] is False})
 write('failure_resume_checks.json',{'fixture_only':True,'tests':tests,'passed':sum(t['pass'] for t in tests),'failed':sum(not t['pass'] for t in tests),'warning':'Synthetic fixture reports must never be cited as measured model performance.'})
 print('FAULT_SUMMARY',json.dumps([(t['name'],t['pass']) for t in tests]),flush=True)

if __name__=='__main__':
 for name,fn in [('ui',main),('faults',faults)]:
  try:fn()
  except Exception as e:
   import traceback
   write(name+'_harness_error.json',{'error':str(e),'traceback':traceback.format_exc()});print(name,'HARNESS_ERROR',repr(e),flush=True)

