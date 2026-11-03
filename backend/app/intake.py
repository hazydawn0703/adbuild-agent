"""Task-scoped interaction state. Draft values remain in drafts, never in checkpoints."""
import json
import asyncio
from hashlib import sha256
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from .auth import Actor, authenticate, project_access
from .db import transaction, json_value
from .models import (Model, IntakeTask, ProjectContext, BuildDraft, FieldEvidence, EvidenceRef,
                     Clarification, ChangeProposal, ChatDecision, ChatEdit)
from .service import edit_draft, EDITABLE, history_for, pending_questions
from .rules import evaluate

router=APIRouter()


def access(c,task_id,actor,write=False):
    row=c.execute('select * from intake_tasks where id=%s for update',(task_id,)).fetchone()
    if not row: raise HTTPException(404,'搭建任务不存在')
    project=project_access(c,row['project_id'],actor,write=write)
    task=IntakeTask.model_validate(row['data'])
    if write and task.archived_at: raise HTTPException(409,'任务已归档，请先恢复')
    return task,project


def save(c,task):
    c.execute('update intake_tasks set version=%s,draft_id=%s,data=%s where id=%s',
              (task.version,task.draft_id,json_value(task),task.id))


def refresh(task,draft):
    if draft is None:
        task.questions=list(task.semantic_questions)
        task.stage='clarifying' if task.questions else 'intake'
        return
    evidence=[]; grouped={}
    for req in draft.requirements:
        for field in ('account_id','post_id','objective','budget','budget_mode','currency','start_at','end_at','timezone','audience','language','identity_id','cta','landing_url'):
            source=req.sources.get(field)
            value=getattr(req,field)
            state='missing' if value in (None,'') else 'inferred' if source and source.kind=='history' and field not in req.confirmed_fields else 'explicit'
            conflicts=[q for q in task.semantic_questions if q.field==field and req.id in q.requirement_ids]
            if conflicts and state!='missing': state='conflict'
            refs=[]
            if source:
                kind={'confirmed':'message','template':'template','history':'history','excel':'excel'}[source.kind]
                refs=[EvidenceRef(kind=kind,ref_id=f'{source.file_id}:{source.sheet}:{source.cell}',detail=source.model_dump())]
            evidence.append(FieldEvidence(requirement_id=req.id,field=field,state=state,sources=refs,reason=conflicts[0].question if conflicts else ''))
        if req.included and req.classification not in ('existing','change','ignored'):
            for issue in req.issues:
                if issue.blocking:
                    grouped.setdefault((issue.field,issue.code),{'ids':[],'message':issue.message})['ids'].append(req.id)
    task.evidence=evidence
    task.questions=[Clarification(id=sha256(f'{task.id}:{draft.version}:{f}:{code}'.encode()).hexdigest()[:20],field=f,
        requirement_ids=list(dict.fromkeys(v['ids'])),question=v['message']) for (f,code),v in grouped.items()]
    task.questions.extend(task.semantic_questions)
    task.stage='clarifying' if task.questions else 'preview'


class NewTask(Model):
    name: str = '新搭建任务'
    draft_id: str | None = None


class ArchiveTask(Model):
    expected_version: int
    archived: bool


@router.post('/tasks/{task_id}/archive')
def archive_task(task_id:str,body:ArchiveTask,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        task,p=access(c,task_id,actor)
        project_access(c,task.project_id,actor,write=True)
        if task.version!=body.expected_version: raise HTTPException(409,'任务已变化，请刷新')
        now=datetime.now(timezone.utc)
        if task.lease and datetime.fromisoformat(task.lease['until'])>now: raise HTTPException(409,'任务正在分析，请稍后重试')
        if task.draft_id and c.execute("select id from runs where draft_id=%s and status not in ('complete','paused','cancelled') for update",(task.draft_id,)).fetchone():
            raise HTTPException(409,'请先处理未结束的执行批次')
        task.archived_at=now if body.archived else None
        task.version+=1
        save(c,task)
        c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,%s,%s)",(p['tenant_id'],task.project_id,'task_archived' if body.archived else 'task_restored',json_value({'task_id':task.id,'actor':actor.user_id})))
    return task


