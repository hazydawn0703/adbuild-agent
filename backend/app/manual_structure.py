"""Register authenticated form edits as user facts; clients cannot write provenance."""
from datetime import datetime,timezone
from .models import SourceRef
from .entities import GROUP_FIELDS,MATERIAL_FIELDS


def accept_structure(original,structure,actor_id,message_id):
    prior={}
    if original.structure:
        for group in original.structure.groups:
            prior[group.id]=group
            prior.update({ad.id:ad for ad in group.ads})
    changed={}
    def fact(value):return SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=str(value),message_id=message_id,accepted_by=actor_id,accepted_at=datetime.now(timezone.utc))
    for group in structure.groups:
        for node,fields in [(group,GROUP_FIELDS),*[(ad,MATERIAL_FIELDS|{'authorization_code'}) for ad in group.ads]]:
            old=prior.get(node.id);sources={}
            def value(item,field):
                if item is None:return None
                if field in ('budget','currency','budget_mode') and hasattr(item,'ads'):
                    return getattr(item.budget,{'budget':'amount','currency':'currency','budget_mode':'mode'}[field]) if item.budget else None
                return getattr(item,field,None)
            for field in fields:
                current=value(node,field);previous=value(old,field)
                if current!=previous:changed.setdefault(node.id,set()).add(field)
                if current in (None,''):continue
                if old is not None and current==previous and old.sources.get(field):sources[field]=old.sources[field].model_copy(deep=True)
                else:sources[field]=fact('已提供素材授权码' if field=='authorization_code' else current)
            if hasattr(node,'ads') and node.budget and node.budget.allocation_ratio is not None:
                sources['allocation_ratio']=fact(node.budget.allocation_ratio)
            if old is not None:
                for key in ('entity_key','group_key','campaign_key','record_key','account_id','business_no','included','unassigned_budget'):
                    if old.sources.get(key):sources[key]=old.sources[key].model_copy(deep=True)
            # Scope remains server-owned even when a user edits the full form.
            if hasattr(node,'included'):
                if old is not None:node.included=old.included
                else:node.included=True
            node.sources=sources
    present={ad.id for group in structure.groups for ad in group.ads}
    if original.structure:
        for previous in original.structure.groups:
            absent=[ad.model_copy(deep=True) for ad in previous.ads if ad.id not in present]
            if not absent:continue
            for ad in absent:ad.included=False;ad.sources['included']=fact(False)
            target=next((g for g in structure.groups if g.id==previous.id),None)
            if target is not None:target.ads.extend(absent)
            else:structure.groups.append(previous.model_copy(deep=True,update={'ads':absent}))
    return structure,changed


def form_facts(draft,message_id,actor_id):
    """Only registered business values enter the conversation, never client sources."""
    facts=[]
    for req in draft.requirements:
        nodes=[req,*[n for g in req.structure.groups for n in [g,*g.ads]]] if req.structure else [req]
        for node in nodes:
            for field,source in node.sources.items():
                if source.kind!='confirmed' or source.message_id!=message_id or source.accepted_by!=actor_id:continue
                if field in ('structure','authorization_code'):continue
                if field not in GROUP_FIELDS|MATERIAL_FIELDS|{'objective','budget_constraint','budget_level','platform','account_id','included','allocation_ratio'}:continue
                facts.append({'field':'budget' if field=='budget_constraint' else field,'answer':source.raw_value,'requirement_ids':[req.id],'target_ids':[node.id]})
    return facts
