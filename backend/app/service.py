from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import HTTPException
from .models import BuildDraft, ProjectPolicy, Requirement, SourceRef
from .rules import evaluate, check_policy, fingerprint
from .db import transaction, json_value
from .auth import project_access

EDITABLE = {"included","account_id","post_id","post_url","name","objective","budget","budget_mode","currency","start_at","end_at","timezone","audience","language","identity_id","cta","landing_url","structure","platform","budget_level"}


def stored_draft(data):
    if data.get('protocol_version')!=4:
        raise HTTPException(409,'草稿协议已更新，请保留原始资料并创建新任务重新分析；旧确认不能继续使用')
    return BuildDraft.model_validate(data)


def history_for(conn, project):
    return [r["data"] for r in conn.execute("select data from history where project_id=%s and account_id=%s",(project["id"],project["account_id"]))]


def pending_questions(conn,draft_id):
    row=conn.execute('select data from intake_tasks where draft_id=%s',(draft_id,)).fetchone()
    if not row:return []
    questions=list(row['data'].get('questions',[]))
    analysis=row['data'].get('analysis') or {}
    if row['data'].get('lease') or analysis.get('resume_available') or (analysis.get('mapping_error') or {}).get('resume_available'):
        questions.append({'field':'scope','scope':'task','requirement_ids':[],
            'question':'本任务还有未完成的分析，请恢复或重新分析后再确认完整方案','blocking':True})
    if (row['data'].get('scope') or {}).get('status')!='resolved' and not any(q.get('scope')=='task' and q.get('field')=='scope' for q in questions):
        questions.append({'field':'scope','scope':'task','requirement_ids':[],
            'question':'本次需求范围尚未确定，请先明确选择范围并重新分析，再确认完整方案','blocking':True})
    proposal=row['data'].get('proposal')
    if proposal and proposal['status']=='pending':
        questions.append({'field':'included','scope':'task' if proposal.get('additions') else 'requirements','requirement_ids':list(dict.fromkeys([e['requirement_id'] for e in proposal['edits']]+[e['base_requirement_id'] for e in proposal.get('expansions',[])])),
            'question':'存在待采纳的修改提案，请先采纳或拒绝，再确认完整方案','blocking':True})
    return questions


def ensure_draft_active(conn,draft_id):
    row=conn.execute('select data from intake_tasks where draft_id=%s for update',(draft_id,)).fetchone()
    if row and row['data'].get('archived_at'): raise HTTPException(409,'任务已归档，请先恢复')


def task_evidence(conn,project,task):
    """Every adoption path uses the same project- and task-bound file registry."""
    allowed=set(task.get('file_ids',[]))
    documents=[row for row in conn.execute('select id,storage_path,sha256,mapping from files where project_id=%s and kind=%s',(project['id'],'requirements')) if str(row['id']) in allowed] if allowed else []
    return {**task,'project_account_id':project.get('account_id',''),'documents':documents}


