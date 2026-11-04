"""Test double for LangChain's include_raw structured output envelope."""
from langchain_core.messages import AIMessage


def model_response(parsed):
    if hasattr(parsed,'model_dump'):
        parsed=parsed.model_dump(mode='json',exclude_defaults=True,exclude_computed_fields=True)
    return {'raw':AIMessage(content=''), 'parsed':parsed, 'parsing_error':None}


def row_bindings(fields):
    from app.models import Binding
    return {field:Binding(kind='row',column=column) for field,column in fields.items()}


def campaign_fixture(**values):
    """Explicit singleton hierarchy for tests unrelated to source-row assembly."""
    from app.models import Requirement,StructureSpec,GroupSpec,MaterialSpec
    row=Requirement(**{'language':'all',**values})
    if row.structure is None:
        row.structure=StructureSpec(budget_level='campaign',groups=[GroupSpec(id=row.id+':group',ads=[MaterialSpec(
            id=row.id+':ad',post_id=row.post_id,post_url=row.post_url,identity_id=row.identity_id,
            cta=row.cta,landing_url=row.landing_url,sources=dict(row.sources))])])
    for group in row.structure.groups:
        group.audience=group.audience if group.audience is not None else row.audience
        group.language=group.language if group.language is not None else row.language
        group.start_at=group.start_at or row.start_at
        group.end_at=group.end_at or row.end_at
        group.timezone=group.timezone or row.timezone
        if not group.ads:
            group.ads=[MaterialSpec(id=group.id+':ad',post_id=row.post_id,post_url=row.post_url,identity_id=row.identity_id)]
        for ad in group.ads:
            for field in ('identity_id','cta','landing_url'):
                if not getattr(ad,field):setattr(ad,field,getattr(row,field))
    return row
