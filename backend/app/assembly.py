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
        key = (req.account_id, req.business_no, entity.get('campaign_key'), req.objective)
        # Missing entity evidence cannot join unrelated rows.
        campaigns[key if entity.get('campaign_key') else (*key, req.id)].append((req, entity, refs))
    result = []
    for key, entries in campaigns.items():
        req = entries[0][0].model_copy(deep=True)
        req.id = identity('campaign', key)
        req.issues = [issue for row, _, _ in entries for issue in row.issues]
        def conflict(field, message):
            req.issues.append(Issue(code='invalid_structure', field=field, message=message))
        def unique(field, values):
            found = {v for v in values if v not in (None, '')}
            if len(found) > 1:
                conflict(field, f'同一实体的 {field} 来源冲突')
            return next(iter(found)) if len(found) == 1 else None
        level = unique('budget_level', [e.get('budget_level') for _, e, _ in entries])
        if level not in ('campaign', 'adgroup') or not entries[0][1].get('campaign_key'):
            conflict('structure', '缺少明确计划身份或预算层级，请审核实体映射')
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
            attributes = {f: unique(f, [getattr(row, f) for row, _, _ in members])
                          for f in ('audience', 'language', 'start_at', 'end_at', 'timezone')}
            attributes['timezone'] = attributes['timezone'] or ''
            amount = unique('budget', [e.get('adgroup_budget') for _, e, _ in members])
            ads = []
            seen = {}
            for row, entity, refs in members:
                material_key = entity.get('record_key') or row.post_id or row.id
                values = {f: getattr(row, f) for f in ('post_id', 'post_url', 'identity_id', 'cta', 'landing_url')}
                if material_key in seen:
                    if seen[material_key] != values:
                        conflict('structure', '同一素材标识包含冲突配置')
                    continue
                seen[material_key] = values
                ads.append(MaterialSpec(id=identity(group_id, material_key), **values,
                    sources={f: row.sources[f] for f in values if f in row.sources}))
            group_sources={**first.sources, **members[0][2]}
            if 'adgroup_budget' in group_sources:group_sources['budget']=group_sources['adgroup_budget']
            groups.append(GroupSpec(id=group_id, name=str(group_key), **attributes,
                budget=BudgetSpec(amount=amount, currency=req.currency, mode=req.budget_mode) if level == 'adgroup' else None,
                ads=ads, sources=group_sources))
        total = unique('budget', [e.get('campaign_budget') for _, e, _ in entries])
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
        if level in ('campaign', 'adgroup'):
            req.structure = StructureSpec(budget_level=level, groups=groups)
        result.append(req)
    return result