def edit_draft(conn, draft_id, actor, expected_version, edits):
    row = conn.execute("select * from drafts where id=%s for update",(draft_id,)).fetchone()
    if not row:
        raise HTTPException(404,"草稿不存在")
    project = project_access(conn,row["project_id"],actor,write=True)
    ensure_draft_active(conn,draft_id)
    draft = stored_draft(row["data"])
    if draft.version != expected_version or draft.status not in ("draft","attention","approved"):
        raise HTTPException(409,"草稿版本已变化或已经冻结，请刷新")
    history = history_for(conn, project)
    taskrow=conn.execute('select data from intake_tasks where draft_id=%s',(draft.id,)).fetchone()
    state=task_evidence(conn,project,taskrow['data'] if taskrow else {})
    for edit in edits:
        req = next((r for r in draft.requirements if r.id == edit["requirement_id"]),None)
        if not req or set(edit["values"]) - EDITABLE:
            raise HTTPException(422,"需求或编辑字段无效")
        from .entities import check_edit_owner
        try:check_edit_owner(req,edit.get('target_id'),edit['values'])
        except ValueError as error:raise HTTPException(422,str(error)) from None
        if 'budget_level' in edit['values']:
            if set(edit['values'])!={'budget_level'}:raise HTTPException(422,'预算层级须单独提交，其他字段分别指定实际目标')
            from .entities import set_budget_level,recover_group_budgets
            try:
                documents=state['documents']
                source=edit.get('sources',{}).get('budget_level')
                if source:
                    from .provenance import supported_source
                    source=supported_source(state,{**req.model_dump(mode='json'),'budget_level':req.structure.budget_level if req.structure else None},'budget_level',edit['values']['budget_level'],source)
                updated=set_budget_level(req,edit['values']['budget_level'],edit.get('source_message_id'),actor.user_id,source)
                updated=recover_group_budgets(updated,documents)
            except ValueError as error:raise HTTPException(422,str(error)) from None
            draft.requirements=[evaluate(updated,history) if r.id==req.id else r for r in draft.requirements]
            continue
        if edit.get('target_id') and edit['target_id']!=req.id:
            from .entities import edit_entity,evidence_values
            try:
                sources={}
                if edit.get('sources'):
                    from .provenance import supported_source
                    original=evidence_values(req,edit['target_id'])
                    sources={field:supported_source(state,original,field,value,edit['sources'][field]) for field,value in edit['values'].items() if field in edit['sources']}
                updated=edit_entity(req,edit['target_id'],edit['values'],edit.get('source_message_id'),actor.user_id,sources)
            except ValueError as error:raise HTTPException(422,str(error)) from None
            draft.requirements=[evaluate(updated,history) if r.id==req.id else r for r in draft.requirements]
            continue
        values = req.model_dump()
        patch=dict(edit["values"])
        verified_structure=None
        verified_budget_level=None
        if patch.get('structure') is not None and edit.get('sources'):
            from .models import ChatDecision,ChatEdit
            from .provenance import validate_proposal_evidence
            decision=ChatDecision(answer='',edits=[ChatEdit(requirement_id=req.id,values=patch,sources=edit['sources'])])
            try:validate_proposal_evidence({**state,'draft':draft.model_dump(mode='json')},decision)
            except (ValueError,OSError) as error:raise HTTPException(422,str(error)) from None
            patch=decision.edits[0].values
            verified_structure=decision.edits[0].sources.get('structure')
            verified_budget_level=decision.edits[0].sources.get('budget_level')
        from .excel import normalize_value,post_id_from_url
        from .models import StructureSpec
        for field,value in list(patch.items()):
            if field=='structure':
                patch[field]=StructureSpec.model_validate(value) if value is not None else None
            elif field!='included':
                try: patch[field]=normalize_value(field,value,patch.get('timezone',req.timezone))
                except (ValueError,TypeError) as error: raise HTTPException(422,str(error))
        if 'post_url' in patch and patch['post_url']:
            linked=post_id_from_url(patch['post_url'])
            if not linked:raise HTTPException(422,'请提供含明确帖子 ID 的 TikTok 视频链接')
            if 'post_id' in patch and patch['post_id'] and patch['post_id']!=linked:
                raise HTTPException(422,'帖子 ID 与 TikTok 视频链接中的 ID 不一致，请核对后一起提交')
            patch['post_id']=linked
        if ('post_id' in patch or 'post_url' in patch) and patch.get('post_url',req.post_url) and patch.get('post_id',req.post_id):
            linked=post_id_from_url(patch.get('post_url',req.post_url))
            if linked and linked!=patch.get('post_id',req.post_id):
                raise HTTPException(422,'帖子 ID 与保留的视频链接冲突，请更新或清空原链接后再提交')
        values.update(patch)
        req = Requirement.model_validate(values)
        if 'budget' in patch and req.structure and req.structure.budget_level=='adgroup':
            req.budget_constraint=patch['budget']
        if verified_budget_level:
            req.sources['budget_level']=verified_budget_level
            req.issues=[i for i in req.issues if i.field!='budget_level']
        for field,value in patch.items():
            offered=edit.get('sources',{}).get(field)
            previous=req.sources.get(field)
            if field=='included':
                req.sources[field]=SourceRef(file_id='',sheet='',cell='',raw_value=str(req.included),kind='confirmed',message_id=edit.get('source_message_id'))
            elif field=='structure' and verified_structure:
                req.sources[field]=verified_structure
            elif offered:
                ref=SourceRef.model_validate(offered)
                if ref.file_id:
                    source_file=conn.execute('select id from files where id=%s and project_id=%s',(ref.file_id,project['id'])).fetchone()
                    if not source_file: raise HTTPException(422,'字段来源不属于当前项目')
                from .provenance import supported_source,verified_history_file
                original=next(r for r in draft.requirements if r.id==req.id)
                try:
                    if ref.kind=='history':
                        ref=verified_history_file(conn,project['id'],ref,field,value,req.timezone)
                    else:
                        ref=supported_source(state,original.model_dump(),field,value,ref)
                except (ValueError,OSError) as error:raise HTTPException(422,str(error)) from None
                req.sources[field]=ref
            elif previous and str(getattr(next(r for r in draft.requirements if r.id==req.id),field))==str(value):
                req.sources[field]=previous
            else:
                message_id=edit.get('source_message_id')
                req.sources[field]=SourceRef(file_id='',sheet='',cell='',raw_value='已提交结构配置' if field=='structure' else str(value),kind='confirmed',message_id=message_id)
            req.sources[field].accepted_by=actor.user_id
            req.sources[field].accepted_at=datetime.now(timezone.utc)
            if field not in req.confirmed_fields: req.confirmed_fields.append(field)
        if 'budget' in patch and req.structure and req.structure.budget_level=='adgroup':
            req.sources['budget_constraint']=req.sources['budget'].model_copy(deep=True)
        from .structure import materialize_allocation
        try:req=materialize_allocation(req)
        except (ValueError,TypeError) as error:raise HTTPException(422,str(error)) from None
        req.issues = [i for i in req.issues if i.field not in patch]
        draft.requirements = [evaluate(req,history) if r.id == req.id else r for r in draft.requirements]
    draft.version += 1
    draft.approval = None
    draft.status = 'draft'
    conn.execute("update drafts set version=%s,data=%s where id=%s",(draft.version,json_value(draft),draft_id))
    return draft


