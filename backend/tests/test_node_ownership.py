from app.models import Requirement,StructureSpec
from app.structure import campaign_tree
import pytest


def test_missing_nodes_do_not_inherit_representative_plan_values():
    req=Requirement(id='p',name='Plan',objective='traffic',post_id='100',identity_id='999',
        audience='Representative audience',language='en',cta='LEARN_MORE',landing_url='https://example.org',
        structure=StructureSpec.model_validate({'groups':[{'id':'g','ads':[{'id':'a'}]}]}))
    tree,issues=campaign_tree(req)
    assert tree['adgroups'][0]['audience'] is None
    assert tree['adgroups'][0]['ads'][0]['post_id']==''
    assert {'audience','language','post_id','identity_id','landing_url'} <= {i.field for i in issues}


def test_plan_edit_cannot_broadcast_group_schedule_or_material_identity():
    from app.entities import check_edit_owner
    req=Requirement(id='p',structure=StructureSpec.model_validate({'budget_level':'campaign','groups':[
        {'id':'g1','ads':[{'id':'a1'}]},{'id':'g2','ads':[{'id':'a2'}]}]}))
    for field in ('end_at','audience','language','post_id','identity_id'):
        with pytest.raises(ValueError,match='目标实体'):check_edit_owner(req,'p',{field})
    check_edit_owner(req,'g2',{'end_at'})
    check_edit_owner(req,'a2',{'identity_id'})
