"""Address and edit the actual owner of a field in a campaign hierarchy."""
from decimal import Decimal
from .models import SourceRef, BudgetSpec, Requirement
from .excel import normalize_value, post_id_from_url


GROUP_FIELDS={'name','audience','language','start_at','end_at','timezone','budget','currency','budget_mode'}
MATERIAL_FIELDS={'name','post_id','post_url','identity_id','cta','landing_url'}


def check_edit_owner(req,target_id,fields):
    if target_id and target_id!=req.id:
        from .models import GroupSpec
        node=find_entity(req,target_id)
        allowed=GROUP_FIELDS if isinstance(node,GroupSpec) else MATERIAL_FIELDS
    else:
        allowed={'included','name','account_id','platform','objective','budget','budget_mode','currency','structure','budget_level'}
        if not req.structure:return
    if set(fields)-allowed:raise ValueError('字段不属于目标实体，请用 target_id 指定实际广告组或素材，不得通过计划字段覆盖子节点')


def find_entity(req,target_id):
    if target_id==req.id:return req
    if req.structure:
        for group in req.structure.groups:
            if group.id==target_id:return group
            for material in group.ads:
                if material.id==target_id:return material
    raise ValueError('目标实体不属于当前计划')


def set_budget_level(req,value,message_id,actor_id,source=None):
    from datetime import datetime,timezone
    req=req.model_copy(deep=True)
    if req.structure is None:raise ValueError('尚无已核实的组和素材，请先明确三层关系')
    level=normalize_value('budget_level',value)
    previous_level=req.structure.budget_level
    changed=level!=previous_level
    req.structure.budget_level=level
    if level=='campaign':
        for group in req.structure.groups:group.budget=None
        if req.budget_constraint is not None:
            req.budget=req.budget_constraint
            if req.sources.get('budget_constraint'):req.sources['budget']=req.sources['budget_constraint']
        req.budget_constraint=None;req.sources.pop('budget_constraint',None)
        if changed and req.sources.get('budget') and req.sources['budget'].kind=='calculated':
            req.budget=None;req.sources.pop('budget')
    else:
        if previous_level=='campaign' and req.budget is not None and req.sources.get('budget') and req.sources['budget'].kind!='calculated':
            req.budget_constraint=req.budget;req.sources['budget_constraint']=req.sources['budget'].model_copy(deep=True)
        for group in req.structure.groups:
            group.budget=group.budget or BudgetSpec(currency=req.currency,mode=req.budget_mode)
    ref=SourceRef.model_validate(source) if source else SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=level,message_id=message_id)
    req.sources['budget_level']=ref.model_copy(update={'accepted_by':actor_id,'accepted_at':datetime.now(timezone.utc)})
    req.issues=[i for i in req.issues if i.field!='budget_level']
    return req


def entity_label(req,target_id):
    label=req.name or req.business_no or req.id
    if target_id==req.id:return label
    if req.structure:
        for group in req.structure.groups:
            group_label=label+' / '+(group.name or group.id)
            if group.id==target_id:return group_label
            for index,ad in enumerate(group.ads,1):
                if ad.id==target_id:return group_label+' / '+(ad.name or f'素材{index}')
    raise ValueError('目标实体不属于当前计划')