def submit_approved_draft(conn, draft, project):
    from .settings import settings
    from .preview import build_preview
    if not settings().execution_enabled:
        raise HTTPException(423,'真实搭建暂缓，当前仅支持预览与人工确认')
    if any(r.included and r.structure for r in draft.requirements):
        raise HTTPException(423,'多层结构尚未完成平台写入验收，当前仅支持预览与人工确认')
    ensure_draft_active(conn,draft.id)
    # Caller holds the project lock. Reservation and queue insertion commit atomically.
    project = conn.execute("select * from projects where id=%s for update",(project["id"],)).fetchone()
    preview=build_preview(draft,history_for(conn,project),project['policy_version'],pending_questions=pending_questions(conn,draft.id),expected_account_id=project['account_id'])
    if draft.status!='approved' or not preview['approval_valid']:
        raise HTTPException(409,'必须先人工确认当前版本的搭建预览')
    if not project["policy"]:
        raise HTTPException(422,'尚未配置项目授权')
    policy = ProjectPolicy.model_validate(project["policy"])
    connection = conn.execute("select verified from connections where tenant_id=%s",(project["tenant_id"],)).fetchone()
    if not connection or not connection["verified"]:
        raise HTTPException(422,'TikTok 账户与工具绑定尚未验证')
    eligible = [r for r in draft.requirements if r.id in preview['selected_ids'] and not check_policy(r,policy)]
    if len(eligible)!=len(preview['selected_ids']):
        raise HTTPException(422,'已确认方案未完整满足项目授权，请重新检查')
    eligible = list({fingerprint(r):r for r in eligible}.values())
    eligible = [r for r in eligible if not conn.execute("select 1 from deployments where project_id=%s and fingerprint=%s",(project["id"],fingerprint(r))).fetchone()]
    if len(eligible)!=len(preview['selected_ids']):
        raise HTTPException(409,'所选方案中已有广告创建记录，请重新检查预览')
    total = sum((r.budget for r in eligible),Decimal(0))
    if not eligible or total > policy.batch_limit or total + project["reserved"] > policy.cumulative_limit:
        raise HTTPException(422,'本次预算超出批次或累计授权额度')
    run_id = str(uuid4())
    frozen = draft.model_copy(deep=True)
    frozen.requirements = eligible
    conn.execute("insert into runs(id,tenant_id,project_id,draft_id,draft_version,policy_version,status,frozen) values(%s,%s,%s,%s,%s,%s,'queued',%s)",(run_id,project["tenant_id"],project["id"],draft.id,draft.version,project["policy_version"],json_value(frozen)))
    for req in eligible:
        conn.execute("insert into deployments(tenant_id,project_id,fingerprint,run_id,requirement) values(%s,%s,%s,%s,%s)",(project["tenant_id"],project["id"],fingerprint(req),run_id,json_value(req)))
    conn.execute("update projects set reserved=reserved+%s where id=%s",(total,project["id"]))
    conn.execute("insert into jobs(message) values(%s)",(json_value({"run_id":run_id}),))
    draft.status = "queued"
    conn.execute("update drafts set data=%s where id=%s",(json_value(draft),draft.id))
    return run_id


