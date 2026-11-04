from .service import stored_draft
from .public import public_business
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


def question_observation(question,draft):
    rows=[] if draft is None else [r for r in draft.requirements if r.included and (question.scope=='task' or r.id in question.requirement_ids)]
    result={}
    from .entities import evidence_values
    for row in rows:
        for target in question.target_ids or [row.id]:
            try:data=row.model_dump(mode='json') if target==row.id else evidence_values(row,target)
            except ValueError:continue
            result[target]=sha256(json.dumps({'value':data.get(question.field),
                'source':data.get('sources',{}).get(question.field)},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return result


def reconcile_questions(task,draft):
    """Resolve old questions only when new verified evidence changes their field."""
    basis=task.analysis.setdefault('question_basis',{})
    unresolved=[]
    for question in task.semantic_questions:
        if question.id in task.answers:continue
        if question.reason_code=='platform_check':continue
        if draft is not None and question.scope!='task':
            selected=[r for r in draft.requirements if r.included and r.id in question.requirement_ids]
            if not selected:continue
            if question.field in ('cta','landing_url') and all(r.objective!='traffic' for r in selected):continue
            if question.field=='structure' and all(r.structure and not any(i.field=='structure' and i.blocking for i in r.issues) for r in selected):continue
            if not any(i.blocking and i.field==question.field and (not question.target_ids or not i.target_id or i.target_id in question.target_ids)
                       for row in selected for i in row.issues):continue
        current=question_observation(question,draft)
        old=basis.get(question.id)
        if question.field=='scope':
            if task.scope.status=='resolved' and (draft is None or any(r.included for r in draft.requirements)):continue
        elif old is not None and current and current!=old and draft is not None:
            from .entities import evidence_values
            targets=[]
            for row in draft.requirements:
                for target in current:
                    try:data=row.model_dump(mode='json') if target==row.id else evidence_values(row,target)
                    except ValueError:continue
                    targets.append((row,target,data))
            if targets and all(data.get(question.field) not in (None,'') and data.get('sources',{}).get(question.field)
                   and not any(i.field==question.field and i.blocking and (not i.target_id or i.target_id==target) for i in row.issues)
                   for row,target,data in targets):continue
        unresolved.append(question)
    task.semantic_questions=unresolved


def refresh(task,draft):
    if draft is not None:
        from .provenance import _budget_values,equivalent
        from .models import Issue
        for row in draft.requirements:
            row.issues=[i for i in row.issues if i.code!='message_conflict']
            if not row.included or row.budget is None:continue
            for message in reversed(task.messages):
                if message.get('role')!='user':continue
                if message.get('id')==getattr(row.sources.get('budget'),'message_id',None):break
                if 'answers' in message:
                    relevant=[a for a in message['answers'] if a['field']=='budget' and row.id in (a.get('target_ids') or a.get('requirement_ids') or [])]
                    values=[a['answer'] for a in relevant]
                else:
                    text=str(message.get('content',''))
                    if row.business_no and '业务编号' in text and row.business_no not in text:continue
                    values=_budget_values(text)
                if not values:continue
                if any(not equivalent('budget',row.budget,value) for value in values):
                    row.issues.append(Issue(code='message_conflict',field='budget',target_id=row.id,message='当前用户预算依据与文件或已存方案存在冲突，请明确本批最终总额'))
                break
    reconcile_questions(task,draft)
    if draft is None:
        task.questions=list(task.semantic_questions)
        task.stage='attention' if task.analysis.get('phase')=='attention' else 'clarifying' if task.questions else 'intake'
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
                kind={'confirmed':'message','template':'template','history':'history','excel':'excel','project':'template','calculated':'template'}[source.kind]
                refs=[EvidenceRef(kind=kind,ref_id=f'{source.file_id}:{source.sheet}:{source.cell}',detail=source.model_dump())]
            evidence.append(FieldEvidence(requirement_id=req.id,field=field,state=state,sources=refs,reason=conflicts[0].question if conflicts else ''))
        if req.included and req.classification not in ('existing','change','ignored'):
            for issue in req.issues:
                if issue.blocking:
                    target=issue.target_id or req.id
                    from .entities import entity_label
                    group=grouped.setdefault((issue.field,issue.code,req.id,target),{'ids':set(),'targets':set(),'messages':[],'label':entity_label(req,target)})
                    group['ids'].add(req.id)
                    group['targets'].add(target)
                    if issue.message not in group['messages']:group['messages'].append(issue.message)
    task.evidence=evidence
    # Merge reasons for the same field and actual owner. Different owners may
    # need different answers and must retain separate controls in the batch form.
    combined={}
    for (field,code,_owner,_target),group in grouped.items():
        key=(field,'requirements',tuple(sorted(group['ids'])),tuple(sorted(group['targets'])))
        entry=combined.setdefault(key,{'messages':[],'codes':set(),'label':group['label']})
        entry['messages'].extend(message for message in group['messages'] if message not in entry['messages'])
        entry['codes'].add(code)
    task.questions=[]
    for (field,scope,ids,targets),entry in combined.items():
        reason='conflict' if any(c.startswith('invalid_') or c in ('duplicate','history_review','language_conflict') for c in entry['codes']) else 'missing'
        question=Clarification(id='',field=field,scope=scope,reason_code=reason,
            requirement_ids=list(ids),target_ids=list(targets),evidence_version=draft.version,question=entry['label']+'：'+'；'.join(entry['messages']))
        key=json.dumps({'origin':'system','task':task.id,'field':field,'scope':scope,'requirements':ids,
            'targets':targets,'reason':reason,'evidence':question_observation(question,draft)},sort_keys=True)
        question.id=sha256(key.encode()).hexdigest()[:20]
        task.questions.append(question)
    task.questions.extend(task.semantic_questions)
    task.stage='attention' if task.analysis.get('phase')=='attention' else 'clarifying' if task.questions else 'preview'


class NewTask(Model):
    name: str = '新搭建任务'
    draft_id: str | None = None


class ArchiveTask(Model):
    expected_version: int
    archived: bool


class RenameTask(Model):
    expected_version: int
    name: str = Field(min_length=1,max_length=255)


@router.patch('/tasks/{task_id}')
def rename_task(task_id:str,body:RenameTask,actor:Actor=Depends(authenticate)):
    name=body.name.strip()
    if not name: raise HTTPException(422,'任务名称不能为空')
    with transaction() as c:
        task,p=access(c,task_id,actor,write=True)
        if task.version!=body.expected_version: raise HTTPException(409,'任务已变化，请刷新后重试')
        if task.lease and datetime.fromisoformat(task.lease['until'])>datetime.now(timezone.utc):
            raise HTTPException(409,'任务正在分析，请稍后重试')
        old_name=task.name
        task.name=name
        task.version+=1
        save(c,task)
        c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,'task_renamed',%s)",(p['tenant_id'],task.project_id,json_value({'task_id':task.id,'actor':actor.user_id,'old_name':old_name,'name':name})))
    return {'id':task.id,'name':name,'version':task.version}


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
    return public_business(task.model_dump(mode='json',exclude={'requests','lease'}))


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
            refresh(task,stored_draft(row['data']))
        c.execute('insert into intake_tasks(id,tenant_id,project_id,draft_id,version,data) values(%s,%s,%s,%s,%s,%s)',(task.id,p['tenant_id'],project_id,task.draft_id,task.version,json_value(task)))
    return public_business(task)


@router.get('/tasks/{task_id}')
def get_task(task_id:str,actor:Actor=Depends(authenticate)):
    from .preview import build_preview
    from .service import history_for
    with transaction() as c:
        task,p=access(c,task_id,actor)
        draft=None;preview=None
        if task.draft_id:
            draft=stored_draft(c.execute('select data from drafts where id=%s',(task.draft_id,)).fetchone()['data'])
            history=history_for(c,p)
            draft.requirements=[evaluate(r,history) for r in draft.requirements]
            preview=build_preview(draft,history,p['policy_version'],pending_questions=pending_questions(c,draft.id),expected_account_id=p['account_id'])
            refresh(task,draft)
        return public_business({'task':task.model_dump(mode='json',exclude={'requests','lease'}),'draft':draft,'preview':preview})


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
    from .agent import FLOW_VERSION
    from .prompts import PROMPT_HASH
    digest=sha256(body.model_dump_json().encode()).hexdigest()
    now=datetime.now(timezone.utc)
    with transaction() as c:
        task,p=access(c,body.task_id,actor,write=True)
        if task.project_id!=project_id: raise HTTPException(404,'任务不属于项目')
        previous=task.requests.get(body.request_id)
        if previous:
            if previous['digest']!=digest: raise HTTPException(409,'请求 ID 已用于不同内容')
            if 'result' in previous: return public_business(previous['result'])
            if previous.get('flow_version')!=FLOW_VERSION or previous.get('prompt_hash')!=PROMPT_HASH:
                raise HTTPException(409,'原分析流程或提示词版本未知或已变化，请使用新请求重新分析')
        if task.version!=body.expected_version: raise HTTPException(409,'任务已变化，请刷新')
        if task.lease and datetime.fromisoformat(task.lease['until'])>now: raise HTTPException(409,'任务正在分析')
        draft=None
        history=history_for(c,p)
        if task.draft_id:
            draft=stored_draft(c.execute('select data from drafts where id=%s',(task.draft_id,)).fetchone()['data'])
            if draft.version!=body.expected_draft_version: raise HTTPException(409,'草稿已变化，请刷新')
            draft.requirements=[evaluate(r,history) for r in draft.requirements]
            refresh(task,draft)
        documents=[]
        for file_id in task.file_ids:
            document=c.execute("select id,name,inspection,mapping,storage_path,sha256 from files where id=%s and project_id=%s and kind='requirements'",(file_id,project_id)).fetchone()
            if document: documents.append(document)
        if body.action in ('accept','reject'):
            proposal=task.proposal
            if not proposal or proposal.id!=body.proposal_id or proposal.status!='pending' or proposal.draft_version!=(draft.version if draft else 0):
                raise HTTPException(409,'提案已过期或已处理')
            if not draft and (proposal.edits or proposal.expansions):raise HTTPException(409,'修改或拆分提案需要已有草稿')
            if body.action=='accept':
                if proposal.edits:
                    draft=edit_draft(c,draft.id,actor,draft.version,[{**e.model_dump(),'source_message_id':proposal.message_id} for e in proposal.edits])
                if proposal.expansions:
                    from .service import apply_expansions
                    draft=apply_expansions(c,draft,actor,proposal.expansions,proposal.message_id)
                if proposal.additions:
                    from .service import apply_additions
                    draft=apply_additions(c,draft,p,actor,proposal.additions,proposal.message_id,task)
                    task.draft_id=draft.id
            proposal.status='accepted' if body.action=='accept' else 'rejected'
        elif body.action=='answer':
            edits=[];message_id=str(uuid4())
            answered=[];scope_answered=False
            for qid,value in body.answers.items():
                q=next((q for q in task.questions if q.id==qid),None)
                if not q or not value.strip(): raise HTTPException(422,'问题已过期或答案为空')
                if q.scope=='task':
                    if q.field not in ('scope','structure'):
                        from .excel import normalize_value
                        try:normalize_value(q.field,value,'')
                        except (ValueError,TypeError,KeyError):raise HTTPException(422,'任务答案格式无效，请按字段要求填写') from None
                    task.answers[qid]={'field':q.field,'value':value,'question':q.question,'message_id':message_id}
                    if q.field=='scope':
                        task.scope.status='unresolved';task.scope.reason='已保存范围回答，请继续分析核对本次集合'
                        scope_answered=True
                elif q.field=='structure':
                    task.answers[qid]={'field':q.field,'value':value,'question':q.question,'message_id':message_id}
                elif not draft or q.field not in EDITABLE:
                    raise HTTPException(422,'问题需要当前草稿与有效字段')
                else:
                    if q.target_ids:
                        from .entities import find_entity
                        for target in q.target_ids:
                            owner=None
                            for candidate in draft.requirements:
                                if candidate.id not in q.requirement_ids:continue
                                try:find_entity(candidate,target)
                                except ValueError:continue
                                owner=candidate;break
                            if owner is None:raise HTTPException(409,'问题目标已变化，请刷新')
                            edits.append({'requirement_id':owner.id,'target_id':target,'values':{q.field:value},'source_message_id':message_id})
                    else:
                        edits.extend({'requirement_id':rid,'values':{q.field:value},'source_message_id':message_id} for rid in q.requirement_ids)
                answered.append({'question':q.question,'field':q.field,'answer':value,'requirement_ids':q.requirement_ids,'target_ids':q.target_ids})
            if not answered: raise HTTPException(422,'请填写答案')
            if edits: draft=edit_draft(c,draft.id,actor,draft.version,edits)
            elif scope_answered and draft: draft=edit_draft(c,draft.id,actor,draft.version,[])
            task.messages.append({'id':message_id,'role':'user','content':'本任务回答：'+json.dumps(answered,ensure_ascii=False),'answers':answered,'created_at':now.isoformat()})
            task.proposal=None
            task.semantic_questions=[q for q in task.semantic_questions if q.id not in body.answers]
        elif body.action in ('message','analyze','resume'):
            checkpoint_request_id=body.request_id
            base_task_version=task.version
            base_draft_version=draft.version if draft else None
            if body.action=='resume':
                saved=task.analysis
                if body.message or body.answers or body.proposal_id:raise HTTPException(409,'恢复只继续原分析；新信息请使用重新分析')
                if saved.get('phase')!='attention' or not saved.get('resume_available') or not all(k in saved for k in ('checkpoint_request_id','base_task_version','base_draft_version','flow_version','prompt_hash')):
                    raise HTTPException(409,'此任务没有可恢复的版本化检查点，请重新分析')
                if saved['flow_version']!=FLOW_VERSION or saved['prompt_hash']!=PROMPT_HASH:
                    raise HTTPException(409,'分析流程或提示词已更新，不能恢复旧检查点，请重新分析')
                if saved['base_task_version']!=task.version or saved['base_draft_version']!=(draft.version if draft else None):
                    raise HTTPException(409,'任务或草稿已变化，不能继续旧检查点，请重新分析')
                checkpoint_request_id=saved['checkpoint_request_id']
                base_task_version=saved['base_task_version']
                base_draft_version=saved['base_draft_version']
            if body.message and not any(m.get('request_id')==body.request_id for m in task.messages):
                task.messages.append({'id':str(uuid4()),'request_id':body.request_id,'role':'user','content':body.message,'created_at':now.isoformat()})
            task.requests[body.request_id]={'digest':digest,'status':'processing','flow_version':FLOW_VERSION,'prompt_hash':PROMPT_HASH}
            task.lease={'request_id':body.request_id,'until':(now+timedelta(seconds=210)).isoformat()}
            task.analysis.update({'checkpoint_request_id':checkpoint_request_id,'base_task_version':base_task_version,
                                  'base_draft_version':base_draft_version,'flow_version':FLOW_VERSION,'prompt_hash':PROMPT_HASH,
                                  'phase':'running','resume_available':False})
            task.analysis.pop('error',None);task.analysis.pop('error_type',None)
            save(c,task)
        else: raise HTTPException(422,'不支持的动作')
        if body.action in ('accept','reject','answer'):
            refresh(task,draft);task.version+=1
            result={'answer':'已保存本任务的答案或提案处理结果，请继续分析并检查预览','task_id':task.id,'project_id':project_id,'draft':draft.model_dump(mode='json') if draft else None,'task':task.model_dump(mode='json',exclude={'requests'})}
            task.requests[body.request_id]={'digest':digest,'result':result};save(c,task)
            return public_business(result)
    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
        from .agent import conversation_graph
        from .settings import settings
        async with AIOMySQLSaver.from_conn_string(settings().database_url.replace('mysql+pymysql://','mysql://')) as saver:
            graph=conversation_graph(saver)
            config={'configurable':{'thread_id':f"{p['tenant_id']}:{task.id}:{checkpoint_request_id}"}}
            checkpoint=await graph.aget_state(config)
            if checkpoint.values.get('decision') and not checkpoint.next:
                output=checkpoint.values
            elif checkpoint.next:
                # Resume the saved node with its validated scope/evidence, not the original input.
                output=await graph.ainvoke(None,config)
            else:
                output=await graph.ainvoke({'messages':task.messages,'draft':draft.model_dump(mode='json') if draft else None,'context':task.context.model_dump(mode='json'),'documents':documents,'scope':task.scope.model_dump(mode='json'),'project_account_id':p['account_id'],'history_records':history},config)
            decision=ChatDecision.model_validate(output['decision'])
            from .models import TaskScope
            resolved_scope=TaskScope.model_validate(output['scope'])
            from .agent import validate_decision
            validate_decision({'messages':task.messages,'draft':draft.model_dump(mode='json') if draft else None,'context':task.context.model_dump(mode='json'),'documents':documents,'scope':resolved_scope.model_dump(mode='json'),'project_account_id':p['account_id']},decision)
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
                question.evidence_version=draft.version if draft else current.context.version
                question.id=sha256(json.dumps({'task':task.id,'scope':question.scope,'field':question.field,'ids':sorted(question.requirement_ids),'targets':sorted(question.target_ids),'reason':question.reason_code,'evidence':question_observation(question,draft)},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
            # Empty model output cannot discard unresolved questions or pending proposals.
            existing={q.id:q for q in current.semantic_questions if q.id not in current.answers}
            existing.update({q.id:q for q in decision.questions})
            current.semantic_questions=list(existing.values())
            for question in decision.questions:
                current.analysis.setdefault('question_basis',{}).setdefault(question.id,question_observation(question,draft))
            current.scope=resolved_scope
            if current.scope.status=='resolved':
                if draft and current.scope.action in ('new','repost'):
                    from .scope import select_requirements
                    scoped=select_requirements(draft.requirements,current.scope,current.messages)
                    selection_edits=[{'requirement_id':r.id,'values':{'included':r.included},'source_message_id':current.scope.message_ids[-1]} for r,old in zip(scoped,draft.requirements) if r.included!=old.included]
                    if selection_edits:
                        draft=edit_draft(c,draft.id,actor,draft.version,selection_edits)
                        current.proposal=None
                if not draft:
                    current.semantic_questions=[q for q in current.semantic_questions if not (q.scope=='task' and q.field=='scope')]
            current.analysis.update({'request_id':body.request_id,'phase':'complete','resume_available':False,'progress':output.get('progress',{}),'completed_at':datetime.now(timezone.utc).isoformat()})
            if (current.scope.action=='history'):
                from .history_query import lookup_history
                current.analysis['history_query']=lookup_history(history,current.scope,[r.model_dump(mode='json') for r in draft.requirements] if draft else [])
            current.messages.append({'id':str(uuid4()),'role':'assistant','content':decision.answer,'created_at':datetime.now(timezone.utc).isoformat()})
            if draft:
                for edit in decision.edits:
                    row=next((r for r in draft.requirements if r.id==edit.requirement_id),None)
                    if row and 'included' in edit.values and edit.values['included']==row.included:
                        edit.values.pop('included');edit.sources.pop('included',None)
                decision.edits=[e for e in decision.edits if e.values]
            if decision.edits or decision.expansions or decision.additions:
                message_id=next((m['id'] for m in reversed(current.messages) if m['role']=='user'),None)
                current.proposal=ChangeProposal(id=str(uuid4()),draft_version=draft.version if draft else 0,edits=decision.edits,expansions=decision.expansions,additions=decision.additions,reason=decision.answer,message_id=message_id)
            refresh(current,draft)
            current.version+=1
            result={'answer':decision.answer,'project_id':project_id,'task_id':current.id,'task':current.model_dump(mode='json',exclude={'requests'}),'draft':draft.model_dump(mode='json') if draft else None}
            current.requests[body.request_id]={'digest':digest,'result':result};save(c,current)
            return public_business(result)
    except (Exception,asyncio.CancelledError) as error:
        with transaction() as c:
            current,_=access(c,task.id,actor,write=True)
            if current.lease and current.lease['request_id']==body.request_id:
                current.lease=None;current.stage='attention'
                current.analysis.update({'request_id':body.request_id,'phase':'attention','error_type':type(error).__name__,'resume_available':True,
                                         'checkpoint_request_id':checkpoint_request_id,'base_task_version':base_task_version,'base_draft_version':base_draft_version})
                current.requests[body.request_id]={'digest':digest,'status':'failed','error_type':type(error).__name__,'flow_version':FLOW_VERSION,'prompt_hash':PROMPT_HASH}
                from .agent import ModelServiceError
                if isinstance(error,ModelServiceError):
                    current.analysis['error']=error.public_error()
                    current.requests[body.request_id]['error']=error.public_error()
                save(c,current)
        raise
