"""Build a reviewable hierarchy from the draft's shared values and explicit differences."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from fractions import Fraction
from urllib.parse import urlsplit
import re
from .models import Requirement, Issue, StructureSpec, GroupSpec, MaterialSpec


def redact_material_secrets(value):
    """JSON response projection; private material codes never leave storage as raw values."""
    if isinstance(value, dict):
        return {key:redact_material_secrets(item) for key,item in value.items() if key != 'authorization_code'}
    if isinstance(value, list):
        return [redact_material_secrets(item) for item in value]
    return value


def allocate_budget(total: Decimal, ratios: list[Decimal]) -> list[Decimal]:
    """Allocate cents deterministically; do not lose money to independent rounding."""
    total = Decimal(total)
    if not total.is_finite() or total <= 0 or total != total.quantize(Decimal('.01')):
        raise ValueError('拆分预算必须为正数且最多两位小数')
    try:weights=[Fraction(r) for r in ratios]
    except (ValueError,TypeError,OverflowError,ZeroDivisionError):raise ValueError('预算分配权重必须为有限正数') from None
    if not weights or any(r<=0 for r in weights):
        raise ValueError('预算分配权重必须为有限正数')
    exact = [Fraction(total) * r / sum(weights) for r in weights]
    amounts = [(Decimal((v * 100).__floor__()) / 100).quantize(Decimal('.01')) for v in exact]
    remainder = int((total - sum(amounts)) / Decimal('.01'))
    order = sorted(range(len(ratios)), key=lambda i: (-(exact[i] - Fraction(amounts[i])), i))
    for i in order[:remainder]:
        amounts[i] += Decimal('.01')
    if any(v <= 0 for v in amounts):
        raise ValueError('拆分后的每组预算必须大于零')
    return amounts


def materialize_allocation(req: Requirement):
    """Persist owned amounts before scope filtering can change the weight set."""
    spec=req.structure
    if not spec or spec.budget_level!='adgroup':return req
    budgets=[g.budget for g in spec.groups]
    if not all(b and b.allocation_ratio is not None and b.amount is None for b in budgets):return req
    total=req.budget_constraint if req.budget_constraint is not None else req.budget
    amounts=allocate_budget(total,[b.allocation_ratio for b in budgets])
    from .models import SourceRef
    for group,amount in zip(spec.groups,amounts):
        group.sources['budget']=SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value=str(amount),
            derived_from=[req.id+':budget',*[g.id+':allocation_ratio' for g in spec.groups]])
        group.budget.amount=amount;group.budget.allocation_ratio=None
    return req


def campaign_tree(req: Requirement):
    """Return materialized review nodes plus local issues. Never creates platform objects."""
    issues = []
    target_id=req.id
    def problem(field, message):
        issues.append(Issue(code='structure', field=field, message=message,target_id=target_id))

    name = req.name or f"{req.business_no or req.id[:8]}-TikTok-{req.objective or '待确认'}"
    spec = req.structure
    if spec is None:
        problem('structure', '尚无经过核实的计划、广告组和素材结构')
        return {'id':f'{req.id}:campaign','requirement_id':req.id,'name':name,
                'account_id':req.account_id,'objective':req.objective,'budget_level':None,
                'budget':None,'planned_total':None,'adgroups':[]}, issues
    spec=spec.model_copy(update={'groups':[g.model_copy(update={'ads':[a for a in g.ads if a.included]}) for g in spec.groups if any(a.included for a in g.ads)]})
    if not spec.groups:problem('included','没有选中需要新增的素材')
    group_ids = [g.id for g in spec.groups]
    if len(set(group_ids)) != len(group_ids) or any(not value.strip() for value in group_ids):
        problem('structure', '广告组 ID 必须非空且在计划内唯一')
    amounts = [None] * len(spec.groups)
    budgets = [g.budget for g in spec.groups]
    if spec.budget_level == 'campaign':
        if any(b is not None for b in budgets):
            problem('budget_level', '计划预算与广告组预算不能同时生效，请明确预算层级')
    elif spec.budget_level == 'adgroup':
        if all(b is not None and b.allocation_ratio is not None and b.amount is None for b in budgets):
            try:
                amounts = allocate_budget(req.budget_constraint if req.budget_constraint is not None else req.budget, [b.allocation_ratio for b in budgets])
            except (ValueError, TypeError, InvalidOperation) as error:
                problem('structure', str(error))
        elif all(b is not None and b.amount is not None and b.allocation_ratio is None for b in budgets):
            amounts = [b.amount for b in budgets]
            from .excel import is_cent_amount
            if any(not v.is_finite() or v <= 0 for v in amounts):
                problem('structure', '每个广告组预算必须为有效正数')
        elif all(b is not None and (b.amount is not None or b.allocation_ratio is not None) for b in budgets):
            problem('structure', '组预算应全部使用金额或全部使用比例，且每组只选一种')
        for b in budgets:
            if b and b.currency and b.currency != req.currency:
                problem('structure', '广告组币种必须与需求总预算一致')
            if b and b.mode and b.mode != req.budget_mode:
                problem('structure', '广告组预算类型必须与需求总预算口径一致')
    else:
        problem('budget_level','预算层级尚未明确，请说明计划预算或广告组预算；已识别的组和素材仍保留')
    from .excel import is_cent_amount
    valid_amount=lambda a:a is not None and a.is_finite() and a>0 and is_cent_amount(a)
    total=req.budget if spec.budget_level=='campaign' else sum(amounts,Decimal(0)) if spec.budget_level=='adgroup' and all(valid_amount(a) for a in amounts) else None
    original_budgets=[g.budget for g in req.structure.groups]
    constraint_total=sum((b.amount for b in original_budgets),Decimal(0)) if all(b and valid_amount(b.amount) for b in original_budgets) else total
    if spec.budget_level=='adgroup' and req.budget_constraint is not None and constraint_total is not None and constraint_total!=req.budget_constraint:
        problem('budget','广告组预算合计与明确的计划总额约束不一致，请核对总额或组预算')

    groups = []
    material_ids = set()
    for index, group in enumerate(spec.groups):
        target_id=group.id
        if spec.budget_level=='adgroup' and group.budget and group.budget.amount is not None and group.budget.amount.is_finite() and not is_cent_amount(group.budget.amount):
            problem('budget','广告组预算金额必须明确到分，不能自动舍入')
        if spec.budget_level=='adgroup' and (not group.budget or group.budget.amount is None and group.budget.allocation_ratio is None):
            problem('budget',f'广告组 {group.name or group.id} 缺少预算金额')
        materials = group.ads
        if not materials:
            problem('structure', '广告组尚无明确素材')
        ads = []
        audience = group.audience
        language = group.language
        if not audience:
            problem('audience', f'广告组 {group.name or group.id} 缺少受众')
        if not language:
            problem('language', f'广告组 {group.name or group.id} 缺少语言')
        delivered=set()
        for field in ('start_at','end_at'):
            if getattr(group,field) is None:problem(field,f'广告组 {group.name or group.id} 缺少明确的'+('开始时间' if field=='start_at' else '结束时间'))
        if not group.timezone:
            problem('timezone',f'广告组 {group.name or group.id} 缺少明确时区')
        elif group.start_at and group.end_at and (not group.start_at.tzinfo or not group.end_at.tzinfo):
            problem('timezone', '广告组排期缺少有效时区')
        elif group.start_at and group.end_at and group.end_at <= group.start_at:
            problem('end_at', '广告组结束时间必须晚于开始时间')
        for material in materials:
            target_id=material.id
            if not material.id.strip() or material.id in material_ids:
                problem('structure', '广告 ID 必须非空且在计划内唯一')
            material_ids.add(material.id)
            post_id = material.post_id
            post_url = material.post_url
            identity_id = material.identity_id
            cta = material.cta
            destination = material.landing_url
            if material.kind == 'authorization_code':
                if not material.authorization_code.strip():
                    problem('structure', f'广告 {material.name or material.id} 缺少素材授权码')
                # Authorization is an independent material type; it never becomes a post ID.
                post_id = material.post_id
                post_url = material.post_url
            elif material.kind == 'post':
                if not post_id or not str(post_id).isdigit():
                    problem('post_id', f'广告 {material.name or material.id} 的帖子 ID 必须为数字字符串')
                if not identity_id:
                    problem('identity_id', f'广告 {material.name or material.id} 缺少广告身份')
            else:
                problem('structure', f'广告 {material.name or material.id} 的素材类型尚未明确')
            if post_url:
                host = urlsplit(post_url).hostname or ''
                if not (host == 'tiktok.com' or host.endswith('.tiktok.com')):
                    problem('post_url', f'广告 {material.name or material.id} 的帖子链接不属于 TikTok')
                found = re.search(r'/video/(\d+)', post_url)
                if found and post_id and found[1] != post_id:
                    problem('post_id', f'广告 {material.name or material.id} 的链接与帖子 ID 冲突')
            if req.objective == 'traffic' and (not destination.startswith('https://') or not cta):
                problem('landing_url', f'广告 {material.name or material.id} 的流量目标需要 HTTPS 落地页与 CTA')
            delivery=(material.kind,post_id,identity_id,material.authorization_code)
            if delivery in delivered:problem('structure','同一广告组内存在重复素材，请核对记录身份')
            delivered.add(delivery)
            ads.append({'id':material.id, 'name':material.name or req.name or req.business_no or post_id,
                        'material_kind':material.kind, 'post_id':post_id, 'post_url':post_url,
                        'identity_id':identity_id, 'authorization_present':bool(material.authorization_code),
                        'cta':cta, 'landing_url':destination,
                        'sources':{k:v.model_dump(mode='json') for k,v in material.sources.items() if k != 'authorization_code'}})
        groups.append({'id':group.id, 'name':group.name or f'{name}-广告组{index+1}',
                       'audience':audience, 'language':language,
                       'start_at':group.start_at.isoformat() if group.start_at else None,
                       'end_at':group.end_at.isoformat() if group.end_at else None, 'timezone':group.timezone,
                       'budget':{'amount':str(amounts[index]) if amounts[index] is not None else None,
                                 'currency':req.currency, 'mode':req.budget_mode} if spec.budget_level == 'adgroup' else None,
                       'ads':ads, 'sources':{k:v.model_dump(mode='json') for k,v in group.sources.items()}})
    return {'id':f'{req.id}:campaign', 'requirement_id':req.id, 'name':name,
            'account_id':req.account_id, 'objective':req.objective, 'budget_level':spec.budget_level,
            'budget':{'amount':str(req.budget) if req.budget is not None else None,
                      'currency':req.currency, 'mode':req.budget_mode} if spec.budget_level == 'campaign' else None,
            'planned_total':str(total) if total is not None else None, 'adgroups':groups}, issues


def validate_structure(req: Requirement):
    return campaign_tree(req)[1]
