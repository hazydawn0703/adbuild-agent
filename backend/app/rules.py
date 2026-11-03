from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from .models import Requirement, Issue, ProjectPolicy
from .excel import date_value


def normalized_language(value):
    text=str(value or '').strip().lower()
    return {'英语':'en','英文':'en','english':'en'}.get(text,text)


def history_differences(req, h):
    differences=[]
    for field in ('name','objective','budget','budget_mode','language','start_at','end_at'):
        requested=getattr(req,field)
        historical=h.get(field)
        if requested in (None,'') or historical in (None,''):
            continue
        if field in ('start_at','end_at'):
            try:
                # The export has no timezone column. Display local clock differences only;
                # never infer the advertiser timezone from a requirement's UTC label.
                equal=requested.replace(tzinfo=None)==date_value(historical,'').replace(tzinfo=None)
            except (ValueError,KeyError):
                equal=False
        elif field=='budget':
            try: equal=requested==Decimal(historical)
            except (InvalidOperation,ValueError): equal=False
        elif field=='language':
            equal=normalized_language(requested)==normalized_language(historical)
        else:
            equal=str(requested)==str(historical)
        if not equal:
            differences.append({'field':field,'requested':str(requested),'historical':str(historical)})
    return differences


def fingerprint(req: Requirement):
    # Budget/name deliberately excluded: changing them is a change, not a new deployment.
    values = [req.account_id, req.post_id, req.objective,
              req.start_at.astimezone(timezone.utc).isoformat() if req.start_at and req.start_at.tzinfo else str(req.start_at or ''), req.end_at.astimezone(timezone.utc).isoformat() if req.end_at and req.end_at.tzinfo else str(req.end_at or '')]
    return sha256(json.dumps(values).encode()).hexdigest()


def evaluate(req: Requirement, history: list[dict], now=None):
    now = now or datetime.now(timezone.utc)
    req = req.model_copy(deep=True)
    req.issues = [i for i in req.issues if i.code.startswith("invalid_")]
    req.history = []
    for h in history:
        if h["post_id"] == req.post_id and req.post_id and (not h.get('account_id') or h['account_id']==req.account_id):
            candidate = {k:v for k,v in h.items() if k != "raw"}
            candidate["same_objective"] = h["objective"] == req.objective
            candidate['differences']=history_differences(req,h)
            candidate['match_reason']='帖子 ID 一致；'+('目标一致' if candidate['same_objective'] else '目标不同，仅作历史参考')
            candidate['timezone_note']=f"历史账户时区：{h['timezone']}" if h.get('timezone') else '导出未包含账户时区，历史时间按原文展示，不能仅凭日期认定已存在'
            raw=h.get('raw',{})
            candidate['targeting']={k:raw[k] for k in ('Location','Gender','Age','Languages','Interest Category','Placements','Video Download','Identity ID') if raw.get(k)}
            req.history.append(candidate)
    for field in ("account_id", "post_id", "objective", "budget", "budget_mode", "currency", "start_at", "end_at", "timezone", "audience", "identity_id"):
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
    if req.start_at and req.end_at and bool(req.start_at.tzinfo) == bool(req.end_at.tzinfo) and req.start_at >= req.end_at:
        req.issues.append(Issue(code="schedule",field="end_at",message="结束时间必须晚于开始时间"))
    if req.objective == "traffic" and (not req.landing_url.startswith("https://") or not req.cta):
        req.issues.append(Issue(code="destination",field="landing_url",message="流量目标需要 HTTPS 落地页和 CTA"))
    exact = []
    for h in req.history:
        try:
            start = date_value(h['start_at'],h.get('timezone',''))
            end = date_value(h['end_at'],h.get('timezone',''))
            if not start or not end:
                raise ValueError('历史排期缺失')
            if h['same_objective'] and start.tzinfo and end.tzinfo and start == req.start_at and end == req.end_at:
                exact.append(h)
            elif h['same_objective'] and req.start_at and req.end_at and max(start.replace(tzinfo=None),req.start_at.replace(tzinfo=None))<=min(end.replace(tzinfo=None),req.end_at.replace(tzinfo=None)):
                req.issues.append(Issue(code='history_review',field='start_at',message=f"历史广告 {h.get('ad_id','')} 的排期相近但并非已核实的同一排期，需核对账户时区与实际投放批次，不能直接当作新增"))
        except (ValueError, KeyError):
            pass
        if h['same_objective'] and req.language and h["language"] and normalized_language(req.language) != normalized_language(h["language"]) and "language" not in req.confirmed_fields:
            req.issues.append(Issue(code="language_conflict",field="language",message=f"本次语言 {req.language} 与历史 {h['language']} 不一致，请确认"))
    if exact:
        changed = any(any(d['field'] in ('budget','budget_mode','language') for d in h['differences']) for h in exact)
        req.classification = "change" if changed else "existing"
    else:
        req.classification = "incomplete" if req.issues else "new"
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