@router.post('/projects/{project_id}/tasks')
def create_task(project_id:str,body:NewTask,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        p=project_access(c,project_id,actor,write=True)
        context=c.execute('select data from project_contexts where project_id=%s',(project_id,)).fetchone()
        task=IntakeTask(id=str(uuid4()),project_id=project_id,name=body.name,context=ProjectContext.model_validate(context['data']) if context else ProjectContext())
        if body.draft_id:
            row=c.execute('select data from drafts where id=%s and project_id=%s',(body.draft_id,project_id)).fetchone()
            if not row: raise HTTPException(404,'草稿不存在')
            if c.execute('select id from intake_tasks where draft_id=%s',(body.draft_id,)).fetchone():
                raise HTTPException(409,'草稿已关联搭建任务')
            task.draft_id=body.draft_id
            refresh(task,BuildDraft.model_validate(row['data']))
        c.execute('insert into intake_tasks(id,tenant_id,project_id,draft_id,version,data) values(%s,%s,%s,%s,%s,%s)',(task.id,p['tenant_id'],project_id,task.draft_id,task.version,json_value(task)))
    return task


@router.get('/tasks/{task_id}')
def get_task(task_id:str,actor:Actor=Depends(authenticate)):
    from .preview import build_preview
    from .service import history_for
    with transaction() as c:
        task,p=access(c,task_id,actor)
        draft=None;preview=None
        if task.draft_id:
            draft=BuildDraft.model_validate(c.execute('select data from drafts where id=%s',(task.draft_id,)).fetchone()['data'])
            history=history_for(c,p)
            draft.requirements=[evaluate(r,history) for r in draft.requirements]
            preview=build_preview(draft,history,p['policy_version'],pending_questions=pending_questions(c,draft.id))
            refresh(task,draft)
        return {'task':task.model_dump(mode='json',exclude={'requests','lease'}),'draft':draft,'preview':preview}


@router.get('/projects/{project_id}/context')
def get_context(project_id:str,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        project_access(c,project_id,actor)
        row=c.execute('select data from project_contexts where project_id=%s',(project_id,)).fetchone()
    return ProjectContext.model_validate(row['data']) if row else ProjectContext()


@router.post('/projects/{project_id}/context')
def set_context(project_id:str,body:ProjectContext,actor:Actor=Depends(authenticate)):
    with transaction() as c:
        p=project_access(c,project_id,actor,admin=True)
        c.execute('select id from projects where id=%s for update',(project_id,)).fetchone()
        row=c.execute('select version from project_contexts where project_id=%s',(project_id,)).fetchone()
        if body.version!=(row['version'] if row else 0): raise HTTPException(409,'项目上下文已变化')
        body.version+=1;body.confirmed_by=actor.user_id
        c.execute('insert into project_contexts(project_id,tenant_id,version,data) values(%s,%s,%s,%s) on duplicate key update version=values(version),data=values(data)',(project_id,p['tenant_id'],body.version,json_value(body)))
        c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,'context_confirmed',%s)",(p['tenant_id'],project_id,json_value(body)))
    return body


class TaskAction(Model):
    task_id: str
    request_id: str
    expected_version: int
    expected_draft_version: int | None = None
    action: str = 'message'
    message: str = ''
    proposal_id: str | None = None
    answers: dict[str,str] = Field(default_factory=dict)


async def act(project_id,body,actor):
    from .agent import structured
    from .prompts import COMMON, INTENT, HISTORY, CLARIFY
    digest=sha256(body.model_dump_json().encode()).hexdigest()
    now=datetime.now(timezone.utc)
    with transaction() as c:
        task,p=access(c,body.task_id,actor,write=True)
        if task.project_id!=project_id: raise HTTPException(404,'任务不属于项目')
        previous=task.requests.get(body.request_id)
        if previous:
            if previous['digest']!=digest: raise HTTPException(409,'请求 ID 已用于不同内容')
            if 'result' in previous: return previous['result']
        if task.version!=body.expected_version: raise HTTPException(409,'任务已变化，请刷新')
        if task.lease and datetime.fromisoformat(task.lease['until'])>now: raise HTTPException(409,'任务正在分析')
        draft=None
        if task.draft_id:
            draft=BuildDraft.model_validate(c.execute('select data from drafts where id=%s',(task.draft_id,)).fetchone()['data'])
            if draft.version!=body.expected_draft_version: raise HTTPException(409,'草稿已变化，请刷新')
            history=history_for(c,p)
            draft.requirements=[evaluate(r,history) for r in draft.requirements]
            refresh(task,draft)
        documents=[]
        for file_id in task.file_ids:
            document=c.execute("select id,name,inspection,mapping from files where id=%s and project_id=%s and kind='requirements'",(file_id,project_id)).fetchone()
            if document: documents.append(document)
        if body.action in ('accept','reject'):
            proposal=task.proposal
            if not proposal or proposal.id!=body.proposal_id or proposal.status!='pending' or not draft or proposal.draft_version!=draft.version:
                raise HTTPException(409,'提案已过期或已处理')
            if body.action=='accept':
                if proposal.edits:
                    draft=edit_draft(c,draft.id,actor,draft.version,[{**e.model_dump(),'source_message_id':proposal.message_id} for e in proposal.edits])
                if proposal.expansions:
                    from .service import apply_expansions
                    draft=apply_expansions(c,draft,actor,proposal.expansions,proposal.message_id)
            proposal.status='accepted' if body.action=='accept' else 'rejected'
        elif body.action=='answer':
            edits=[];message_id=str(uuid4())
            answered=[]
            for qid,value in body.answers.items():
                q=next((q for q in task.questions if q.id==qid),None)
                if not q or not value.strip(): raise HTTPException(422,'问题已过期或答案为空')
                if q.scope=='task':
                    task.answers[qid]={'field':q.field,'value':value,'question':q.question,'message_id':message_id}
                elif not draft or q.field not in EDITABLE:
                    raise HTTPException(422,'问题需要当前草稿与有效字段')
                else:
                    edits.extend({'requirement_id':rid,'values':{q.field:value},'source_message_id':message_id} for rid in q.requirement_ids)
                answered.append({'question':q.question,'field':q.field,'answer':value})
            if not answered: raise HTTPException(422,'请填写答案')
            if edits: draft=edit_draft(c,draft.id,actor,draft.version,edits)
            task.messages.append({'id':message_id,'role':'user','content':'本任务回答：'+json.dumps(answered,ensure_ascii=False),'created_at':now.isoformat()})
            task.proposal=None
            task.semantic_questions=[q for q in task.semantic_questions if q.id not in body.answers]
        elif body.action in ('message','analyze','resume'):
            if body.message and not any(m.get('request_id')==body.request_id for m in task.messages):
                task.messages.append({'id':str(uuid4()),'request_id':body.request_id,'role':'user','content':body.message,'created_at':now.isoformat()})
            task.requests[body.request_id]={'digest':digest,'status':'processing'}
            task.lease={'request_id':body.request_id,'until':(now+timedelta(seconds=210)).isoformat()}
            save(c,task)
        else: raise HTTPException(422,'不支持的动作')
        if body.action in ('accept','reject','answer'):
            refresh(task,draft);task.version+=1
            result={'answer':'已保存本任务的答案或提案处理结果，请继续分析并检查预览','task_id':task.id,'project_id':project_id,'draft':draft.model_dump(mode='json') if draft else None,'task':task.model_dump(mode='json',exclude={'requests'})}
            task.requests[body.request_id]={'digest':digest,'result':result};save(c,task)
            return result
    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
        from .agent import conversation_graph
        from .settings import settings
        async with AIOMySQLSaver.from_conn_string(settings().database_url.replace('mysql+pymysql://','mysql://')) as saver:
            graph=conversation_graph(saver)
            config={'configurable':{'thread_id':f"{p['tenant_id']}:{task.id}:{body.request_id}"}}
            checkpoint=await graph.aget_state(config)
            if checkpoint.values.get('decision') and not checkpoint.next:
                output=checkpoint.values
            else:
                output=await graph.ainvoke({'messages':task.messages,'draft':draft.model_dump(mode='json') if draft else None,'context':task.context.model_dump(mode='json'),'documents':documents,'scope':task.scope.model_dump(mode='json')},config)
            decision=ChatDecision.model_validate(output['decision'])
            from .agent import validate_decision
            validate_decision({'messages':task.messages,'draft':draft.model_dump(mode='json') if draft else None},decision)
        if (decision.edits or decision.expansions) and not draft: raise ValueError('尚无草稿，不能修改字段')
        if draft:
            ids={r.id for r in draft.requirements}
            if any(e.requirement_id not in ids or set(e.values)-EDITABLE for e in decision.edits): raise ValueError('修改范围或字段无效')
        with transaction() as c:
            current,p=access(c,task.id,actor,write=True)
            if current.version!=body.expected_version or not current.lease or current.lease['request_id']!=body.request_id: raise HTTPException(409,'分析期间任务已变化')
            if draft:
                version=c.execute('select version from drafts where id=%s for update',(draft.id,)).fetchone()['version']
                if version!=draft.version: raise HTTPException(409,'分析期间草稿已变化')
            current.lease=None
            for question in decision.questions:
                question.id=sha256(json.dumps({'task':task.id,'scope':question.scope,'field':question.field,'ids':sorted(question.requirement_ids),'question':question.question},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
            # Empty model output cannot discard unresolved questions or pending proposals.
            existing={q.id:q for q in current.semantic_questions if q.id not in current.answers}
            existing.update({q.id:q for q in decision.questions})
            current.semantic_questions=list(existing.values())
            if decision.scope is not None:
                current.scope=decision.scope
                if draft and decision.scope.status=='resolved' and decision.scope.action in ('new','repost'):
                    from .scope import select_requirements
                    scoped=select_requirements(draft.requirements,decision.scope,current.messages)
                    selection_edits=[{'requirement_id':r.id,'values':{'included':r.included},'source_message_id':decision.scope.message_ids[-1]} for r,old in zip(scoped,draft.requirements) if r.included!=old.included]
                    if selection_edits:
                        draft=edit_draft(c,draft.id,actor,draft.version,selection_edits)
                        current.proposal=None
                if not draft:
                    current.semantic_questions=[q for q in current.semantic_questions if not (q.scope=='task' and q.field=='scope' and decision.scope.status=='resolved')]
            current.analysis.update({'request_id':body.request_id,'phase':'complete','progress':output.get('progress',{}),'completed_at':datetime.now(timezone.utc).isoformat()})
            current.messages.append({'id':str(uuid4()),'role':'assistant','content':decision.answer,'created_at':datetime.now(timezone.utc).isoformat()})
            if decision.edits or decision.expansions:
                message_id=next((m['id'] for m in reversed(current.messages) if m['role']=='user'),None)
                current.proposal=ChangeProposal(id=str(uuid4()),draft_version=draft.version,edits=decision.edits,expansions=decision.expansions,reason=decision.answer,message_id=message_id)
            refresh(current,draft)
            current.version+=1
            result={'answer':decision.answer,'project_id':project_id,'task_id':current.id,'task':current.model_dump(mode='json',exclude={'requests'}),'draft':draft.model_dump(mode='json') if draft else None}
            current.requests[body.request_id]={'digest':digest,'result':result};save(c,current)
            return result
    except (Exception,asyncio.CancelledError) as error:
        with transaction() as c:
            current,_=access(c,task.id,actor,write=True)
            if current.lease and current.lease['request_id']==body.request_id:
                current.lease=None;current.stage='attention'
                current.analysis.update({'request_id':body.request_id,'phase':'attention','error_type':type(error).__name__,'resume_available':True})
                current.requests[body.request_id]={'digest':digest,'status':'failed','error_type':type(error).__name__}
                save(c,current)
        raise
