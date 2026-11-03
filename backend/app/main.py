from .public import public_business, public_agent_state
import json
from uuid import uuid4
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from .settings import settings
from .auth import authenticate, Actor, project_access
from .db import transaction, json_value
from .models import SheetMapping, BuildDraft, DraftPatch, ProjectPolicy, Model, SourceRef, Requirement
from .excel import inspect_workbook, parse_export, parse_requirements, rebind_mapping
from .rules import evaluate
from .service import history_for, edit_draft, submit_approved_draft, pending_questions
from . import storage
from .agent import propose_mapping, conversation_graph, ModelServiceError
import asyncio
import os
from urllib.parse import urlsplit
from pydantic import ValidationError
from .tiktok import EncryptedStorage, connect

oauth_tasks = set()

app = FastAPI(title="Adbuild API",version="0.1.0",root_path=os.getenv('ROOT_PATH',''))
from .intake import router as intake_router
app.include_router(intake_router)
from .ops import router as ops_router
app.include_router(ops_router)
web=urlsplit(os.getenv('APP_ORIGIN','http://localhost:3000'))
app.add_middleware(CORSMiddleware,allow_origins=[f'{web.scheme}://{web.netloc}',"http://127.0.0.1:3000"],allow_methods=["*"],allow_headers=["Authorization","Content-Type"])


@app.get('/config')
def public_config():
    try: cfg=settings()
    except ValidationError as error:
        return {'configured':False,'missing':[str(e['loc'][0]) for e in error.errors()]}
    return {'configured':True,'missing':[],'model_configured':bool(cfg.llm_api_key),'model':cfg.llm_model,'execution_enabled':cfg.execution_enabled}

class LoginBody(BaseModel):
    email:str
    password:str

@app.post('/auth/login')
def login(body:LoginBody):
    from .auth import password_hash
    from itsdangerous import URLSafeTimedSerializer
    from time import sleep
    with transaction() as c:
        user=c.execute('select * from users where email=%s and active=true',(body.email.lower(),)).fetchone()
    if not user or not password_hash.verify(body.password,user['password_hash']):
        sleep(1)
        raise HTTPException(401,'邮箱或密码不正确')
    from .auth import credential_version
    token=URLSafeTimedSerializer(settings().session_secret,salt='adbuild-session').dumps({'user_id':user['id'],'credential_version':credential_version(user['password_hash'])})
    return {'access_token':token,'expires_in':86400}


