from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from .models import Requirement, Issue, ProjectPolicy
from .excel import date_value,zone_value


def normalized_language(value):
    text=str(value or '').strip().lower()
    return {'英语':'en','英文':'en','english':'en'}.get(text,text)


def _historical_budget(h,level):
    amount=h.get(level+'_budget')
    mode=h.get(level+'_budget_mode')
    if level=='campaign':
        if amount in (None,''):amount=h.get('budget')
        if mode in (None,''):mode=h.get('budget_mode')
    return amount,mode


def _difference(field,requested,historical,*,level=None,reference=None,historical_tz=''):
    if requested in (None,'') or historical in (None,''):return None
    if field in ('budget','allocation_ratio'):
        try:equal=Decimal(str(requested))==Decimal(str(historical))
        except (InvalidOperation,ValueError,TypeError):equal=False
    elif field=='language':equal=normalized_language(requested)==normalized_language(historical)
    elif field in ('start_at','end_at'):
        try:
            previous=date_value(historical,historical_tz)
            equal=requested==previous if requested.tzinfo and previous.tzinfo else requested.replace(tzinfo=None)==previous.replace(tzinfo=None)
        except (ValueError,KeyError,TypeError):equal=False
    else:equal=str(requested)==str(historical)
    if equal:return None
    result={'field':field,'requested':str(requested),'historical':str(historical)}
    if level:result['level']=level
    if reference:result['requested_group_id']=reference
    return result


def history_differences(req,h):
    """Compare configuration at its actual budget level, without implying adoption."""
    differences=[]
    for field in ('name','objective','language','start_at','end_at'):
        difference=_difference(field,getattr(req,field),h.get(field),historical_tz=h.get('timezone',''))
        if difference:differences.append(difference)
    level=req.structure.budget_level if req.structure else 'campaign'
    amount,mode=_historical_budget(h,'campaign')
    if level=='campaign':
        for field,value in (('budget',amount),('budget_mode',mode)):
            difference=_difference(field,getattr(req,field),value,level='campaign')
            if difference:differences.append(difference)
    if req.structure:
        # A history row represents one ad group. Do not compare its budget against
        # every requested group or select a group merely by list position.
        history_name=h.get('adgroup_name') or h.get('raw',{}).get('Ad Group Name','')
        groups=[g for g in req.structure.groups if g.id==h.get('adgroup_id') or g.name and g.name==history_name]
        if len(groups)==1:
            group=groups[0]
            if group.budget:
                amount,mode=_historical_budget(h,'adgroup')
                for field,requested,value in (('budget',group.budget.amount,amount),('budget_mode',group.budget.mode,mode)):
                    difference=_difference(field,requested,value,level='adgroup',reference=group.id)
                    if difference:differences.append(difference)
    return differences


def _schedule(req,h):
    try:
        start=date_value(h.get('start_at'),h.get('timezone',''))
        end=date_value(h.get('end_at'),h.get('timezone',''))
    except (ValueError,KeyError,TypeError):return {'status':'unknown','comparable':False}
    if not start or not end or not req.start_at or not req.end_at:return {'status':'missing','comparable':False}
    if bool(start.tzinfo)!=bool(end.tzinfo) or end<=start:return {'status':'invalid','comparable':False}
    comparable=bool(start.tzinfo and end.tzinfo and req.start_at.tzinfo and req.end_at.tzinfo)
    if comparable:
        status='same' if start==req.start_at and end==req.end_at else 'overlap' if max(start,req.start_at)<min(end,req.end_at) else 'different'
    else:
        # This is explicitly only a local-clock hint, never a verified batch key.
        status='local_overlap' if max(start.replace(tzinfo=None),req.start_at.replace(tzinfo=None))<min(end.replace(tzinfo=None),req.end_at.replace(tzinfo=None)) else 'unknown'
    return {'status':status,'comparable':comparable,'historical_start':str(start),'historical_end':str(end)}


