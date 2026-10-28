import os,sys,json,math,copy,logging,hashlib,zipfile,runpy
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];os.chdir(ROOT);sys.path.insert(0,str(ROOT/'app'));sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
OUT=ROOT/'data/test_results/full_qa_20261028'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def write(name,x):(OUT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
from services.model_gateway import configure,ModelConfig
from services.observability import configure_logging
log=configure_logging()
for h in list(log.handlers):
 if type(h) is logging.StreamHandler:log.removeHandler(h)
configure(ModelConfig.from_env())
from evaluation.verdict_evaluation import evaluate_verdicts
try:
 report=evaluate_verdicts(dataset_dir=OUT/'datasets/main',cache_mode='reuse',stop_on_error=False,max_new_predictions=200,progress=lambda s: print(s,flush=True) if '/160' in s and int(s.split('/')[0].split()[-1])%20==0 else None)
 write('evaluations/verdict_full.json',report)
 print('FULL_VERDICT',json.dumps(report['summary']),flush=True)
except Exception as e:write('evaluations/verdict_full.json',{'error':str(e),'kind':type(e).__name__});print('FULL_VERDICT_ERROR',str(e),flush=True)
from verification import grounded_numeric as g
base={'id':'fixture','document_id':'qa_fixture','type':'table','page':1,'headers':['Revenue','2024','2025'],'rows':[['Revenue','100','110']],'column_headers':{'raw':'Revenue USD millions years ended 2024 2025'},'column_map':[{'col':1,'year':2024,'period':'annual'},{'col':2,'year':2025,'period':'annual'}]}
pc={'operation':'growth','metric':'Revenue','start_period':2024,'end_period':2025,'period_type':'annual','claimed_literal':'10%'}
tests=[]
def case(name,item,parsed,expected=None,reason_contains=None,selection=None):
 with patch.object(g,'structured',side_effect=AssertionError('No provider calls allowed in numerical fixture tests')):
  try:
   if selection:
    with patch.object(g,'_select',return_value=selection):r=g.verify_numeric(copy.deepcopy(parsed),copy.deepcopy(item))
   else:r=g.verify_numeric(copy.deepcopy(parsed),copy.deepcopy(item))
   calc,comp,reason,used=r
   good=(calc is None and reason_contains.lower() in reason.lower()) if reason_contains else bool(comp and comp['verdict']==expected)
   tests.append({'name':name,'pass':good,'calculation':calc,'comparison':comp,'reason':reason})
  except Exception as e:tests.append({'name':name,'pass':False,'error':str(e)})
case('source_growth',[base],pc,'SUPPORTED')
case('source_contradicted_growth',[base],dict(pc,claimed_literal='40%'),'CONTRADICTED')
a=copy.deepcopy(base);a['rows'][0][2]='110.04'
case('rounded_within_tolerance',[a],dict(pc,claimed_literal='10.0%'),'SUPPORTED')
case('rounded_outside_tolerance',[a],dict(pc,claimed_literal='10.1%'),'CONTRADICTED')
a=copy.deepcopy(base);a['rows'][0]=['Revenue','USD 100 million','EUR 110 million'];a['column_headers']['raw']='Revenue years ended 2024 2025'
case('different_currencies_no_conversion',[a],pc,reason_contains='currencies differ')
a=copy.deepcopy(base);a['rows'][0][1]='0'
case('zero_baseline_explicit',[a],pc,reason_contains='zero baseline')
a=copy.deepcopy(base);a['rows'][0][1]='-100'
case('negative_baseline_explicit',[a],pc,reason_contains='negative baseline')
a=copy.deepcopy(base);a['rows'][0]=['Revenue','100','121'];a['column_map'][1]['year']=2026;a['headers'][2]='2026';a['column_headers']['raw']='Revenue USD millions years ended 2024 2026'
case('cagr_actual_year_span',[a],dict(pc,operation='cagr',end_period=2026),'SUPPORTED')
a=copy.deepcopy(base);a['rows'][0]=['Revenue margin','20%','25%'];a['column_headers']['raw']='Revenue margin percent years ended 2024 2025'
case('percentage_points_source',[a],dict(pc,metric='Revenue margin',operation='percentage_points',claimed_literal='5 percentage points'),'SUPPORTED')
a=copy.deepcopy(base);a['rows'][0]=['Revenue','1000 million','1.1 billion'];a['column_headers']['raw']='Revenue USD years ended 2024 2025'
case('millions_billions_source',[a],pc,'SUPPORTED')
a=copy.deepcopy(base);a['rows']=[['Operating profit','20','22'],['Revenue','100','110']];a['column_headers']['raw']='Operating margin USD millions years ended 2024 2025'
sel=g.Selection(applicable=True,reason='QA explicit-address fixture; no model involved',operands=[g.Operand(name='profit',evidence_id='fixture',row_index=0,column_index=2),g.Operand(name='revenue',evidence_id='fixture',row_index=1,column_index=2)])
case('margin_amounts',[a],dict(pc,operation='margin',metric='operating margin',claimed_literal='20%'),'SUPPORTED',selection=sel)
write('numerical_source_checks.json',{'fixture_only':True,'tests':tests,'passed':sum(t['pass'] for t in tests),'failed':sum(not t['pass'] for t in tests)})
print('NUMERIC_SUMMARY',json.dumps([(t['name'],t['pass']) for t in tests]),flush=True)
ctx=runpy.run_path(str(ROOT/'data/test_results/qa_full_runner.py'))
logs=ctx['logs']();write('logs.json',logs)
inv=read('inventory.json');claims=read('claims.json');offline=read('offline.json');ui=read('ui_checks.json');fault=read('failure_resume_checks.json');uploads=read('uploads.json');retrieval=read('evaluations/retrieval_live.json');ablation=read('evaluations/ablation_live.json');hall=read('evaluations/hallucination_live.json');verdict=read('evaluations/verdict_full.json')
checks=[{'step':1,'status':'PASS (headless)','detail':'Dashboard loaded without application exceptions; browser visual access unavailable.'},{'step':2,'status':'PASS','detail':'3M original PDF 158 pages; approved drafts and saved reports persisted across restart. Source hashes unchanged.'},{'step':3,'status':'PARTIAL','detail':'OpenRouter model selected; Gemini gemini-embedding-001 768 dimensions retained; reasoning model change did not embed documents. Live reasoning blocked by HTTP 402; vision model blank.'},{'step':4,'status':'FAILED OPERATIONALLY','detail':f"All {claims['attempted']} direct inputs attempted; {claims['operational_errors']} service errors. No valid factual accuracy denominator."},{'step':5,'status':'FUNCTIONAL CHECKS PASS; LIVE BLOCKED','detail':f"Primitive/safety checks {offline['passed']}/{offline['passed']+offline['failed']} (the failure is HTTP 402 pause classification). Numerical source-fixture checks {sum(t['pass'] for t in tests)}/{len(tests)}. Live reviewed calculations blocked at parser."},{'step':6,'status':'PARTIAL','detail':'Table/text/chart page navigation, full table, citation identity and coordinate highlight passed functional tests. Chart record has no usable content or indexed chunks; no saved OCR evidence.'},{'step':7,'status':'PASS for error/coverage handling','detail':'Six isolated PDF fixtures attempted. Invalid and encrypted rejected; text-only ready with warning; scan/blank failed without ready index; difficult table text retained with warning.'},{'step':8,'status':'PARTIAL / LOGGING BUG','detail':f"Timestamps present and no keys/prompts/document text detected. {logs['missing_request_id']} records lack correlation (400 ablation calls plus one deliberately unwrapped malformed-table fixture). Model/provider/duration fields on llm_called are null."},{'step':9,'status':'PARTIAL / CREDIT PAUSE BUG','detail':'Five-prediction limit, reuse, cache-only, 429 pause and error exclusion passed isolated control-flow fixtures. Natural HTTP 402 credit exhaustion does not pause evaluation. No compatible real successful predictions existed for this configuration.'},{'step':10,'status':'PARTIAL','detail':'100 approved main claims, 25 per verdict. Missing currencies/charts/OCR/footnotes coverage. Retrieval complete with parser disabled; full verdict and hallucination attempts failed operationally. All four ablations attempted; 400 errors. Human grounding review pending.'},{'step':11,'status':'PASS','detail':'README includes architecture, setup, limitations and historical retrieval; verdict F1, numerical accuracy, ablation and hallucination conclusions remain pending.'}]
write('summary.json',{'steps':checks,'preservation':logs,'direct_claims':{k:claims[k] for k in ['attempted','operational_successes','operational_errors']},'retrieval_summary':retrieval['summary'],'verdict_summary':verdict.get('summary'),'hallucination_summary':hall.get('summary'),'ablation_summary':ablation['summary'],'ui_functional_tests':{'passed':ui['passed'],'failed':ui['failed']},'failure_resume_tests':{'passed':fault['passed'],'failed':fault['failed']},'numerical_source_tests':{'passed':sum(t['pass'] for t in tests),'failed':sum(not t['pass'] for t in tests)}})
lines=['# Claim verifier QA report - 28 October 2026','','Tests continued after failures. Application source code, reviewed claims and approvals were preserved. New reports live under this test directory.','', '## Steps 1-11','','| Step | Status | Finding |','| --- | --- | --- |']
for c in checks:lines.append(f"| {c['step']} | {c['status']} | {c['detail']} |")
lines+=['','## Provider failure','','Configured provider: OpenRouter. Reasoning model: `nvidia/nemotron-3-ultra-550b-a55b`. Maximum output: 8192 tokens.','','Exact provider error excerpt: `Model service error 402: This request requires more credits, or fewer max_tokens. You requested up to 8192 tokens, but can only afford 7454.`','','The complete unmodified error and model-call record are retained in each claim JSON under `claims/`. A service failure is not a factual UNKNOWN gold-label outcome. No accuracy or F1 is claimed from these failed predictions.','','## Completed retrieval run','','Parser disabled; candidate depth 20; 75 eligible complete claims and 49 eligible subclaims. Queries with no positive gold evidence are excluded. Historical parser-enabled results in README are a different configuration.','','| Level | Stage | Recall@5 | Recall@10 | MRR@20 |','| --- | --- | ---: | ---: | ---: |']
for s in retrieval['summary']:lines.append(f"| {s['level']} | {s['stage']} | {s['Recall@5']:.2%} | {s['Recall@10']:.2%} | {s['MRR@20']:.4f} |")
lines+=['','## Runtime attempts','','| Workflow | Outcomes |','| --- | --- |',f"| Individual inputs | {claims['attempted']} attempted, {claims['operational_errors']} operational errors |",f"| Full verdict workflow | {len(verdict.get('queries',[]))} claim/subclaim queries; {sum(q['status']=='error' for q in verdict.get('queries',[]))} errors |",f"| Hallucination workflow | {len(hall['queries'])} claim/subclaim queries; {sum(q['status']=='error' for q in hall['queries'])} errors |",f"| Ablation | {len(ablation['queries'])} attempts; {sum(q['status']=='error' for q in ablation['queries'])} errors; four identical model configurations |",'','## Failures requiring attention','','1. OpenRouter rejects the configured 8192-token reservation because the key can afford 7454. Provider capacity must be restored or the output limit changed before valid verdict metrics can be measured.','2. `quota_pause` does not classify natural HTTP 402 credit exhaustion as a pause. The UI can therefore repeat failed requests until the new-prediction budget stops it.','3. `render_ablation_evaluation` exists but is absent from `run_dashboard`. The backend was tested directly.','4. Four hundred ablation `llm_called` events lack request IDs; event adapter uses field names that leave provider/model/duration null.','5. Reviewed coverage lacks currencies, charts, OCR and footnotes. The stored chart has no readable content, and the ablation corpus has zero indexed chart chunks. The vision model is unconfigured.','','## Scope and safeguards','','- Streamlit AppTest exercised the actual dashboard. Browser visual access was unavailable; this is not a claim of a full visual browser pass.','- Numerical/display/control-flow fixtures are labeled and stored separately. Their synthetic predictions are not model-performance measurements.','- Source page 102 was visually inspected: China/Hong Kong net PP&E is 392 million in 2025 and 407 million in 2024. Its year/value pairs agree with saved reviewed evidence.','- Human grounding assessments remain pending. Automated checks were saved, and review persistence was tested in an isolated fixture; no human approval was fabricated.',f"- Preserved {logs['preserved_source_files']} tracked source/data files; changed files: {logs['changed_source_files']}.",'- Existing saved reports remain untouched. New live report files are under `datasets/main/` and `datasets/hallucination/`; synthetic control-flow reports are under distinct fixture directories.','','## Artifacts','','- `summary.json`: machine-readable outcome for every step.','- `claims/`: one full result JSON per reviewed claim plus the question.','- `evaluations/`: full verdict, retrieval, hallucination and ablation reports.','- `uploads/`: exact per-input processing results.','- `ui_checks.json`, `numerical_source_checks.json`, `offline.json`, `failure_resume_checks.json`: detailed functional checks.','- `logs.json`: event privacy, correlation and source-preservation checks.']
(OUT/'QA_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
with zipfile.ZipFile(OUT/'qa_results.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in OUT.rglob('*'):
  if not p.is_file() or p.suffix.lower() not in ['.json','.md']:continue
  rel=p.relative_to(OUT)
  if rel.parts[0] in ['control_flow_fixtures','fault_fixtures','grounding_control_fixture','extraction_workspace']:continue
  if any(x in rel.parts for x in ['verdict_prediction_cache','retrieval_checkpoints','ablation_cache']):continue
  z.write(p,str(rel))
print('FINAL_REPORT',str(OUT/'QA_REPORT.md'),flush=True);print('PRESERVATION',logs['changed_source_files'],flush=True)