def recover_group_budgets(req,documents):
    """After ownership is confirmed, re-read mapped amounts instead of asking again."""
    if not req.structure:return req
    from .provenance import verify_excel_source,read_verified_workbook
    from .models import Issue
    workbooks={}
    def amounts(refs):
        found={};seen=set()
        for ref in refs:
            key=(ref.file_id,ref.sheet,ref.cell)
            if key in seen:continue
            seen.add(key)
            document=next((d for d in documents if str(d['id'])==ref.file_id),None)
            if not document:raise ValueError('预算来源不属于当前任务')
            if ref.file_id not in workbooks:workbooks[ref.file_id]=read_verified_workbook(document)
            amount=normalize_value('budget',ref.raw_value)
            verified=verify_excel_source(document,'budget',amount,ref,workbook=workbooks[ref.file_id])
            found[amount]=verified
        return found
    if req.structure.budget_level=='campaign':
        refs=[a.sources['unassigned_budget'] for g in req.structure.groups for a in g.ads if a.sources.get('unassigned_budget')]
        if not refs or req.sources.get('budget') and req.sources['budget'].kind=='confirmed':return req
        try:
            found=amounts(refs)
            if len(found)!=1:raise ValueError('已确认计划预算，但源记录金额冲突；不能采用第一行金额')
            req.budget,req.sources['budget']=next(iter(found.items()))
        except (ValueError,OSError) as error:
            req.budget=None;req.sources.pop('budget',None)
            req.issues.append(Issue(code='invalid_budget_source',field='budget',target_id=req.id,message=str(error)))
        return req
    for group in req.structure.groups:
        ref=group.sources.get('budget')
        refs=[a.sources['unassigned_budget'] for a in group.ads if a.sources.get('unassigned_budget')]
        if not group.budget or group.budget.amount is not None:continue
        if not refs:
            if not ref or ref.kind!='excel' or any(i.field=='budget' and i.target_id==group.id and i.code.startswith('invalid_') for i in req.issues):continue
            refs=[ref]
        try:
            found=amounts(refs)
            if len(found)!=1:raise ValueError('同一广告组的源记录预算冲突，不能采用第一行金额')
            amount,verified=next(iter(found.items()))
            group.budget.amount=amount;group.sources['budget']=verified
        except (ValueError,OSError) as error:
            req.issues.append(Issue(code='invalid_budget_source',field='budget',target_id=group.id,message=str(error)))
    if all(g.budget and g.budget.amount is not None for g in req.structure.groups):
        req.budget=sum((g.budget.amount for g in req.structure.groups),Decimal(0))
        req.sources['budget']=SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value=str(req.budget),derived_from=[g.id+':budget' for g in req.structure.groups])
    return req


def evidence_values(req,target_id):
    node=find_entity(req,target_id)
    data=node.model_dump(mode='json')
    if hasattr(node,'budget') and isinstance(node.budget,BudgetSpec):
        data.update(budget=str(node.budget.amount) if node.budget.amount is not None else None,
                    currency=node.budget.currency,budget_mode=node.budget.mode)
    return {**{'account_id':req.account_id,'business_no':req.business_no,'objective':req.objective,'timezone':req.timezone},**data,
        '_entity_kind':'campaign' if node is req else 'adgroup' if hasattr(node,'ads') else 'material','_campaign_name':req.name,
        '_group_name':next((g.name for g in req.structure.groups if any(a.id==target_id for a in g.ads)),'') if req.structure else '',
        'issues':[issue.model_dump(mode='json') for issue in req.issues if issue.target_id==target_id]}