def history_candidates(req,history):
    """History has already been tenant/project-scoped by the server."""
    candidates=[]
    invalid_identity=any(i.code.startswith('invalid_') and i.field in ('account_id','post_id') for i in req.issues)
    if invalid_identity:return candidates
    for h in history:
        historical_account=h.get('account_id','')
        if historical_account and req.account_id and historical_account!=req.account_id:continue
        same_post=bool(req.post_id and h.get('post_id')==req.post_id)
        same_business=bool(req.business_no and h.get('business_no')==req.business_no)
        if not same_post and not same_business:continue
        account_verified=bool(req.account_id and historical_account and req.account_id==historical_account)
        same_objective=bool(req.objective and h.get('objective')==req.objective)
        schedule=_schedule(req,h)
        differences=history_differences(req,h)
        candidate={k:v for k,v in h.items() if k!='raw'}
        evidence=[{'field':field,'basis':'exact','requested':str(value),'historical':str(h.get(field,''))} for field,value,matched in (('post_id',req.post_id,same_post),('business_no',req.business_no,same_business),('account_id',req.account_id,account_verified)) if matched]
        if not same_post or not account_verified or not req.objective or not h.get('objective'):
            relation='unverified'
        elif not same_objective:relation='reference'
        elif schedule['comparable'] and schedule['status']=='different':relation='new_batch'
        elif schedule['comparable'] and schedule['status']=='same':
            relation='config_change' if any(d['field'] not in ('name','start_at','end_at') for d in differences) else 'existing'
        else:relation='unverified'
        basis=[]
        if same_post:basis.append('帖子 ID 一致')
        if same_business:basis.append('业务编号一致')
        if not account_verified:basis.append('账户尚未证实，仅作待核实参考')
        elif not req.objective or not h.get('objective'):basis.append('目标尚未明确，仅作待核实参考')
        elif same_objective:basis.append('目标一致')
        else:basis.append('目标不同，仅作配置参考')
        raw=h.get('raw',{})
        candidate.update(same_objective=same_objective,same_post=same_post,account_verified=account_verified,
                         classification=relation,differences=differences,match_evidence=evidence,
                         schedule_comparison=schedule,match_reason='；'.join(basis),
                         reference_use='duplicate_check' if relation in ('existing','config_change') else 'configuration_reference',
                         timezone_note=f"历史账户时区：{h['timezone']}" if h.get('timezone') else '历史账户时区未知；仅使用明确带偏移的完整时间比较，否则保留待核实',
                         budgets={level:dict(zip(('amount','mode'),_historical_budget(h,level))) for level in ('campaign','adgroup')},
                         targeting={k:raw[k] for k in ('Location','Gender','Age','Languages','Interest Category','Placements','Video Download','Identity ID') if raw.get(k)})
        candidates.append(candidate)
    return candidates


def fingerprint(req: Requirement):
    # Budget/name deliberately excluded: changing them is a change, not a new deployment.
    values = [req.account_id, req.post_id, req.objective,
              req.start_at.astimezone(timezone.utc).isoformat() if req.start_at and req.start_at.tzinfo else str(req.start_at or ''), req.end_at.astimezone(timezone.utc).isoformat() if req.end_at and req.end_at.tzinfo else str(req.end_at or '')]
    return sha256(json.dumps(values).encode()).hexdigest()


