"""Compile evidenced source rows into campaign/group/material entities."""
from collections import defaultdict
from hashlib import sha256
import json
from decimal import Decimal
from .models import StructureSpec, GroupSpec, MaterialSpec, BudgetSpec, Issue, SourceRef


def identity(*parts):
    return sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def assemble(rows):
    campaigns = defaultdict(list)
    for req, entity, refs in rows:
        # A reviewed campaign-name binding is itself explicit identity evidence
        # within this task/account/business. It need not be mapped twice.
        plan_key=entity.get('campaign_key') or (req.name if req.sources.get('name') else None)
        key = (req.account_id, req.business_no, plan_key, req.objective)
        # Missing entity evidence cannot join unrelated rows.
        campaigns[key if plan_key else (*key, req.id)].append((req, entity, refs))
    result = []
    for key, entries in campaigns.items():
        req = entries[0][0].model_copy(deep=True)
        req.id = identity('campaign', key)
        req.issues = []
        conflict_target=req.id
        def conflict(field, message,code=None):
            req.issues.append(Issue(code=code or ('invalid_budget_level' if field=='budget_level' else 'invalid_structure'), field=field, message=message,target_id=conflict_target))
        def unique(field, values):
            found = {v for v in values if v not in (None, '')}
            if len(found) > 1:
                conflict(field, f'同一实体的 {field} 来源冲突','invalid_budget_level_conflict' if field=='budget_level' else None)
            return next(iter(found)) if len(found) == 1 else None
        level = unique('budget_level', [e.get('budget_level') for _, e, _ in entries])
        if level not in ('campaign', 'adgroup'):conflict('budget_level','预算层级尚未明确，请选择计划预算或广告组预算')
        if not key[2]:conflict('structure', '缺少明确计划身份，请审核实体映射')
        for field in ('currency', 'budget_mode', 'platform', 'name'):
            unique(field, [getattr(row, field) for row, _, _ in entries])
        grouped = defaultdict(list)
        for row, entity, refs in entries:
            group_key = entity.get('group_key')
            if not group_key:
                conflict('structure', '缺少广告组身份，不能默认每行建立广告组')
            grouped[group_key or row.id].append((row, entity, refs))
        groups = []
        for group_key, members in grouped.items():
            first = members[0][0]
            group_id = identity(req.id, group_key)
            conflict_target=group_id
            attributes = {f: unique(f, [getattr(row, f) for row, _, _ in members])
                          for f in ('audience', 'language', 'start_at', 'end_at', 'timezone')}
            attributes['timezone'] = attributes['timezone'] or ''
            amount = unique('budget', [e.get('adgroup_budget') for _, e, _ in members])
            ads = []
            seen = {}
            for row, entity, refs in members:
                material_key = entity.get('record_key') or row.post_id or row.id
                material_id=identity(group_id,material_key)
                for issue in row.issues:
                    target=material_id if issue.field in ('post_id','post_url','identity_id','cta','landing_url') else group_id if issue.field in ('audience','language','start_at','end_at','timezone') or issue.field=='budget' and level=='adgroup' else req.id
                    req.issues.append(issue.model_copy(update={'target_id':target}))
                values = {f: getattr(row, f) for f in ('post_id', 'post_url', 'identity_id', 'cta', 'landing_url')}
                if material_key in seen:
                    if seen[material_key] != values:
                        conflict('structure', '同一素材标识包含冲突配置')
                    continue
                seen[material_key] = values
                ads.append(MaterialSpec(id=material_id, **values,
                    sources={f: row.sources[f] for f in values if f in row.sources}))
            group_sources={**{f:first.sources[f] for f in ('budget','currency','budget_mode','audience','language','start_at','end_at','timezone') if f in first.sources},**members[0][2]}
            if members[0][1].get('group_key') and 'group_key' in members[0][2]:group_sources['name']=members[0][2]['group_key']
            if 'adgroup_budget' in group_sources:group_sources['budget']=group_sources['adgroup_budget']
            groups.append(GroupSpec(id=group_id, name=str(members[0][1].get('group_key') or ''), **attributes,
                budget=BudgetSpec(amount=amount, currency=req.currency, mode=req.budget_mode) if level == 'adgroup' or amount is not None else None,
                ads=ads, sources=group_sources))
        conflict_target=req.id
        total = unique('budget', [e.get('campaign_budget') for _, e, _ in entries])
        req.budget_constraint=total if level=='adgroup' else None
        if req.budget_constraint is not None:
            source=next((refs['campaign_budget'] for _,e,refs in entries if e.get('campaign_budget') is not None and 'campaign_budget' in refs),None)
            if source:req.sources['budget_constraint']=source
        if level == 'adgroup':
            amounts = [g.budget.amount for g in groups]
            if all(v is not None for v in amounts):
                derived = sum(amounts, Decimal(0))
                if total is not None and total != derived:
                    conflict('budget', '计划总额与广告组预算合计不一致')
                total = derived
        if level in ('campaign','adgroup'):
            req.budget = total
        if total is not None:
            if level=='campaign':
                source=next((refs['campaign_budget'] for _, e, refs in entries if e.get('campaign_budget') is not None and 'campaign_budget' in refs),None)
                if source:req.sources['budget']=source
            else:
                req.sources['budget']=SourceRef(file_id='',sheet='',cell='',raw_value=str(total),kind='calculated',derived_from=[g.id+':budget' for g in groups])
        source=next((refs['budget_level'] for _,e,refs in entries if e.get('budget_level')==level and 'budget_level' in refs),None)
        if source:req.sources['budget_level']=source
        req.structure = StructureSpec(budget_level=level if level in ('campaign','adgroup') else None, groups=groups)
        result.append(req)
    return result
