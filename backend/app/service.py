from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import HTTPException
from .models import BuildDraft, ProjectPolicy, Requirement, SourceRef
from .rules import evaluate, check_policy, fingerprint
from .db import transaction, json_value
from .auth import project_access

EDITABLE = {"included","account_id","post_id","post_url","name","objective","budget","budget_mode","currency","start_at","end_at","timezone","audience","language","identity_id","cta","landing_url","structure"}


def history_for(conn, project):
    return [r["data"] for r in conn.execute("select data from history where project_id=%s and account_id=%s",(project["id"],project["account_id"]))]


def pending_questions(conn,draft_id):
    row=conn.execute('select data from intake_tasks where draft_id=%s',(draft_id,)).fetchone()
    if not row:return []
    questions=list(row['data'].get('semantic_questions',[]))
    proposal=row['data'].get('proposal')
    if proposal and proposal['status']=='pending':
        questions.append({'field':'included','requirement_ids':[e['requirement_id'] for e in proposal['edits']],
            'question':'存在待采纳的修改提案，请先采纳或拒绝，再确认完整方案','blocking':True})
    return questions


def edit_draft(conn, draft_id, actor, expected_version, edits):
    row = conn.execute("select * from drafts where id=%s for update",(draft_id,)).fetchone()
    if not row:
        raise HTTPException(404,"草稿不存在")
    project = project_access(conn,row["project_id"],actor,write=True)
    draft = BuildDraft.model_validate(row["data"])
    if draft.version != expected_version or draft.status not in ("draft","attention","approved"):
        raise HTTPException(409,"草稿版本已变化或已经冻结，请刷新")
    history = history_for(conn, project)
    for edit in edits:
        req = next((r for r in draft.requirements if r.id == edit["requirement_id"]),None)
        if not req or set(edit["values"]) - EDITABLE:
            raise HTTPException(422,"需求或编辑字段无效")
        values = req.model_dump()
        patch=dict(edit["values"])
        from .excel import normalize_value
        from .models import StructureSpec
        for field,value in list(patch.items()):
            if field=='structure':
                patch[field]=StructureSpec.model_validate(value) if value is not None else None
            elif field!='included':
                try: patch[field]=normalize_value(field,value,patch.get('timezone',req.timezone))
                except (ValueError,TypeError) as error: raise HTTPException(422,str(error))
        if 'post_url' in patch and patch['post_url']:
            import re
            match=re.search(r'/video/(\d+)',patch['post_url'])
            if match: patch['post_id']=match[1]
        values.update(patch)
        req = Requirement.model_validate(values)
        for field,value in patch.items():
            offered=edit.get('sources',{}).get(field)
            previous=req.sources.get(field)
            if offered:
                ref=SourceRef.model_validate(offered)
                if ref.file_id:
                    source_file=conn.execute('select id from files where id=%s and project_id=%s',(ref.file_id,project['id'])).fetchone()
                    if not source_file: raise HTTPException(422,'字段来源不属于当前项目')
                # Model may retain a previously verified reference, never invent a new cell.
                if not previous or ref.model_dump(exclude={'accepted_at','accepted_by'})!=previous.model_dump(exclude={'accepted_at','accepted_by'}):
                    raise HTTPException(422,'新来源需通过文件映射或结构化回答核实')
                req.sources[field]=ref
            elif previous and str(getattr(next(r for r in draft.requirements if r.id==req.id),field))==str(value):
                req.sources[field]=previous
            else:
                message_id=edit.get('source_message_id')
                req.sources[field]=SourceRef(file_id='',sheet='',cell='',raw_value=str(value),kind='confirmed',message_id=message_id)
            req.sources[field].accepted_by=actor.user_id
            req.sources[field].accepted_at=datetime.now(timezone.utc)
            if field not in req.confirmed_fields: req.confirmed_fields.append(field)
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
    # Caller holds the project lock. Reservation and queue insertion commit atomically.
    project = conn.execute("select * from projects where id=%s for update",(project["id"],)).fetchone()
    preview=build_preview(draft,history_for(conn,project),project['policy_version'],pending_questions=pending_questions(conn,draft.id))
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


def apply_expansions(conn,draft,actor,expansions,message_id):
    """Accepted variants replace only their explicitly referenced base requirements."""
    from .models import RequirementExpansion
    variants=[RequirementExpansion.model_validate(x) for x in expansions]
    bases={r.id:r for r in draft.requirements}
    if not variants or any(x.base_requirement_id not in bases for x in variants):
        raise HTTPException(422,'拆分包含未知需求')
    keys=[(x.base_requirement_id,x.objective) for x in variants]
    if len(set(keys))!=len(keys): raise HTTPException(422,'拆分目标重复')
    added=[]
    for item in variants:
        base=bases[item.base_requirement_id]
        copy=base.model_copy(deep=True)
        copy.id=str(uuid4());copy.objective=item.objective;copy.budget=item.budget
        copy.structure=None
        for field in ('objective','budget'):
            copy.sources[field]=SourceRef(file_id='',sheet='',cell='',raw_value=str(getattr(copy,field)),kind='confirmed',message_id=message_id,accepted_by=actor.user_id,accepted_at=datetime.now(timezone.utc))
        added.append(copy)
    replaced={x.base_requirement_id for x in variants}
    draft.requirements=[r for r in draft.requirements if r.id not in replaced]+added
    draft.version+=1;draft.approval=None;draft.status='draft'
    conn.execute('update drafts set version=%s,data=%s where id=%s',(draft.version,json_value(draft),draft.id))
    return draft
