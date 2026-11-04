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
        allowed={'included','name','account_id','platform','objective','budget','budget_mode','currency','structure'}
        if not req.structure:return
        if req.structure.budget_level=='adgroup':allowed-={'budget_mode','currency'}
    if set(fields)-allowed:raise ValueError('字段不属于目标实体，请用 target_id 指定实际广告组或素材，不得通过计划字段覆盖子节点')


def find_entity(req,target_id):
    if target_id==req.id:return req
    if req.structure:
        for group in req.structure.groups:
            if group.id==target_id:return group
            for material in group.ads:
                if material.id==target_id:return material
    raise ValueError('目标实体不属于当前计划')


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


def evidence_values(req,target_id):
    node=find_entity(req,target_id)
    data=node.model_dump(mode='json')
    if hasattr(node,'budget') and isinstance(node.budget,BudgetSpec):
        data.update(budget=str(node.budget.amount) if node.budget.amount is not None else None,
                    currency=node.budget.currency,budget_mode=node.budget.mode)
    return {**{'account_id':req.account_id,'objective':req.objective,'timezone':req.timezone},**data}


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