def apply_expansions(conn,draft,actor,expansions,message_id,context,history,messages):
    """Accepted variants replace only their explicitly referenced base requirements."""
    from .models import RequirementExpansion
    variants=[RequirementExpansion.model_validate(x) for x in expansions]
    bases={r.id:r for r in draft.requirements}
    if not variants or any(x.base_requirement_id not in bases for x in variants):
        raise HTTPException(422,'拆分包含未知需求')
    keys=[(x.base_requirement_id,x.objective) for x in variants]
    if len(set(keys))!=len(keys): raise HTTPException(422,'拆分目标重复')
    if any(sum(x.base_requirement_id==base for x in variants)<2 for base in {x.base_requirement_id for x in variants}):
        raise HTTPException(422,'目标拆分须包含该计划完整的多个目标')
    added=[]
    for item in variants:
        base=bases[item.base_requirement_id]
        from .entities import objective_variant
        from .provenance import supported_source
        state={'messages':messages,'context':context}
        try:sources={field:supported_source(state,base.model_dump(mode='json'),field,value) for field,value in [('objective',item.objective),('budget',item.budget)]}
        except ValueError as error:raise HTTPException(422,str(error)) from None
        copy=objective_variant(base,str(uuid4()),item.objective,item.budget,message_id,actor.user_id,context,sources)
        added.append(evaluate(copy,history))
    replaced={x.base_requirement_id for x in variants}
    draft.requirements=[r for r in draft.requirements if r.id not in replaced]+added
    draft.version+=1;draft.approval=None;draft.status='draft'
    conn.execute('update drafts set version=%s,data=%s where id=%s',(draft.version,json_value(draft),draft.id))
    return draft


def apply_additions(conn,draft,project,actor,additions,message_id,task):
    """Adopt grounded message candidates into the one canonical draft."""
    from .models import DemandCandidate
    from .provenance import supported_source
    from .excel import normalize_value
    from .rules import fingerprint
    state=task_evidence(conn,project,task.model_dump(mode='json'))
    added=[]
    for raw in additions:
        candidate=DemandCandidate.model_validate(raw)
        values={};sources={}
        for field,value in candidate.values.items():
            if value in (None,''): continue
            values[field]=normalize_value(field,value,candidate.values.get('timezone',''))
            sources[field]=supported_source(state,candidate.values,field,values[field],candidate.sources.get(field))
        if values.get('post_url') and not values.get('post_id'):
            import re
            match=re.search(r'/video/(\d+)',values['post_url'])
            if match: values['post_id']=match[1];sources['post_id']=sources['post_url']
        if not values.get('post_id'): raise HTTPException(422,'新增候选需要可核实的帖子 ID 或视频链接')
        if not values.get('account_id') and project.get('account_id'):
            values['account_id']=normalize_value('account_id',project['account_id'])
            sources['account_id']=SourceRef(file_id='',sheet='',cell='',raw_value=project['account_id'],kind='project')
        for ref in sources.values(): ref.accepted_by=actor.user_id;ref.accepted_at=datetime.now(timezone.utc)
        from .provenance import validate_candidate_structure
        try:structure=validate_candidate_structure(state,values,sources,candidate.structure)
        except ValueError as error:raise HTTPException(422,str(error)) from None
        added.append(Requirement(id=str(uuid4()),**values,sources=sources,structure=structure,confirmed_fields=list(sources)))
    if not added: raise HTTPException(422,'没有可采纳的新需求')
    current=list(draft.requirements) if draft else []
    seen={fingerprint(r) for r in current}
    for req in added:
        key=fingerprint(req)
        if key in seen: raise HTTPException(422,'候选与已有需求重复，请修改现有需求或明确新排期')
        seen.add(key)
    is_new=draft is None
    if is_new: draft=BuildDraft(id=str(uuid4()),project_id=project['id'])
    draft.requirements=[evaluate(r,history_for(conn,project)) for r in current+added];draft.approval=None;draft.status='draft'
    if is_new:
        conn.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,%s,%s)',(draft.id,project['tenant_id'],project['id'],draft.version,json_value(draft)))
    else:
        draft.version+=1
        conn.execute('update drafts set version=%s,data=%s where id=%s',(draft.version,json_value(draft),draft.id))
    return draft