def evaluate(req: Requirement, history: list[dict], now=None):
    now = now or datetime.now(timezone.utc)
    req = req.model_copy(deep=True)
    req.issues = [i for i in req.issues if i.code.startswith("invalid_")]
    req.history = history_candidates(req,history)
    for field in ("account_id", "post_id", "objective", "budget", "budget_mode", "currency", "start_at", "end_at", "timezone", "audience", "identity_id"):
        if req.structure and field in ('post_id','identity_id','audience','start_at','end_at','timezone'):
            continue
        if getattr(req, field) in (None, ""):
            req.issues.append(Issue(code="missing",field=field,message=f"缺少 {field}"))
    if req.budget is not None and (not req.budget.is_finite() or req.budget <= 0):
        req.issues.append(Issue(code="budget",field="budget",message="预算必须大于零"))
    for field in ("start_at", "end_at"):
        dt = getattr(req,field)
        if dt and dt.tzinfo is None:
            req.issues.append(Issue(code="timezone",field=field,message="排期缺少有效时区"))
    if req.end_at and req.end_at.tzinfo and req.end_at <= now:
        req.issues.append(Issue(code="expired",field="end_at",message="排期已经结束，不会自动平移"))
    elif req.end_at is None and req.timezone and any(i.code=='invalid_date_precision' and i.field=='end_at' for i in req.issues):
        source=req.sources.get('end_at')
        if source:
            try:
                # A date-only Excel serial does not authorize a midnight schedule.
                # Its entire calendar day can nevertheless be proved past in an
                # explicit timezone without choosing any missing start/end time.
                day=re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',source.raw_value)
                last_day=date(*(int(v) for v in day.groups())) if day else datetime.fromisoformat(source.raw_value).date()
                if last_day<now.astimezone(zone_value(req.timezone)).date():
                    req.issues.append(Issue(code='expired',field='end_at',message=f'原始结束日期 {last_day.isoformat()} 在 {req.timezone} 下已经过去；具体结束时刻仍缺失，不会自动补时或平移'))
            except (ValueError,KeyError,TypeError):
                pass
    if req.start_at and req.end_at and bool(req.start_at.tzinfo) == bool(req.end_at.tzinfo) and req.start_at >= req.end_at:
        req.issues.append(Issue(code="schedule",field="end_at",message="结束时间必须晚于开始时间"))
    if not req.structure and req.objective == "traffic" and (not req.landing_url.startswith("https://") or not req.cta):
        req.issues.append(Issue(code="destination",field="landing_url",message="流量目标需要 HTTPS 落地页和 CTA"))
    from .structure import validate_structure
    req.issues.extend(validate_structure(req))
    if req.structure:
        for group in req.structure.groups:
            if group.end_at and group.end_at.tzinfo and group.end_at <= now:
                req.issues.append(Issue(code='expired',field='end_at',message=f'广告组 {group.name} 排期已经结束，不会自动平移'))
    exact=[]
    for h in req.history:
        if h['classification'] in ('existing','config_change'):exact.append(h)
        elif h['same_post'] and h['same_objective'] and h['classification']=='unverified':
            req.issues.append(Issue(code='history_review',field='start_at',message=f"历史广告 {h.get('ad_id','')} 的账户或批次尚未核实，不能直接当作新增或已存在"))
        if h['same_objective'] and req.language and h.get('language') and normalized_language(req.language)!=normalized_language(h['language']) and 'language' not in req.confirmed_fields and req.sources.get('language') and req.sources['language'].kind=='history':
            req.issues.append(Issue(code='language_conflict',field='language',message=f"本次语言 {req.language} 与历史 {h['language']} 不一致，请确认"))
    if exact:
        req.classification='change' if any(h['classification']=='config_change' for h in exact) else 'existing'
    else:
        req.classification='incomplete' if req.issues else 'new'
    if req.structure:
        from .models import StructureSpec
        states=[];candidates=[]
        for group in req.structure.groups:
            for ad in group.ads:
                material=req.model_copy(update={'post_id':ad.post_id,'post_url':ad.post_url,
                    'start_at':group.start_at,'end_at':group.end_at,'timezone':group.timezone,
                    'structure':StructureSpec(budget_level=req.structure.budget_level,groups=[group])})
                matches=history_candidates(material,history)
                candidates.extend({**h,'target_id':ad.id} for h in matches)
                exact=[h for h in matches if h['classification'] in ('existing','config_change')]
                unknown=any(h['same_post'] and h['same_objective'] and h['classification']=='unverified' for h in matches)
                states.append('change' if any(h['classification']=='config_change' for h in exact) else 'existing' if exact else 'unknown' if unknown else 'new')
        req.history=candidates
        if states and all(s in ('existing','change') for s in states):
            req.classification='change' if 'change' in states else 'existing'
        else:
            if any(s in ('existing','change','unknown') for s in states):
                req.issues.append(Issue(code='history_review',field='included',message='本计划包含已有投放或待核实素材，请按素材明确选择新增范围',target_id=req.id))
            req.classification='incomplete' if req.issues else 'new'
    req.issues=list({(i.code,i.field,i.message):i for i in req.issues}.values())
    return req


def check_policy(req: Requirement, policy: ProjectPolicy, now=None):
    now = now or datetime.now(timezone.utc)
    failures = []
    if not policy.enabled or policy.valid_until.tzinfo is None or policy.valid_until <= now:
        failures.append("项目预授权未启用或已过期")
    for field, allowed in (("account_id",policy.account_ids),("objective",policy.objectives),("audience",policy.audiences),("identity_id",policy.identities)):
        if getattr(req,field) not in allowed:
            failures.append(f"{field} 不在预授权范围")
    if req.currency != policy.currency:
        failures.append("币种与预授权不一致")
    if req.budget_mode != "lifetime":
        failures.append("自动投放要求总预算，日预算需转换并确认总上限")
    if req.budget is None or req.budget > policy.per_item_limit:
        failures.append("单条预算超过上限或缺失")
    if req.classification != "new" or any(i.blocking for i in req.issues):
        failures.append("需求尚未满足新增广告执行条件")
    for field, source in req.sources.items():
        if source.kind == "history" and field not in req.confirmed_fields:
            failures.append(f"{field} 的历史推断尚未确认")
    return failures

