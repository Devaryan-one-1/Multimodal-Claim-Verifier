from __future__ import annotations
import sys,os,json,time,traceback,hashlib,shutil,subprocess,collections,math,copy
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
os.chdir(ROOT);sys.path.insert(0,str(ROOT/'app'));sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
OUT=ROOT/'data/test_results/full_qa_20261028';OUT.mkdir(parents=True,exist_ok=True)
def save(name,value):
 p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8');return str(p)
def emit(label,value): print(label,json.dumps(value,ensure_ascii=True,default=str),flush=True)
def guarded(name,fn):
 try:
  value=fn();save(name+'.json',value);emit(name,value if not isinstance(value,dict) else {k:v for k,v in value.items() if k not in ['queries','results','inventory']});return value
 except Exception as e:
  v={'status':'error','error':str(e),'kind':type(e).__name__,'traceback':traceback.format_exc()};save(name+'.json',v);emit(name,v);return v

def inventory():
 from evaluation.retrieval_evaluation import _load_dataset
 claims,truths,m=_load_dataset(Path('data/evaluation'))
 hclaims,htruths,hm=_load_dataset(Path('data/evaluation/hallucination'))
 paths=[p for d in ['data/processed','data/documents','data/evaluation/drafts','data/evaluation/exports'] for p in Path(d).rglob('*') if p.is_file()]
 paths += [Path('data/evaluation')/x for x in ['claims.jsonl','ground_truth.jsonl','manifest.json']]
 save('preservation_before.json',{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
 details=[]
 for label,d in [('main',Path('data/evaluation')),('hallucination',Path('data/evaluation/hallucination'))]:
  target=OUT/('datasets/'+label);target.mkdir(parents=True,exist_ok=True)
  for n in ['claims.jsonl','ground_truth.jsonl','manifest.json']:
   shutil.copy2(d/n,target/n)
  for n in ['verdict_prediction_cache','retrieval_checkpoints','ablation_cache','grounding_reviews']:
   if (d/n).is_dir():shutil.copytree(d/n,target/n,dirs_exist_ok=True)
  for folder in ['retrieval_results','verdict_results','grounding_results','ablation_results']:
   for p in sorted((d/folder).glob('*.json')):
    r=json.loads(p.read_text(encoding='utf-8'));details.append({'path':str(p),'complete':r.get('complete'),'valid_inputs':r.get('valid_inputs'),'paused':r.get('paused'),'pause':r.get('pause'),'summary':r.get('summary'),'query_statuses':dict(collections.Counter(q.get('status') for q in r.get('queries',[])))})
 readme=Path('README.md').read_text(encoding='utf-8')
 return {'status':'checked','main_count':len(claims),'review_status':dict(collections.Counter(t.get('review_status') for t in truths.values())),'labels':dict(collections.Counter(t['verdict'] for t in truths.values())),'coverage':m.get('reviewed_coverage'),'missing_case_tags':m.get('missing_case_tags'),'hallucination_count':len(hclaims),'hallucination_labels':dict(collections.Counter(t['verdict'] for t in htruths.values())),'saved_reports':details,'readme':{'architecture':'## Architecture' in readme,'setup':'## Local setup' in readme,'limitations':'limitation' in readme.lower(),'historical_retrieval':'historical completed retrieval baseline' in readme,'pending_metrics':'Pending completed' in readme,'ablation_pending':'Pending complete comparable reports' in readme},'ablation_dashboard_connected':'render_ablation_evaluation(' in Path('app/ui/workspace.py').read_text(encoding='utf-8')}

def offline():
 from verification import financial_calculations as f
 from verification.number_parser import parse_number
 from services.verification_errors import quota_pause
 from evaluation.verdict_evaluation import _metrics
 from ui.document_viewer import highlight_evidence
 from PIL import Image,ImageChops
 tests=[]
 def check(name,got,expected):tests.append({'name':name,'actual':got,'expected':expected,'pass':(math.isclose(got,expected,rel_tol=1e-9,abs_tol=1e-9) if isinstance(got,(int,float)) and isinstance(expected,(int,float)) else got==expected)})
 for n,r,x in [('growth',f.percentage_growth(100,110),10),('absolute',f.absolute_change(407,392),-15),('margin',f.margin(20,100),20),('cagr_two_year_span',f.cagr(100,121,2),10),('percentage_points',f.percentage_point_change('20%','25%'),5),('millions_billions',f.absolute_change('1000 million','1.1 billion'),100000000),('zero',f.percentage_growth(0,10),None),('negative',f.percentage_growth(-10,10),None)]:
  check(n,r['value'],x);tests[-1]['detail']=r
 for raw in ['EUR 10 million','USD 10 million','GBP 3 billion']:
  p,note=parse_number(raw);tests.append({'name':'currency_retained_'+raw,'parsed':p,'note':note,'pass':bool(p and p.get('currency')==raw[:3])})
 from verification.grounded_numeric import _value,Operand
 item={'id':'fixture','type':'table','document_id':'fixture','page':1,'headers':['Revenue','2024','2025'],'rows':[['Revenue','100','110']],'column_headers':{'raw':'Revenue USD millions years ended 2024 2025'},'column_map':[{'col':1,'year':2024,'period':'annual'},{'col':2,'year':2025,'period':'annual'}]}
 for name,pc,operand in [('quarterly_vs_annual',{'metric':'Revenue','end_period':2025,'period_type':'quarter'},Operand(name='value',evidence_id='fixture',row_index=0,column_index=2)),('wrong_year',{'metric':'Revenue','end_period':2024,'period_type':'annual'},Operand(name='value',evidence_id='fixture',row_index=0,column_index=2)),('invented_cell',{'metric':'Revenue','end_period':2025},Operand(name='value',evidence_id='fixture',row_index=8,column_index=2))]:
  try: value=_value(pc,operand,copy.deepcopy(item));tests.append({'name':name,'pass':False,'value':value})
  except ValueError as e: tests.append({'name':name,'pass':True,'reason':str(e)})
 for name,message,code,wanted in [('429','rate limit exceeded',429,True),('daily','daily quota exhausted',429,True),('402','This request requires more credits, or fewer max_tokens',402,True)]:
  pause=quota_pause(RuntimeError(message),[{'status':'error','http_status':code,'error_message':message}]);tests.append({'name':'pause_'+name,'pass':bool(pause)==wanted,'pause':pause})
 ev=json.loads(Path('data/processed/0000066740-26-000014-56e1ef4130/evidence.json').read_text(encoding='utf-8'))['evidence']
 table=next(e for e in ev if e['id']=='evidence_4868')
 image=Image.new('RGB',(1224,1584),'white')
 try:
  highlighted=highlight_evidence(image,table);changed=ImageChops.difference(image,highlighted).getbbox();tests.append({'name':'coordinate_highlight','pass':bool(changed),'changed_box':changed});highlighted.save(OUT/'highlight_test.png')
 except Exception as e:tests.append({'name':'coordinate_highlight','pass':False,'error':str(e)})
 from services.document_guards import usable_tables
 warnings=[];r=usable_tables({'tables':[{'page':1,'headers':['Metric','2025'],'rows':[]}]},warnings)
 tests.append({'name':'malformed_table_skipped','pass':not r['tables'] and bool(warnings),'warnings':warnings})
 return {'tests':tests,'passed':sum(t['pass'] for t in tests),'failed':sum(not t['pass'] for t in tests),'evidence_types':dict(collections.Counter(e['type'] for e in ev)),'empty_chart_ids':[e['id'] for e in ev if e['type']=='chart' and not e.get('content') and not e.get('facts')]}

def claims():
 from services.model_gateway import configure,ModelConfig
 from pipelines.verification_pipeline import verify_claims,aggregate_verdict
 configure(ModelConfig.from_env())
 cs=[json.loads(l) for l in Path('data/evaluation/claims.jsonl').read_text(encoding='utf-8').splitlines()];truth={x['id']:x for x in map(json.loads,Path('data/evaluation/ground_truth.jsonl').read_text(encoding='utf-8').splitlines())}
 cs += [{'id':'question_input','claim_text':'What was 3M revenue in 2025?','document_ids':['0000066740-26-000014-56e1ef4130']}]
 rows=[]
 for i,c in enumerate(cs,1):
  t=time.monotonic()
  try:
   r=verify_claims(c['claim_text'],c['document_ids']);p=save('claims/'+c['id']+'.json',r);ok=all(x.get('status')!='error' for x in r);label=aggregate_verdict(r)
   row={'id':c['id'],'claim':c['claim_text'],'expected':truth.get(c['id'],{}).get('verdict'),'actual':label,'operational_ok':ok,'matches_gold':label==truth.get(c['id'],{}).get('verdict') if ok and c['id'] in truth else None,'error':next((x.get('error') for x in r if x.get('error')),None),'result_json':p,'case_tags':truth.get(c['id'],{}).get('case_tags'),'seconds':time.monotonic()-t}
  except Exception as e:row={'id':c['id'],'operational_ok':False,'error':str(e),'seconds':time.monotonic()-t};save('claims/'+c['id']+'.json',row)
  rows.append(row);save('claims_progress.json',rows);emit('claim_progress',{'done':i,'total':len(cs),'id':c['id'],'operational_ok':row['operational_ok'],'matches_gold':row.get('matches_gold'),'error':str(row.get('error',''))[:180]})
 return {'attempted':len(rows),'operational_successes':sum(r['operational_ok'] for r in rows),'operational_errors':sum(not r['operational_ok'] for r in rows),'gold_matches':sum(r.get('matches_gold') is True for r in rows),'rows':rows}

def uploads():
 import pymupdf
 from PIL import Image,ImageDraw
 from services.model_gateway import configure,ModelConfig
 configure(ModelConfig.from_env())
 marker='C:/Users/devar/.codex/plugins/cache/openai-primary-runtime/pdf/26.1025.11240/skills/pdf/container_tools/mark_artifact_operation_started.mjs'
 subprocess.run(['node',marker,'--operation-kind','create','--expected-output-count','5','--output-format','pdf'],check=True)
 work=OUT/'extraction_workspace';work.mkdir(exist_ok=True);assets=work/'fixtures';assets.mkdir(exist_ok=True)
 def pdf(text=None):
  d=pymupdf.open();p=d.new_page()
  if text:p.insert_textbox(pymupdf.Rect(50,50,550,700),text,fontsize=12)
  return d
 d=pdf('QA Test Company annual report 2025.\nRevenue increased from USD 100 million in 2024 to USD 110 million in 2025.\nThis page contains prose only and has no structured table.\nThe report describes annual results for the year ended December 31, 2025.');d.save(assets/'text_only.pdf');d.close()
 d=pdf('This encrypted fixture requires an unlocked copy.');d.save(assets/'encrypted.pdf',encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='qa_owner',user_pw='qa_user');d.close()
 d=pdf();d.save(assets/'blank.pdf');d.close()
 im=Image.new('RGB',(1200,600),'white');draw=ImageDraw.Draw(im);draw.text((30,100),'QA Scanned Report 2025: Revenue USD 110 million',fill='black',stroke_width=1);im.save(assets/'scan.png');d=pdf();d[0].insert_image(pymupdf.Rect(40,100,570,400),filename=str(assets/'scan.png'));d.save(assets/'scanned.pdf');d.close()
 d=pdf();p=d[0];p.insert_text((40,60),'QA difficult table: USD millions, years ended 2024 and 2025',fontsize=11)
 for pos,text in [((45,105),'Metric'),((250,105),'2024'),((430,105),'2025'),((45,135),'Revenue'),((250,135),'100'),((430,135),'110'),((45,165),'Operating'),((55,180),'profit'),((250,180),'10'),((430,165),'12')]:p.insert_text(pos,text,fontsize=11)
 d.save(assets/'difficult_table.pdf');d.close()
 (assets/'invalid.pdf').write_bytes(b'not a PDF - QA fixture')
 for p in assets.glob('*.pdf'):
  if p.name in ['invalid.pdf','encrypted.pdf']:continue
  d=pymupdf.open(p);d[0].get_pixmap().save(assets/(p.stem+'.png'));d.close()
 os.chdir(work)
 from pipelines.document_pipeline import process_document
 rows=[]
 for name,ocr in [('invalid',False),('encrypted',False),('text_only',False),('scanned',True),('blank',False),('difficult_table',False)]:
  try:
   result=process_document((assets/(name+'.pdf')).read_bytes(),name+'.pdf','qa_'+name,enable_ocr=ocr,chart_mode='off')
   row={'name':name,'returned':result,'status':result.get('status')}
  except Exception as e:
   meta=Path('data/processed')/('qa_'+name)/'document.json';m=json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
   row={'name':name,'status':'error','error':str(e),'kind':type(e).__name__,'persisted':m,'index_exists':(meta.parent/'index.json').exists()}
  rows.append(row);save('uploads/'+name+'.json',row);emit('upload',row)
 os.chdir(ROOT)
 return {'rows':rows,'isolated_workspace':str(work),'note':'Full real pipeline, including providers; no mock embeddings.'}

def evals():
 from services.model_gateway import configure,ModelConfig
 from evaluation.verdict_evaluation import evaluate_verdicts
 from evaluation.retrieval_evaluation import evaluate_retrieval
 from evaluation.ablation_evaluation import evaluate_ablation,inspect_ablation_sources
 configure(ModelConfig.from_env())
 main=OUT/'datasets/main';h=OUT/'datasets/hallucination';results=[]
 calls=[('verdict_cache',lambda:evaluate_verdicts(dataset_dir=main,cache_mode='cache_only',stop_on_error=False)),('verdict_live',lambda:evaluate_verdicts(dataset_dir=main,cache_mode='reuse',stop_on_error=False,max_new_predictions=5)),('verdict_resume',lambda:evaluate_verdicts(dataset_dir=main,cache_mode='reuse',stop_on_error=False,max_new_predictions=5)),('hallucination_live',lambda:evaluate_verdicts(dataset_dir=h,cache_mode='reuse',stop_on_error=False,max_new_predictions=100)),('retrieval_live',lambda:evaluate_retrieval(dataset_dir=main,use_claim_parser=False,stop_on_error=False,resume=True,progress=lambda x:emit('retrieval_progress',x))),('ablation_inventory',lambda:inspect_ablation_sources(dataset_dir=main)),('ablation_cache',lambda:evaluate_ablation(dataset_dir=main,variants=('A','B','C','D'),cache_mode='cache_only',stop_on_error=False)),('ablation_live',lambda:evaluate_ablation(dataset_dir=main,variants=('A','B','C','D'),cache_mode='reuse',stop_on_error=False,progress=lambda x:emit('ablation_progress',x)))]
 for name,fn in calls:
  emit('starting',name);r=guarded('evaluations/'+name,fn)
  results.append({'test':name,'status':r.get('status'),'complete':r.get('complete'),'valid_inputs':r.get('valid_inputs'),'paused':r.get('paused'),'pause':r.get('pause'),'error':r.get('error'),'summary':r.get('summary'),'report_path':r.get('report_path'),'query_statuses':dict(collections.Counter(q.get('status') for q in r.get('queries',[])))})
  save('evaluation_progress.json',results)
 return {'runs':results}

def logs():
 paths=list(Path('data/logs').glob('*.jsonl'))+list((OUT/'extraction_workspace/data/logs').glob('*.jsonl'))
 records=[];bad=[]
 secrets=[v for k,v in os.environ.items() if ('API_KEY' in k or k.endswith('_TOKEN')) and len(v)>10]
 for p in paths:
  for line in p.read_text(encoding='utf-8').splitlines():
   try:r=json.loads(line)
   except Exception:bad.append({'path':str(p),'error':'invalid JSON'});continue
   records.append(r)
   if any(s in line for s in secrets) or any(k in r for k in ['prompt','claim_text','document_text','api_key']):bad.append({'path':str(p),'event':r.get('event'),'error':'sensitive field or key'})
 before=json.loads((OUT/'preservation_before.json').read_text(encoding='utf-8'))
 changed=[p for p,h in before.items() if not Path(p).is_file() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
 return {'files':list(map(str,paths)),'event_counts':dict(collections.Counter(r.get('event') for r in records)),'missing_timestamp':sum(not r.get('timestamp') for r in records),'missing_request_id':sum(not r.get('request_id') for r in records),'privacy_findings':bad,'preserved_source_files':len(before),'changed_source_files':changed}

if __name__=='__main__':
 phase=sys.argv[1]
 guarded(phase,globals()[phase])

