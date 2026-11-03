from app.models import Requirement,StructureSpec
from app.structure import campaign_tree


def test_missing_nodes_do_not_inherit_representative_plan_values():
    req=Requirement(id='p',name='Plan',objective='traffic',post_id='100',identity_id='999',
        audience='Representative audience',language='en',cta='LEARN_MORE',landing_url='https://example.org',
        structure=StructureSpec.model_validate({'groups':[{'id':'g','ads':[{'id':'a'}]}]}))
    tree,issues=campaign_tree(req)
    assert tree['adgroups'][0]['audience'] is None
    assert tree['adgroups'][0]['ads'][0]['post_id']==''
    assert {'audience','language','post_id','identity_id','landing_url'} <= {i.field for i in issues}

