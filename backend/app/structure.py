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
    ratios = [Decimal(r) for r in ratios]
    if not total.is_finite() or total <= 0 or total != total.quantize(Decimal('.01')):
        raise ValueError('拆分预算必须为正数且最多两位小数')
    if not ratios or any(not r.is_finite() or r <= 0 for r in ratios):
        raise ValueError('预算分配权重必须为有限正数')
    weights = [Fraction(r) for r in ratios]
    exact = [Fraction(total) * r / sum(weights) for r in weights]
    amounts = [(Decimal((v * 100).__floor__()) / 100).quantize(Decimal('.01')) for v in exact]
    remainder = int((total - sum(amounts)) / Decimal('.01'))
    order = sorted(range(len(ratios)), key=lambda i: (-(exact[i] - Fraction(amounts[i])), i))
    for i in order[:remainder]:
        amounts[i] += Decimal('.01')
    if any(v <= 0 for v in amounts):
        raise ValueError('拆分后的每组预算必须大于零')
    return amounts


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
    group_ids = [g.id for g in spec.groups]
    if len(set(group_ids)) != len(group_ids) or any(not value.strip() for value in group_ids):
        problem('structure', '广告组 ID 必须非空且在计划内唯一')
    amounts = [None] * len(spec.groups)
    budgets = [g.budget for g in spec.groups]
    if spec.budget_level == 'campaign':
        if any(b is not None for b in budgets):
            problem('structure', '计划预算与广告组预算不能同时生效，请明确预算层级')
    else:
        if any(b is None for b in budgets):
            problem('structure', '广告组预算模式下，每组都需要金额或分配比例')
        elif all(b.allocation_ratio is not None and b.amount is None for b in budgets):
            try:
                amounts = allocate_budget(req.budget, [b.allocation_ratio for b in budgets])
            except (ValueError, TypeError, InvalidOperation) as error:
                problem('structure', str(error))
        elif all(b.amount is not None and b.allocation_ratio is None for b in budgets):
            amounts = [b.amount for b in budgets]
            if any(not v.is_finite() or v <= 0 for v in amounts):
                problem('structure', '每个广告组预算必须为有效正数')
            elif req.budget is None or sum(amounts) != req.budget:
                problem('structure', '广告组预算合计必须等于本需求的总预算')
        else:
            problem('structure', '组预算应全部使用金额或全部使用比例，且每组只选一种')
        for b in budgets:
            if b and b.currency and b.currency != req.currency:
                problem('structure', '广告组币种必须与需求总预算一致')
            if b and b.mode and b.mode != req.budget_mode:
                problem('structure', '广告组预算类型必须与需求总预算口径一致')

    groups = []
    material_ids = set()
    for index, group in enumerate(spec.groups):
        target_id=group.id
        materials = group.ads
        if not materials:
            problem('structure', '广告组尚无明确素材')
        ads = []
        audience = req.audience if group.audience is None else group.audience
        language = req.language if group.language is None else group.language
        if not audience:
            problem('audience', f'广告组 {group.name or group.id} 缺少受众')
        delivered=set()
        if not group.start_at or not group.end_at or not group.timezone:
            problem('start_at', f'广告组 {group.name or group.id} 缺少明确排期或时区')
        elif not group.start_at.tzinfo or not group.end_at.tzinfo:
            problem('timezone', '广告组排期缺少有效时区')
        elif group.end_at <= group.start_at:
            problem('end_at', '广告组结束时间必须晚于开始时间')
        for material in materials:
            target_id=material.id
            if not material.id.strip() or material.id in material_ids:
                problem('structure', '广告 ID 必须非空且在计划内唯一')
            material_ids.add(material.id)
            post_id = material.post_id or req.post_id
            post_url = material.post_url or req.post_url
            identity_id = material.identity_id or req.identity_id
            cta = material.cta or req.cta
            destination = material.landing_url or req.landing_url
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
            'planned_total':str(req.budget) if req.budget is not None else None, 'adgroups':groups}, issues


def validate_structure(req: Requirement):
    return campaign_tree(req)[1]