@app.get('/projects/{project_id}/connection')
def connection_status(project_id:str,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        p=project_access(c,project_id,actor)
        row=c.execute("select state,verified,tools from connections where tenant_id=%s",(p['tenant_id'],)).fetchone()
    if not row: return {'status':'disconnected','verified':False}
    return {'status':row['state'].get('status','disconnected'),'verified':row['verified'],
        'authorization_url':row['state'].get('authorization_url') if p['role']=='admin' else None,
        'tool_count':len(row['tools'])}


@app.post('/projects/{project_id}/connection')
async def start_connection(project_id:str,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        p=project_access(c,project_id,actor,admin=True)
    store=EncryptedStorage(str(p['tenant_id']))
    store.update(status='starting',code=None,authorization_url=None)
    task=asyncio.create_task(connect(str(p['tenant_id'])))
    oauth_tasks.add(task)
    task.add_done_callback(oauth_tasks.discard)
    return {'status':'starting'}


@app.get('/oauth/tiktok/callback')
def oauth_callback(code:str,state:str):
    with transaction() as c:
        row=c.execute("select tenant_id from connections where JSON_UNQUOTE(JSON_EXTRACT(state,'$.oauth_state'))=%s and JSON_UNQUOTE(JSON_EXTRACT(state,'$.status'))='authorizing'",(state,)).fetchone()
        if not row: raise HTTPException(400,'授权状态无效或已过期')
    EncryptedStorage(str(row['tenant_id'])).update(code=code)
    return RedirectResponse(settings().app_origin)


from fastapi.exceptions import RequestValidationError


def error_detail(error):
    if isinstance(error,(ValidationError,RequestValidationError)):
        return [{key:item[key] for key in ('loc','type','msg') if key in item} for item in error.errors()]
    return str(error)


@app.exception_handler(RequestValidationError)
@app.exception_handler(ValueError)
async def invalid(_,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422,content={'detail':error_detail(exc)})


@app.exception_handler(ModelServiceError)
async def model_service_error(_,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code,content={'detail':exc.safe_message,**exc.public_error()})


@app.get('/health')
def health():
    return {"status":"ok","version":"0.1.0"}


@app.get('/projects')
def projects(actor:Actor=Depends(authenticate)):
    with transaction() as c:
        return c.execute("select p.*,m.role,t.name tenant_name from projects p join memberships m on m.tenant_id=p.tenant_id join tenants t on t.id=p.tenant_id where m.user_id=%s order by p.created_at",(actor.user_id,)).fetchall()


class NewProject(BaseModel):
    tenant_id: str
    name: str
    account_id: str


@app.post('/projects')
def new_project(body:NewProject,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        member=c.execute("select role from memberships where tenant_id=%s and user_id=%s",(body.tenant_id,actor.user_id)).fetchone()
        if not member or member['role']!='admin':
            raise HTTPException(403,"只有管理员可以创建项目")
        project_id=str(uuid4())
        c.execute("insert into projects(id,tenant_id,name,account_id) values(%s,%s,%s,%s)",(project_id,body.tenant_id,body.name,body.account_id))
        return c.execute("select * from projects where id=%s",(project_id,)).fetchone()


@app.get('/projects/{project_id}/workspace')
def workspace(project_id:str,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        project=project_access(c,project_id,actor)
        return public_business({"tasks":[{k:r[k] for k in ("id","draft_id","version","data")} for r in c.execute("select id,draft_id,version,data from intake_tasks where project_id=%s order by created_at desc",(project_id,))],"project":project,"files":c.execute("select id,name,kind,inspection,mapping from files where project_id=%s order by created_at desc",(project_id,)).fetchall(),
            "drafts":[r['data'] for r in c.execute("select data from drafts where project_id=%s order by created_at desc",(project_id,))],
            "runs":c.execute("select id,status,error,created_at from runs where project_id=%s order by created_at desc",(project_id,)).fetchall(),
            "messages":c.execute("select role,content from messages where project_id=%s order by created_at",(project_id,)).fetchall()})


@app.post('/projects/{project_id}/files')
async def upload_file(project_id:str,kind:str,task_id:str,file:UploadFile=File(),actor:Actor=Depends(authenticate)):
    if kind not in ('requirements','export') or not file.filename or not file.filename.lower().endswith('.xlsx'):
        raise HTTPException(422,"请选择 .xlsx 需求文件或 ExportAds")
    with transaction() as c:
        project=project_access(c,project_id,actor,write=True)
        from .intake import access
        task,_=access(c,task_id,actor,write=True)
        if task.project_id!=project_id: raise HTTPException(404,"任务不属于项目")
    data=await file.read(20*1024*1024+1)
    from .excel import WorkbookReadError
    file_error=None
    try:
        inspection=inspect_workbook(data)
        records=parse_export(data) if kind=='export' else []
    except WorkbookReadError as error:
        from hashlib import sha256
        file_error={'code':error.code,'message':str(error),'recoverable':True}
        inspection={'sha256':sha256(data).hexdigest(),'sheets':[],'error':file_error}
        records=[]
    file_id=str(uuid4())
    path=f"{project['tenant_id']}/{project_id}/{file_id}.xlsx"
    await storage.upload(path,data)
    with transaction() as c:
        project_access(c,project_id,actor,write=True)
        c.execute("insert into files(id,tenant_id,project_id,name,kind,sha256,storage_path,inspection) values(%s,%s,%s,%s,%s,%s,%s,%s)",(file_id,project['tenant_id'],project_id,file.filename,kind,inspection['sha256'],path,json_value(inspection)))
        from .intake import access,save
        task,_=access(c,task_id,actor,write=True)
        task.file_ids.append(file_id);task.version+=1;task.lease=None;task.proposal=None
        task.stage='attention' if file_error else 'mapping' if kind=='requirements' else task.stage
        if file_error: task.analysis.setdefault('file_errors',{})[file_id]=file_error
        save(c,task)
        for r in records:
            r['source_file']=file_id
            r['imported_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
            r['account_id']=project['account_id']
            c.execute("insert into history(tenant_id,project_id,account_id,ad_id,data) values(%s,%s,%s,%s,%s) on duplicate key update data=values(data),account_id=values(account_id)",(project['tenant_id'],project_id,project['account_id'],r['ad_id'],json_value(r)))
    return {"id":file_id,"file_error":file_error,"inspection":inspection,"imported_ads":len(records),"campaigns":len({r['campaign_id'] for r in records}),"adgroups":len({r['adgroup_id'] for r in records})}


@app.post('/files/{file_id}/mapping/propose')
async def mapping_proposal(file_id:str,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        row=c.execute("select * from files where id=%s",(file_id,)).fetchone()
        if not row: raise HTTPException(404,"文件不存在")
        project_access(c,row['project_id'],actor,write=True)
        linked=c.execute("select id from intake_tasks where project_id=%s and JSON_CONTAINS(data,%s,'$.file_ids')",(row['project_id'],json.dumps(file_id))).fetchone()
        if not linked: raise HTTPException(422,'请在搭建任务中上传需求文件')
        from .intake import access
        task,_=access(c,linked['id'],actor,write=True)
        task_version=task.version
        templates=c.execute("select data from mapping_templates where project_id=%s and JSON_TYPE(data)='OBJECT'",(row['project_id'],)).fetchall()
    if row['inspection'].get('error'): raise HTTPException(422,{'code':'file_unreadable','file_id':file_id,**row['inspection']['error']})
    from .templates import reuse
    data=await storage.download(row['storage_path'])
    matches=[m for t in templates if (m:=reuse(t['data'],row['inspection'],data))]
    unique={json.dumps([m.model_dump() for m in group],sort_keys=True):group for group in matches}
    if len(unique)==1:
        maps=next(iter(unique.values()))
        return {'mappings':[m.model_dump() for m in maps],'explanation':'已按确认过的表头语义复用列，并重新检测数据范围。','reused':True,'expected_task_version':task_version}
    template_review_required=bool(templates)
    from datetime import datetime,timezone,timedelta
    from .intake import save
    lease_id=str(uuid4())
    with transaction() as c:
        current,_=access(c,task.id,actor,write=True)
        if current.version!=task_version: raise HTTPException(409,'分析期间任务已变化')
        if current.lease and datetime.fromisoformat(current.lease['until'])>datetime.now(timezone.utc): raise HTTPException(409,'任务正在分析')
        current.lease={'request_id':lease_id,'until':(datetime.now(timezone.utc)+timedelta(seconds=210)).isoformat()}
        save(c,current)
    if task.analysis.get('mapping',{}).get('file_id')!=file_id: task.analysis.pop('mapping',None)
    def persist_progress(proposal):
        with transaction() as c:
            current,_=access(c,task.id,actor,write=True)
            if current.version!=task_version or not current.lease or current.lease['request_id']!=lease_id: raise HTTPException(409,'分析期间任务已变化')
            current.analysis['mapping']={'file_id':file_id,'proposal':proposal.model_dump(mode='json'),'base_version':task_version,
                                         'template_review_required':template_review_required}
            save(c,current)
    try:
        proposal=await asyncio.wait_for(propose_mapping(row['inspection'],data,task=task,on_progress=persist_progress),180)
        if template_review_required:
            proposal.explanation='已确认模板无法通过本次来源、变换或唯一性校验，以下为重新提议的映射，请审核。'+proposal.explanation
        persist_progress(proposal)
    except BaseException as error:
        public_error=ModelServiceError('analysis_timeout',retryable=True) if isinstance(error,TimeoutError) else error if isinstance(error,ModelServiceError) else None
        with transaction() as c:
            current,_=access(c,task.id,actor,write=True)
            if current.lease and current.lease['request_id']==lease_id:
                current.lease=None;current.stage='attention'
                current.analysis['mapping_error']={'type':type(error).__name__,'resume_available':True}
                if public_error: current.analysis['mapping_error'].update(public_error.public_error())
                save(c,current)
        if isinstance(error,TimeoutError): raise public_error from None
        raise
    with transaction() as c:
        current,_=access(c,task.id,actor,write=True)
        if current.version!=task_version or not current.lease or current.lease['request_id']!=lease_id: raise HTTPException(409,'分析期间任务已变化')
        current.lease=None;current.stage='mapping';current.analysis.pop('mapping_error',None);save(c,current)
    return {**proposal.model_dump(mode='json'),'expected_task_version':task_version,'template_review_required':template_review_required}


def signature(inspection):
    import hashlib
    headers=[{'sheet':s['sheet'],'cols':s['cols'],'sample':s['sample'][:2]} for s in inspection['sheets']]
    return hashlib.sha256(json.dumps(headers,sort_keys=True).encode()).hexdigest()


class MappingConfirmation(Model):
    mappings:list[SheetMapping]=Field(min_length=1)
    expected_task_version:int
    request_id:str


@app.post('/files/{file_id}/mapping/confirm')
async def confirm_mapping(file_id:str,body:MappingConfirmation,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        row=c.execute("select * from files where id=%s",(file_id,)).fetchone()
        if not row or row['kind']!='requirements': raise HTTPException(404,"需求文件不存在")
        project=project_access(c,row['project_id'],actor,write=True)
        history=history_for(c,project)
    data=await storage.download(row['storage_path'])
    requirements=parse_requirements(data,file_id,body.mappings)
    for req in requirements:
        if not req.account_id and not any(i.field=='account_id' and i.code.startswith('invalid_') for i in req.issues):
            req.account_id=project['account_id']
            req.sources['account_id']=SourceRef(file_id='',sheet='',cell='',raw_value=project['account_id'],kind='template')
    requirements=[evaluate(r,history) for r in requirements]
    draft=BuildDraft(id=str(uuid4()),project_id=str(project['id']),requirements=requirements,summary={"source_file":file_id,"count":len(requirements)})
    with transaction() as c:
        project_access(c,row['project_id'],actor,write=True)
        from .intake import access,save,refresh
        taskrow=c.execute("select id from intake_tasks where project_id=%s and JSON_CONTAINS(data,%s,'$.file_ids')",(project['id'],json.dumps(file_id))).fetchone()
        if not taskrow: raise HTTPException(422,'文件未关联搭建任务')
        task,_=access(c,taskrow['id'],actor,write=True)
        from hashlib import sha256
        request_digest=sha256(body.model_dump_json().encode()).hexdigest()
        previous=task.requests.get(body.request_id)
        if previous:
            if previous['digest']!=request_digest: raise HTTPException(409,'映射请求 ID 已用于其他内容')
            return public_business(previous['result'])
        if task.version!=body.expected_task_version: raise HTTPException(409,'任务已变化，请重新识别和审核映射')
        proposed=task.analysis.get('mapping',{})
        if proposed.get('file_id')==file_id and not proposed.get('proposal',{}).get('complete',True):
            raise HTTPException(422,'映射分析尚未完成，请继续分析剩余区域')
        from .scope import select_requirements
        draft.requirements=select_requirements(draft.requirements,task.scope,task.messages)
        if task.scope.status=='resolved' and task.scope.action in ('new','repost') and not any(r.included for r in draft.requirements):
            from .models import Clarification
            task.scope.status='unresolved'
            task.semantic_questions=[q for q in task.semantic_questions if q.field!='scope']+[Clarification(id=str(uuid4()),field='scope',scope='task',question='当前范围条件没有匹配记录，请核对编号、帖子或名称，或在预览中明确选择本批需求。')]
            task.analysis['scope_diagnostic']='没有记录同时满足范围条件；范围尚未确定'


        from .models import InputField
        context=task.context
        if (not context.account_ids or project['account_id'] in context.account_ids):
            for req in draft.requirements:
                if context.objectives and req.objective not in context.objectives: continue
                for field,value in context.defaults.items():
                    if getattr(req,field) in (None,'') and not any(i.field==field and i.code.startswith('invalid_') for i in req.issues):
                        from .excel import normalize_value
                        setattr(req,field,normalize_value(field,value,req.timezone))
                        req.sources[field]=SourceRef(file_id='',sheet='',cell='',raw_value=value,kind='template',context_version=context.version)
            draft.requirements=[evaluate(Requirement.model_validate(r.model_dump()),history) for r in draft.requirements]

        if task.draft_id:
            old=c.execute('select version,data from drafts where id=%s for update',(task.draft_id,)).fetchone()
            from .merge import merge_requirements
            draft.requirements=[evaluate(r,history) for r in merge_requirements(BuildDraft.model_validate(old['data']).requirements,draft.requirements)]
            draft.id=task.draft_id;draft.version=old['version']+1
            c.execute('update drafts set version=%s,data=%s where id=%s',(draft.version,json_value(draft),draft.id))
        else:
            c.execute("insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,%s,%s)",(draft.id,project['tenant_id'],project['id'],draft.version,json_value(draft)))
        task.draft_id=draft.id;task.version+=1;task.proposal=None;task.lease=None;refresh(task,draft)
        task.requests[body.request_id]={'digest':request_digest,'result':draft.model_dump(mode='json')}
        save(c,task)
        from .templates import capture
        try: maps=capture(body.mappings,row['inspection'],data)
        except ValueError: maps=None
        c.execute("update files set mapping=%s where id=%s",(json_value([m.model_dump() for m in body.mappings]),file_id))
        if maps:
            c.execute("insert into mapping_templates(tenant_id,project_id,signature,data) values(%s,%s,%s,%s) on duplicate key update data=values(data)",(project['tenant_id'],project['id'],signature(row['inspection']),json_value(maps)))
    return public_business(draft)


class StructureProposalBody(Model):
    expected_version: int
    requirement_id: str
    structure: dict


@app.post('/drafts/{draft_id}/structure-proposal')
def propose_structure(draft_id:str,body:StructureProposalBody,actor:Actor=Depends(authenticate)):
    from .models import StructureSpec,ChangeProposal,ChatEdit
    from .structure import validate_structure
    from .intake import access,save
    with transaction() as c:
        draft,project=draft_access(c,draft_id,actor,write=True)
        if draft.version!=body.expected_version: raise HTTPException(409,'草稿已变化')
        req=next((r for r in draft.requirements if r.id==body.requirement_id),None)
        if not req: raise HTTPException(422,'未知需求')
        raw=body.structure
        if req.structure:
            existing={a.id:a for g in req.structure.groups for a in g.ads}
            for group in raw.get('groups',[]):
                for ad in group.get('ads',[]):
                    old=existing.get(ad.get('id'))
                    if old and old.kind=='authorization_code' and ad.get('kind')==old.kind and not ad.get('authorization_code'):
                        ad['authorization_code']=old.authorization_code
        structure=StructureSpec.model_validate(raw)
        candidate=req.model_copy(update={'structure':structure})
        issues=validate_structure(candidate)
        if issues: raise HTTPException(422,[i.model_dump() for i in issues])
        row=c.execute('select id from intake_tasks where draft_id=%s',(draft_id,)).fetchone()
        if not row: raise HTTPException(422,'请在搭建任务中编辑结构')
        task,_=access(c,row['id'],actor,write=True)
        message_id=str(uuid4())
        task.messages.append({'id':message_id,'role':'user','content':'提交分组结构供审核','created_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()})
        task.proposal=ChangeProposal(id=str(uuid4()),draft_version=draft.version,edits=[ChatEdit(requirement_id=req.id,values={'structure':structure.model_dump(mode='json')})],reason='分组结构已校验，请采纳后核对完整预览',message_id=message_id)
        task.version+=1;task.lease=None;save(c,task)
        return public_business({'task_id':task.id,'proposal':task.proposal})


@app.post('/drafts/{draft_id}/edit')
def patch_draft(draft_id:str,body:DraftPatch,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        linked=c.execute('select id from intake_tasks where draft_id=%s',(draft_id,)).fetchone()
        message_id=str(uuid4()) if linked else None
        draft=edit_draft(c,draft_id,actor,body.expected_version,[{**e.model_dump(),'source_message_id':message_id} for e in body.edits])
        project=project_access(c,draft.project_id,actor,write=True)
        linked=c.execute('select id from intake_tasks where draft_id=%s',(draft_id,)).fetchone()
        if linked:
            from .intake import access,save,refresh
            task,_=access(c,linked['id'],actor,write=True)
            task.version+=1;task.proposal=None;task.lease=None
            changed={(e.requirement_id,f) for e in body.edits for f in e.values}
            task.semantic_questions=[q for q in task.semantic_questions if not any((rid,q.field) in changed for rid in q.requirement_ids)]
            task.messages.append({'id':message_id,'role':'user','content':'人工明确修改草稿：'+json.dumps([e.model_dump() for e in body.edits],ensure_ascii=False)})
            if any('included' in e.values for e in body.edits):
                from .models import TaskScope,ScopeSelector
                selected_ids=[r.id for r in draft.requirements if r.included]
                task.scope=TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='requirement_id',values=selected_ids)],message_ids=[message_id],reason='用户在预览中明确选择本批需求') if selected_ids else TaskScope(reason='用户取消了全部选择')
                task.semantic_questions=[q for q in task.semantic_questions if q.field!='scope']
            refresh(task,draft);save(c,task)
        return public_business(draft)


def draft_access(c,draft_id,actor,write=False):
    row=c.execute('select * from drafts where id=%s for update',(draft_id,)).fetchone()
    if not row: raise HTTPException(404,'草稿不存在')
    project=project_access(c,row['project_id'],actor,write=write)
    return BuildDraft.model_validate(row['data']),project

@app.get('/drafts/{draft_id}/preview')
def preview_draft(draft_id:str,actor:Actor=Depends(authenticate)):
    from .preview import build_preview
    with transaction() as c:
        draft,project=draft_access(c,draft_id,actor)
        result=build_preview(draft,history_for(c,project),project['policy_version'],pending_questions=pending_questions(c,draft.id),expected_account_id=project['account_id'])
        result['confirmation_history']=[r['data'] for r in c.execute("select data from audit where project_id=%s and event='build_confirmed' and JSON_UNQUOTE(JSON_EXTRACT(data,'$.draft_id'))=%s order by id desc",(project['id'],draft_id))]
    return public_business(result)

class BuildConfirmation(Model):
    expected_version:int
    digest:str
    acknowledged:bool

@app.get('/projects/{project_id}/history')
def query_history(project_id:str,task_id:str,offset:int=0,limit:int=25,actor:Actor=Depends(authenticate)):
    if offset<0 or offset>100000 or limit<1 or limit>100: raise HTTPException(422,'分页范围无效')
    from .intake import access
    from .history_query import lookup_history
    with transaction() as c:
        project=project_access(c,project_id,actor)
        task,_=access(c,task_id,actor)
        if task.project_id!=project_id: raise HTTPException(404,'任务不属于当前项目')
        if task.scope.action!='history': raise HTTPException(409,'任务意图已变化，请重新明确历史查询范围')
        requirements=[]
        if task.draft_id:
            stored=c.execute('select data from drafts where id=%s and project_id=%s',(task.draft_id,project_id)).fetchone()
            if stored: requirements=stored['data'].get('requirements',[])
        result=lookup_history(history_for(c,project),task.scope,requirements,limit=offset+limit)
        result['items']=result['items'][offset:offset+limit]
        result.update(offset=offset,task_version=task.version)
        result['coverage']={'returned':len(result['items']),'total':result['total'],'complete':not result['requires_scope'] and offset+limit>=result['total']}
        return public_business(result)


@app.get('/drafts/{draft_id}/requirements/{requirement_id}/history')
def page_history(draft_id:str,requirement_id:str,offset:int=0,limit:int=25,actor:Actor=Depends(authenticate)):
    if offset<0 or limit<1 or limit>100: raise HTTPException(422,'分页范围无效')
    with transaction() as c:
        draft,project=draft_access(c,draft_id,actor)
        req=next((r for r in draft.requirements if r.id==requirement_id),None)
        if not req: raise HTTPException(404,'需求不存在')
        req=evaluate(req,history_for(c,project))
        return public_business({'items':req.history[offset:offset+limit],'total':len(req.history),'offset':offset,'complete':offset+limit>=len(req.history)})


@app.post('/drafts/{draft_id}/confirm')
def confirm_build(draft_id:str,body:BuildConfirmation,actor:Actor=Depends(authenticate)):
    from .preview import build_preview,approve_preview
    with transaction() as c:
        draft,project=draft_access(c,draft_id,actor,write=True)
        preview=build_preview(draft,history_for(c,project),project['policy_version'],pending_questions=pending_questions(c,draft.id),expected_account_id=project['account_id'])
        previous=draft.approval
        approve_preview(draft,preview,actor.user_id,body.expected_version,body.digest,body.acknowledged)
        c.execute('update drafts set data=%s where id=%s',(json_value(draft),draft_id))
        if previous!=draft.approval:
            c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,'build_confirmed',%s)",(project['tenant_id'],project['id'],json_value({'draft_id':draft.id,'version':draft.version,'digest':body.digest,'actor':actor.user_id,'approval':draft.approval.model_dump(mode='json')})))
    return public_business(draft)

@app.post('/drafts/{draft_id}/execute')
def execute_build(draft_id:str,body:BuildConfirmation,actor:Actor=Depends(authenticate)):
    from .preview import build_preview
    with transaction() as c:
        draft,project=draft_access(c,draft_id,actor,write=True)
        preview=build_preview(draft,history_for(c,project),project['policy_version'],pending_questions=pending_questions(c,draft.id),expected_account_id=project['account_id'])
        if draft.version!=body.expected_version or preview['digest']!=body.digest or not body.acknowledged:
            raise HTTPException(409,'预览已变化，请重新确认')
        run_id=submit_approved_draft(c,draft,project)
    return {'run_id':run_id}


@app.put('/projects/{project_id}/policy')
def put_policy(project_id:str,body:ProjectPolicy,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        project=project_access(c,project_id,actor,admin=True)
        row=c.execute("select * from projects where id=%s for update",(project_id,)).fetchone()
        if body.version != row['policy_version']+1:
            raise HTTPException(409,"授权版本已变化")
        if body.cumulative_limit < row['reserved']:
            raise HTTPException(422,"额度不能小于已预占金额")
        c.execute("update projects set policy=%s,policy_version=%s where id=%s",(json_value(body),body.version,project_id))
        c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,'policy_updated',%s)",(project['tenant_id'],project_id,json_value({"version":body.version,"actor":actor.user_id})))
    return body


@app.get('/runs/{run_id}')
def get_run(run_id:str,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        row=c.execute("select * from runs where id=%s",(run_id,)).fetchone()
        if not row: raise HTTPException(404,"任务不存在")
        project_access(c,row['project_id'],actor)
        return public_business({**row,"deployments":c.execute("select status,steps,requirement from deployments where run_id=%s",(run_id,)).fetchall()})


@app.post('/runs/{run_id}/{action}')
def run_action(run_id:str,action:str,actor:Actor=Depends(authenticate)):
    if action not in ('cancel','resume','pause'): raise HTTPException(404)
    with transaction() as c:
        row=c.execute("select * from runs where id=%s for update",(run_id,)).fetchone()
        if not row: raise HTTPException(404)
        project_access(c,row['project_id'],actor,write=True)
        if action=='cancel':
            c.execute("update runs set cancelled=true where id=%s",(run_id,))
        else:
            if not settings().execution_enabled: raise HTTPException(423,'真实搭建暂缓')
            c.execute("insert into jobs(message) values(%s)",(json_value({"run_id":run_id,"action":action}),))
    return {"accepted":True}


from .intake import TaskAction as ChatBody, act


@app.post('/projects/{project_id}/chat')
async def chat(project_id:str,body:ChatBody,actor:Actor=Depends(authenticate)):
    try:
        return await asyncio.wait_for(act(project_id,body,actor),180)
    except TimeoutError:
        raise ModelServiceError('analysis_timeout',retryable=True) from None


@app.post('/projects/{project_id}/agent')
async def agent_stream(project_id:str,request:Request,actor:Actor=Depends(authenticate)):
    from ag_ui.core import RunAgentInput
    from ag_ui.encoder import EventEncoder
    from ag_ui_langgraph import LangGraphAgent
    from langgraph.graph import StateGraph, MessagesState, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import AIMessage
    from fastapi.responses import StreamingResponse
    # Never accept tenant identity, authoritative draft or a reusable graph thread from the browser.
    input_data=RunAgentInput.model_validate(await request.json())
    props=input_data.forwarded_props or {}
    body=ChatBody.model_validate(props)
    with transaction() as c:
        p=project_access(c,project_id,actor,write=True)
    class TransportState(MessagesState):
        response:dict
        draft:dict
    async def process(_):
        result=await chat(project_id,body,actor)
        safe=public_agent_state(result,result.get('draft'),body.request_id)
        return {'messages':[AIMessage(content=safe['response']['answer'])],**safe}
    builder=StateGraph(TransportState)
    builder.add_node('process',process);builder.add_edge(START,'process');builder.add_edge('process',END)
    # The official AG-UI adapter reads graph state before executing a frame.
    # This saver belongs only to this HTTP request; business recovery stays in MySQL.
    transport=LangGraphAgent(name='adbuild',graph=builder.compile(checkpointer=InMemorySaver()))
    input_data.thread_id=f"{p['tenant_id']}:{body.task_id}:{body.request_id}"
    input_data.state={}
    encoder=EventEncoder(accept=request.headers.get('accept'))
    async def events():
        async for event in transport.run(input_data):
            yield encoder.encode(event)
    return StreamingResponse(events(),media_type=encoder.get_content_type())
