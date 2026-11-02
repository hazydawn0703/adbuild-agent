from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from .models import Requirement, Issue, ProjectPolicy


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
        if h["post_id"] == req.post_id and req.post_id:
            candidate = {k:v for k,v in h.items() if k != "raw"}
            candidate["same_objective"] = h["objective"] == req.objective
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
            # Export timestamps are account-local; compare only with explicitly supplied account zone.
            from zoneinfo import ZoneInfo
            start = datetime.fromisoformat(h["start_at"].replace("/","-")).replace(tzinfo=ZoneInfo(req.timezone))
            end = datetime.fromisoformat(h["end_at"].replace("/","-")).replace(tzinfo=ZoneInfo(req.timezone))
            if h["same_objective"] and start == req.start_at and end == req.end_at:
                exact.append(h)
        except (ValueError, KeyError):
            pass
        if req.language and h["language"] and req.language.lower() != h["language"].lower() and "language" not in req.confirmed_fields:
            req.issues.append(Issue(code="language_conflict",field="language",message=f"本次语言 {req.language} 与历史 {h['language']} 不一致，请确认"))
    if exact:
        changed = any(h["budget"] and req.budget is not None and Decimal(h["budget"]) != req.budget for h in exact)
        req.classification = "change" if changed else "existing"
    else:
        req.classification = "incomplete" if req.issues else "new"
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
