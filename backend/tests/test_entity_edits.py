from decimal import Decimal
import pytest
from app.entities import edit_entity
from app.models import Requirement,StructureSpec,GroupSpec,MaterialSpec,BudgetSpec


def campaign():
    return Requirement(id='c',account_id='123',budget=Decimal(30),currency='USD',budget_mode='lifetime',
        structure=StructureSpec(budget_level='adgroup',groups=[
            GroupSpec(id='g1',audience='A',budget=BudgetSpec(amount=10,currency='USD',mode='lifetime'),ads=[MaterialSpec(id='a1',post_id='100')]),
            GroupSpec(id='g2',audience='B',budget=BudgetSpec(amount=20,currency='USD',mode='lifetime'),ads=[MaterialSpec(id='a2',post_id='200')])]))


def test_group_edit_updates_only_target_and_recomputes_total():
    original=campaign()
    changed=edit_entity(original,'g2',{'audience':'New','budget':'25'},'m','u')
    assert changed.budget==35
    assert changed.structure.groups[0].audience=='A'
    assert changed.structure.groups[1].audience=='New'
    assert original.budget==30
    assert changed.structure.groups[1].sources['budget'].message_id=='m'


def test_group_totals_do_not_overwrite_explicit_plan_constraint():
    from app.rules import evaluate
    from app.structure import campaign_tree
    original=campaign();original.budget_constraint=Decimal(30)
    changed=evaluate(edit_entity(original,'g2',{'budget':'25'},'m','u'),[])
    tree,issues=campaign_tree(changed)
    assert tree['planned_total']=='35' and changed.budget==35 and changed.budget_constraint==30
    assert any(i.field=='budget' and i.target_id==changed.id for i in issues)


def test_material_edit_keeps_siblings_and_rejects_wrong_owner_or_conflict():
    changed=edit_entity(campaign(),'a2',{'identity_id':'300'},'m','u')
    assert changed.structure.groups[0].ads[0].identity_id==''
    assert changed.structure.groups[1].ads[0].identity_id=='300'
    with pytest.raises(ValueError):edit_entity(campaign(),'absent',{'name':'x'},'m','u')
    with pytest.raises(ValueError):edit_entity(campaign(),'a1',{'budget':'5'},'m','u')
    with pytest.raises(ValueError):edit_entity(campaign(),'a1',{'post_url':'https://www.tiktok.com/@a/video/999'},'m','u')


def test_mixed_existing_and_new_materials_cannot_skip_the_whole_campaign():
    from app.rules import evaluate
    from datetime import datetime,timezone
    req=campaign();req.objective='followers';req.post_id='100'
    for group in req.structure.groups:
        group.start_at=datetime(2027,1,1,tzinfo=timezone.utc)
        group.end_at=datetime(2027,1,2,tzinfo=timezone.utc);group.timezone='UTC'
    history=[{'account_id':'123','post_id':'100','objective':'followers','start_at':'2027-01-01T00:00:00Z','end_at':'2027-01-02T00:00:00Z','ad_id':'old'}]
    result=evaluate(req,history,datetime(2026,11,4,tzinfo=timezone.utc))
    assert result.classification=='incomplete'
    assert any(i.code=='history_review' and i.field=='included' for i in result.issues)
