"""The reviewed plan is server-derived; browser/model output cannot approve it."""
from hashlib import sha256
import json
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import HTTPException
from .models import BuildDraft, BuildApproval
from .rules import evaluate
from .structure import campaign_tree, redact_material_secrets


def build_preview(draft: BuildDraft, history: list[dict], policy_version: int, now=None, pending_questions=None):
    rows=[]
    totals={}
    issues=[]
    selected=[]
    seen=set()
    campaigns=[]
    for original in draft.requirements:
        req=evaluate(original,history,now)
        tree,_=campaign_tree(req)
        action='create' if req.included and req.classification not in ('existing','change','ignored') else 'skip'
        campaign_name=req.name or f"{req.business_no or req.id[:8]}-TikTok-{req.objective or '待确认'}"
        data=redact_material_secrets(req.model_dump(mode='json'))
        data['field_states']={q['field']:'conflict' for q in pending_questions or [] if req.id in q['requirement_ids']}
        data.update(action=action,campaign=tree,campaign_name=campaign_name,adgroup_name=campaign_name+'-广告组',ad_name=req.name or req.business_no or req.post_id,
                    skip_reason=('未勾选' if not req.included else '已有广告，不重复创建' if req.classification=='existing' else '已有广告变更，本次不自动修改' if req.classification=='change' else '忽略'))
        rows.append(data)
        if action=='create':
            campaigns.append(tree)
            selected.append(req.id)
            # Multiple groups in one requirement are intentional; separate duplicate
            # requirements cannot evade checking by changing group names or budgets.
            identities=sorted({(a['material_kind'],a['post_id']) for g in tree['adgroups'] for a in g['ads']})
            private_codes=sorted({sha256(a.authorization_code.encode()).hexdigest() for g in req.structure.groups for a in g.ads if a.kind=='authorization_code'}) if req.structure else []
            key=sha256(json.dumps([req.account_id,req.objective,str(req.start_at),str(req.end_at),identities,private_codes],sort_keys=True).encode()).hexdigest()
            if key in seen:
                issues.append({'requirement_id':req.id,'code':'duplicate','field':'included','message':'所选需求中存在相同账户、帖子、目标及排期，请只保留一条','blocking':True})
            seen.add(key)
            issues.extend({'requirement_id':req.id,**i.model_dump()} for i in req.issues if i.blocking)
            if req.budget is not None and req.budget.is_finite() and req.currency:
                key=(req.currency,req.budget_mode or 'unknown')
                totals[key]=totals.get(key,Decimal(0))+req.budget
    if not selected:
        issues.append({'requirement_id':'','code':'empty_selection','field':'included','message':'没有选中需要新增的广告需求','blocking':True})
    for question in pending_questions or []:
        if question.get('blocking',True) and (question.get('scope')=='task' or set(question['requirement_ids']) & set(selected)):
            issues.append({'requirement_id':'','code':'semantic_review','field':question['field'],'message':question['question'],'blocking':True})
    snapshot={'draft_id':draft.id,'version':draft.version,'policy_version':policy_version,'rows':rows,'campaigns':campaigns,
              'counts':{'campaigns':len(campaigns),'adgroups':sum(len(c['adgroups']) for c in campaigns),
                        'ads':sum(len(g['ads']) for c in campaigns for g in c['adgroups']),'skipped':len(rows)-len(selected)},
              'totals':[{'currency':currency,'budget_mode':mode,'amount':str(amount)} for (currency,mode),amount in sorted(totals.items())],
              'issues':issues,'selected_ids':selected,'can_confirm':not issues,
              'platform_checks':[{'item':item,'status':'unverified'} for item in ('账户能力','受众快照','Spark 素材权限')],
              'delivery_note':'确认仅保存搭建方案，真实搭建暂缓；后台账户、受众和素材权限尚需实际校验。'}
    digest=sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    valid=bool(draft.approval and draft.approval.version==draft.version and draft.approval.digest==digest)
    return {**snapshot,'digest':digest,'approval_valid':valid,'approval':draft.approval.model_dump(mode='json') if valid else None}


def approve_preview(draft,preview,actor_id,expected_version,digest,acknowledged):
    if draft.version!=expected_version or preview['digest']!=digest:
        raise HTTPException(409,'预览已变化，请刷新并重新检查后确认')
    if draft.status not in ('draft','attention','approved'):
        raise HTTPException(409,'草稿已进入执行，不能重新确认')
    if not acknowledged:
        raise HTTPException(422,'请先勾选已核对搭建方案')
    if not preview['can_confirm']:
        raise HTTPException(422,'请补齐所选需求的缺失信息，或取消勾选不参与本次搭建的需求')
    if preview['approval_valid']:
        return draft
    snapshot={k:v for k,v in preview.items() if k not in ('digest','approval_valid','approval')}
    draft.approval=BuildApproval(version=draft.version,digest=digest,approved_by=actor_id,
                                approved_at=datetime.now(timezone.utc),snapshot=snapshot)
    draft.status='approved'
    return draft