def edit_entity(req,target_id,patch,message_id,actor_id,sources=None):
    from datetime import datetime,timezone
    from .models import GroupSpec
    req=req.model_copy(deep=True);node=find_entity(req,target_id)
    if node is req:raise ValueError('计划字段请使用计划编辑入口')
    allowed=GROUP_FIELDS if isinstance(node,GroupSpec) else MATERIAL_FIELDS
    if set(patch)-allowed:raise ValueError('字段不属于目标实体')
    tz=patch.get('timezone',getattr(node,'timezone','') or req.timezone)
    for field,value in patch.items():
        value=normalize_value(field,value,tz)
        if field in ('budget','currency','budget_mode'):
            if req.structure.budget_level!='adgroup':raise ValueError('计划预算模式不能设置组预算')
            node.budget=node.budget or BudgetSpec()
            setattr(node.budget,{'budget':'amount','currency':'currency','budget_mode':'mode'}[field],value)
        else:setattr(node,field,value)
        ref=(sources or {}).get(field)
        node.sources[field]=(SourceRef.model_validate(ref) if ref else SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=str(value),message_id=message_id)).model_copy(update={'accepted_by':actor_id,'accepted_at':datetime.now(timezone.utc)})
    if isinstance(node,GroupSpec):
        if 'timezone' in patch and node.timezone:
            for field in ('start_at','end_at'):
                value=getattr(node,field)
                if value is not None and value.tzinfo is None:setattr(node,field,normalize_value(field,value,node.timezone))
        if req.structure.budget_level=='adgroup' and all(g.budget and g.budget.amount is not None for g in req.structure.groups):
            req.budget=sum((g.budget.amount for g in req.structure.groups),Decimal(0))
            req.sources['budget']=SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value=str(req.budget),derived_from=[g.id+':budget' for g in req.structure.groups])
    elif node.post_url:
        linked=post_id_from_url(node.post_url)
        if not linked or node.post_id and linked!=node.post_id:raise ValueError('素材帖子 ID 与链接不一致')
    req.issues=[issue for issue in req.issues if not (issue.target_id==target_id and issue.field in patch)]
    return Requirement.model_validate(req.model_dump())


def objective_variant(base,new_id,objective,total,message_id,actor_id,context,sources):
    """Clone the evidenced graph for an explicitly accepted objective variant."""
    from datetime import datetime,timezone
    from .assembly import identity
    from .provenance import _context_supports
    from .models import Objective
    copy=base.model_copy(deep=True);copy.id=new_id;copy.objective=Objective(objective);copy.budget=total
    ids={base.id:copy.id}
    if copy.structure:
        for group in copy.structure.groups:
            previous=group.id;group.id=identity(copy.id,previous);ids[previous]=group.id
            for ad in group.ads:
                previous=ad.id;ad.id=identity(group.id,previous);ids[previous]=ad.id
    copy.issues=[issue.model_copy(update={'target_id':ids.get(issue.target_id,issue.target_id)}) for issue in copy.issues
        if not (issue.target_id in ('',base.id) and issue.field in ('objective','budget'))]
    for field in ('objective','budget'):
        copy.sources[field]=SourceRef.model_validate(sources[field]).model_copy(update={'accepted_by':actor_id,'accepted_at':datetime.now(timezone.utc)})
        if field not in copy.confirmed_fields:copy.confirmed_fields.append(field)
    copy.budget_constraint=total if copy.structure and copy.structure.budget_level=='adgroup' else None
    if copy.budget_constraint is not None:copy.sources['budget_constraint']=copy.sources['budget'].model_copy(deep=True)
    else:copy.sources.pop('budget_constraint',None)
    nodes=[copy,*[node for group in copy.structure.groups for node in [group,*group.ads]]] if copy.structure else [copy]
    for node in nodes:
        values=copy.model_dump(mode='json') if node is copy else evidence_values(copy,node.id)
        for field,ref in list(node.sources.items()):
            if ref.kind!='template' or field not in values:continue
            if _context_supports({'context':context},values,field,values[field]):continue
            if hasattr(node,'budget') and isinstance(node.budget,BudgetSpec) and field in ('budget','budget_mode','currency'):
                setattr(node.budget,{'budget':'amount','budget_mode':'mode','currency':'currency'}[field],'' if field=='currency' else None)
            elif hasattr(node,field):setattr(node,field,None if field in ('budget','objective','budget_mode','start_at','end_at') else '')
            node.sources.pop(field)
            if node is copy and field in copy.confirmed_fields:copy.confirmed_fields.remove(field)
    if copy.structure:
        goal_source=copy.sources['objective']
        copy.sources['structure']=SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value='沿用已核实的三层结构并建立新目标节点',derived_from=[base.id+':structure',f'{goal_source.kind}:{goal_source.file_id}:{goal_source.sheet}:{goal_source.cell}:{goal_source.message_id}'])
    return copy
