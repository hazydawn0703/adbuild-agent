"""One-attempt regression of exposed private inputs; never an independent blind test.

Inputs: protocol.json (ordered cases), setup-context.json (source-grounded account
bindings), and one directory per case containing brief.xlsx, export.xlsx, paste.txt.
Reference answers are forbidden in the input directory. Scoring is a later process.
Run from backend after freezing the exact release, against an initialized *_test DB.
"""
import argparse
import asyncio
import contextvars
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import traceback
from unittest.mock import patch
from uuid import uuid4
from datetime import datetime,timezone


def now():return datetime.now(timezone.utc).isoformat()


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_inputs(directory):
    """Allow only data required by the run; gold cannot enter the model process."""
    directory=Path(directory).resolve()
    protocol=json.loads((directory/'protocol.json').read_text(encoding='utf-8-sig'))
    contexts=json.loads((directory/'setup-context.json').read_text(encoding='utf-8-sig'))
    cases=protocol.get('cases')
    if not isinstance(cases,list) or not cases or len(cases)!=len(set(cases)):
        raise ValueError('Input protocol requires a nonempty, unique ordered case list')
    if any(not isinstance(case,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',case) for case in cases):
        raise ValueError('Unsafe case directory name')
    allowed={'protocol.json','setup-context.json'}
    for case in cases:
        for name in ('brief.xlsx','export.xlsx','paste.txt'):allowed.add(case+'/'+name)
        context=contexts.get(case)
        if not isinstance(context,dict) or not isinstance(context.get('account_id'),str):
            raise ValueError('Each case requires explicit account setup, including an empty unknown account')
        if context['account_id'] and not re.fullmatch(r'[0-9]{1,30}',context['account_id']):
            raise ValueError('Account setup must contain a real numeric ID, or remain empty')
        if not context.get('source'):raise ValueError('Account setup requires its original source description')
    actual={p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual!=allowed:raise ValueError('Input allowlist mismatch; missing='+repr(sorted(allowed-actual))+' extra='+repr(sorted(actual-allowed)))
    for path in directory.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(directory):raise ValueError('Linked inputs are not allowed')
    return cases,contexts,{name:digest(directory/name) for name in sorted(allowed)}


def verify_sources(root,manifest):
    root=Path(root).resolve();files=manifest.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('A source freeze is required')
    required={p.relative_to(root).as_posix() for p in (root/'backend/app').glob('*.py')}
    required.add('backend/evals/run_regression.py')
    if not required<=set(files):raise ValueError('Freeze does not cover the complete app and this driver')
    mismatches=[]
    for name,expected in files.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root):raise ValueError('Source freeze contains an unsafe path')
        if not path.is_file() or digest(path)!=expected:mismatches.append(name)
    return mismatches


class Run:
    def __init__(self,output,secrets_to_mask):
        self.output=Path(output).resolve();self.output.mkdir(parents=True,exist_ok=False,mode=0o700)
        self.raw=self.output/'raw';self.raw.mkdir(mode=0o700)
        self.lock=threading.Lock();self.secrets=[value for value in secrets_to_mask if value]
        self.ad_calls=0

    def safe(self,value):
        text=str(value)
        for secret in self.secrets:text=text.replace(secret,'[REDACTED]')
        return text

    def write(self,path,value):
        with (self.output/path).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,default=str)

    def event(self,kind,value):
        with self.lock:
            with (self.raw/'model-events.jsonl').open('a',encoding='utf-8') as f:
                f.write(self.safe(json.dumps({'time':now(),'case':CASE.get(),'stage':STAGE.get(),'event':kind,'payload':value},ensure_ascii=False,default=str))+'\n')

    async def forbidden_ad_call(self,*args,**kwargs):
        self.ad_calls+=1;self.event('forbidden_advertising_call',{})
        raise AssertionError('Advertising calls are forbidden during regression')

    def seal(self):
        entries={p.relative_to(self.output).as_posix():digest(p) for p in self.output.rglob('*') if p.is_file()}
        self.write('outputs-seal.json',{'sealed_at':now(),'files':entries})


CASE=contextvars.ContextVar('exposed_regression_case',default='setup')
STAGE=contextvars.ContextVar('exposed_regression_stage',default='setup')
RECORDER=contextvars.ContextVar('exposed_regression_recorder',default=None)


async def main(args):
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file,override=True)
    from sqlalchemy.engine import make_url
    from app.settings import settings
    settings.cache_clear();cfg=settings()
    if cfg.execution_enabled:raise RuntimeError('Refuse execution-enabled environment')
    if not (make_url(cfg.database_url).database or '').endswith('_test'):raise RuntimeError('Refuse a non-test database')
    if not cfg.llm_api_key:raise RuntimeError('A real configured model is required')
    source_root=Path(__file__).resolve().parents[2]
    manifest=json.loads(Path(args.freeze).read_text(encoding='utf-8'))
    if manifest.get('commit')!=args.commit or manifest.get('model')!=cfg.llm_model or manifest.get('execution_enabled') is not False:
        raise ValueError('Commit, model configuration or execution flag differs from freeze')
    mismatch=verify_sources(source_root,manifest)
    if mismatch:raise ValueError('Source files differ from freeze: '+repr(mismatch))
    inputs=Path(args.inputs).resolve();output=Path(args.output).resolve()
    if output==inputs or output.is_relative_to(inputs):raise ValueError('Output must be a new directory outside inputs')
    cases,contexts,input_hashes=validate_inputs(inputs)
    os.umask(0o077)
    run=Run(output,[cfg.llm_api_key,cfg.session_secret,cfg.token_encryption_key,make_url(cfg.database_url).password])
    os.environ['STORAGE_DIRECTORY']=str(output/'private-workbooks');settings.cache_clear()
    run.write('freeze.json',manifest)
    protocol={'label':'exposed_regression','run_id':args.run_id,'parent_run':args.parent_run,'started_at':now(),
              'commit':args.commit,'cases':cases,'input_files':input_hashes,'model':cfg.llm_model,
              'attempts_per_case':1,'max_analysis_actions':1,'max_unchanged_proposal_acceptances':1,
              'mapping_resume_actions':0,'extra_retries':0,'manual_mapping_changes':0,'manual_field_changes':0,
              'extra_answers':0,'final_confirmation':False,'execution_enabled':False,
              'workflow':['source-grounded account setup','upload unchanged export and brief','paste text verbatim before mapping',
                          'propose mapping once','confirm unchanged complete mapping','analyze once after draft exists',
                          'accept at most one unchanged proposal','read final task and preview'],
              'gold_in_model_inputs':False,'scoring':'Separate process only after outputs-seal.json is written',
              'limits':'No assisted completion is measured; file_error and unresolved scope are not successes',
              'dependency_versions':{p:importlib.metadata.version(p) for p in ('openpyxl','pydantic','fastapi','langgraph','langchain-openai','PyMySQL','aiomysql')}}
    run.write('protocol.json',protocol)
    from app.auth import password_hash
    from app.db import transaction
    from app.main import app
    import httpx
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import register_configure_hook

    class Recorder(BaseCallbackHandler):
        def on_chat_model_start(self,serialized,messages,*,run_id,**kwargs):
            run.event('model_start',{'run_id':str(run_id),'messages':[[m.model_dump(mode='json') for m in seq] for seq in messages]})
        def on_llm_end(self,response,*,run_id,**kwargs):run.event('model_end',{'run_id':str(run_id),'response':response.model_dump(mode='json')})
        def on_llm_error(self,error,*,run_id,**kwargs):run.event('model_error',{'run_id':str(run_id),'error_type':type(error).__name__,'message':run.safe(error)})
    register_configure_hook(RECORDER,True);RECORDER.set(Recorder())
    async def observed(scope,receive,send):
        try:await app(scope,receive,send)
        except BaseException as error:
            run.event('server_error',{'error_type':type(error).__name__,'message':run.safe(error),'stack':run.safe(traceback.format_exc())})
            raise
    user,tenant=str(uuid4()),str(uuid4());password=secrets.token_urlsafe(32);email=user+'@regression.invalid'
    summaries=[]
    try:
        with transaction() as conn:
            conn.execute('insert into users(id,email,password_hash) values(%s,%s,%s)',(user,email,password_hash.hash(password)))
            conn.execute('insert into tenants(id,name) values(%s,%s)',(tenant,args.run_id))
            conn.execute('insert into memberships values(%s,%s,%s)',(tenant,user,'admin'))
            jobs_before=conn.execute('select count(*) n from jobs').fetchone()['n']
    except Exception as error:
        run.write('bootstrap-error.json',{'time':now(),'error_type':type(error).__name__,'message':run.safe(error)})
        run.write('run-audit.json',{'time':now(),'complete':False,'cases_recorded':0,'cases_expected':len(cases),'advertising_calls':0})
        run.seal()
        raise
    try:
        with patch('app.executor.call',run.forbidden_ad_call),patch('app.executor.call_binding',run.forbidden_ad_call),patch('app.main.connect',run.forbidden_ad_call):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed,raise_app_exceptions=False),base_url='https://regression.invalid',timeout=210) as client:
                login=await client.post('/auth/login',json={'email':email,'password':password})
                if login.status_code!=200:raise RuntimeError('Test user authentication failed')
                client.headers['Authorization']='Bearer '+login.json()['access_token']
                for case in cases:
                    CASE.set(case);(run.raw/case).mkdir(mode=0o700);counter=0;project_id=task_id=None
                    summary={'case':case,'normal_action_attempts':[],'normal_action_successes':[],
                             'manual_mapping_changes':0,'manual_field_changes':0,'extra_answers':0,
                             'stage_errors':[],'file_errors':[]}
                    async def call(stage,method,path,**kwargs):
                        nonlocal counter
                        STAGE.set(stage);counter+=1;prefix=Path('raw')/case/f'{counter:02d}-{stage}'
                        request={'method':method,'path':path,'json':kwargs.get('json'),'params':kwargs.get('params')}
                        if 'files' in kwargs:request['files']={k:{'filename':v[0],'bytes':len(v[1]),'sha256':hashlib.sha256(v[1]).hexdigest()} for k,v in kwargs['files'].items()}
                        started=time.monotonic();response=await client.request(method,path,**kwargs)
                        with (run.output/(str(prefix)+'.body')).open('xb') as f:f.write(response.content)
                        run.write(str(prefix)+'.json',{'time':now(),'elapsed_seconds':time.monotonic()-started,'request':request,
                                                    'status':response.status_code,'response_sha256':hashlib.sha256(response.content).hexdigest()})
                        if response.status_code>=400:summary['stage_errors'].append({'stage':stage,'status':response.status_code,'body_file':str(prefix)+'.body'})
                        try:body=response.json()
                        except ValueError:body=None
                        return response.status_code,body
                    try:
                        async with asyncio.timeout(650):
                            status,project=await call('create-project','POST','/projects',json={'tenant_id':tenant,'name':args.run_id+' '+case,'account_id':contexts[case]['account_id']})
                            if status!=200:raise RuntimeError('Project setup failed')
                            project_id=project['id'];summary['project_id']=project_id;summary['account_setup']=contexts[case]
                            status,task=await call('create-task','POST',f'/projects/{project_id}/tasks',json={'name':args.run_id+' '+case})
                            if status!=200:raise RuntimeError('Task setup failed')
                            task_id=task['id'];summary['task_id']=task_id;file_id=None
                            for kind,name in (('export','export.xlsx'),('requirements','brief.xlsx')):
                                status,uploaded=await call('upload-'+kind,'POST',f'/projects/{project_id}/files',params={'kind':kind,'task_id':task_id},files={'file':(name,(inputs/case/name).read_bytes())})
                                if status==200:
                                    if uploaded.get('file_error'):summary['file_errors'].append({'kind':kind,**uploaded['file_error']})
                                    if kind=='requirements':file_id=uploaded['id']
                            _,state=await call('state-before-paste','GET','/tasks/'+task_id)
                            await call('paste','POST',f'/projects/{project_id}/chat',json={'task_id':task_id,'request_id':str(uuid4()),'expected_version':state['task']['version'],
                                       'expected_draft_version':(state.get('draft') or {}).get('version'),'action':'message','message':(inputs/case/'paste.txt').read_text(encoding='utf-8-sig')})
                            if file_id:
                                status,mapping=await call('mapping-propose','POST',f'/files/{file_id}/mapping/propose')
                                if status==200 and mapping.get('mappings') and mapping.get('complete',True):
                                    summary['normal_action_attempts'].append('confirm-unchanged-mapping')
                                    status,draft=await call('mapping-confirm-unchanged','POST',f'/files/{file_id}/mapping/confirm',json={'mappings':mapping['mappings'],'expected_task_version':mapping['expected_task_version'],'request_id':str(uuid4())})
                                    if status==200:
                                        summary['normal_action_successes'].append('confirm-unchanged-mapping');summary['draft_id']=draft['id']
                                        await call('preview-before-analysis','GET',f"/drafts/{draft['id']}/preview")
                                        _,state=await call('state-before-analysis','GET','/tasks/'+task_id)
                                        await call('analyze','POST',f'/projects/{project_id}/chat',json={'task_id':task_id,'request_id':str(uuid4()),'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],'action':'analyze'})
                                        _,state=await call('state-after-analysis','GET','/tasks/'+task_id)
                                        pending=state['task'].get('proposal')
                                        if pending and pending['status']=='pending':
                                            summary['normal_action_attempts'].append('accept-unchanged-proposal')
                                            status,_=await call('accept-unchanged-proposal','POST',f'/projects/{project_id}/chat',json={'task_id':task_id,'request_id':str(uuid4()),'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],'action':'accept','proposal_id':pending['id']})
                                            if status==200:summary['normal_action_successes'].append('accept-unchanged-proposal')
                            _,state=await call('sealed-task-state','GET','/tasks/'+task_id)
                            if state.get('draft'):
                                status,preview=await call('final-preview','GET',f"/drafts/{state['draft']['id']}/preview")
                                if status==200:summary.update(draft_id=state['draft']['id'],parsed_requirements=len(preview['rows']),selected_ids=preview['selected_ids'],counts=preview['counts'],can_confirm=preview['can_confirm'],approval_valid=preview['approval_valid'])
                    except Exception as error:
                        summary['runner_error']={'type':type(error).__name__,'message':run.safe(error)}
                    finally:
                        if project_id:
                            with transaction() as conn:
                                observation={table:conn.execute('select count(*) n from '+table+' where project_id=%s',(project_id,)).fetchone()['n'] for table in ('runs','deployments')}
                                observation['reserved']=str(conn.execute('select reserved from projects where id=%s',(project_id,)).fetchone()['reserved'])
                                observation['jobs_delta']=conn.execute('select count(*) n from jobs').fetchone()['n']-jobs_before
                            observation['advertising_calls']=run.ad_calls;summary['safety']=observation
                            if observation!={'runs':0,'deployments':0,'reserved':'0.0000','jobs_delta':0,'advertising_calls':0}:
                                summary['safety_failure']=True
                        run.write(Path('raw')/case/'summary.json',summary);summaries.append(summary)
                        print(json.dumps({'case':case,'recorded':True,'selected':len(summary.get('selected_ids',[])),'stage_errors':len(summary['stage_errors'])}),flush=True)
                        if summary.get('safety_failure'):raise RuntimeError('Advertising safety observation failed')
        run.write('run-complete.json',{'time':now(),'tenant_id':tenant,'cases':summaries,'execution_enabled':False,'label':'exposed_regression','run_id':args.run_id,'parent_run':args.parent_run})
    finally:
        mismatch=verify_sources(source_root,manifest)
        input_mismatch=[p for p,h in input_hashes.items() if not (inputs/p).is_file() or digest(inputs/p)!=h]
        safety_failure=run.ad_calls!=0 or any(s.get('safety_failure') for s in summaries)
        run.write('run-audit.json',{'time':now(),'source_mismatches_after':mismatch,'input_mismatches_after':input_mismatch,
                  'advertising_calls':run.ad_calls,'cases_recorded':len(summaries),'cases_expected':len(cases),
                  'complete':len(summaries)==len(cases) and not mismatch and not input_mismatch and not safety_failure})
        run.seal()
        if mismatch or input_mismatch:raise RuntimeError('Sources or inputs changed during regression')
    print('EXPOSED_REGRESSION_OUTPUTS_SEALED',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--freeze',required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--parent-run',required=True)
    parser.add_argument('--env-file')
    asyncio.run(main(parser.parse_args()))
