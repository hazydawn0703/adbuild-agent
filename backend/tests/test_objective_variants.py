from decimal import Decimal
from app.entities import objective_variant
from app.models import SourceRef,Issue
from test_entity_assembly import parse


def test_objective_variant_keeps_all_groups_materials_and_maps_issue_targets():
    base=parse('campaign',('G1','G1','G2'))[0]
    ad=base.structure.groups[0].ads[0]
    base.issues.append(Issue(code='invalid_precision',field='post_id',target_id=ad.id,message='precision unresolved'))
    changed=objective_variant(base,'variant','video_views',Decimal(200),'m','actor',{})
    assert changed.structure is not None and [len(g.ads) for g in changed.structure.groups]==[2,1]
    assert [a.post_id for g in changed.structure.groups for a in g.ads]==['100','101','102']
    assert changed.structure.groups[0].id!=base.structure.groups[0].id
    assert changed.issues[0].target_id==changed.structure.groups[0].ads[0].id
    assert base.structure.groups[0].ads[0].id==ad.id
    assert changed.budget==200 and changed.objective=='video_views'


def test_goal_scoped_defaults_are_not_carried_to_inapplicable_variant():
    base=parse('campaign',('G1',))[0];group=base.structure.groups[0]
    group.sources['audience']=SourceRef(kind='template',context_version=1,file_id='',sheet='',cell='',raw_value=group.audience)
    changed=objective_variant(base,'variant','traffic',Decimal(100),'m','actor',
        {'objectives':['followers'],'account_ids':['123'],'defaults':{'audience':group.audience}})
    assert changed.structure.groups[0].audience==''
    assert 'audience' not in changed.structure.groups[0].sources
