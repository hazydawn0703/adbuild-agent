from datetime import datetime,timezone
from decimal import Decimal
import pytest
from app.models import Requirement,StructureSpec,GroupSpec,MaterialSpec,BudgetAllocation,ChatDecision
from app.provenance import allocation_edits,validate_proposal_evidence,supported_source
from app.structure import campaign_tree


def allocation_state():
    start=datetime(2028,1,10,9,tzinfo=timezone.utc);end=datetime(2028,1,15,9,tzinfo=timezone.utc)
    req=Requirement(id='plan',account_id='123',objective='followers',currency='USD',budget_mode='lifetime',
        structure=StructureSpec(budget_level='adgroup',groups=[GroupSpec(id=f'g{i}',name=f'Group {i}',
            audience=f'Audience {i}',language='en',start_at=start,end_at=end,timezone='UTC',
            ads=[MaterialSpec(id=f'a{i}',post_id=str(i*100),identity_id='999')]) for i in range(1,4)]))
    text='总额100.01美元，三个广告组按表顺序1:1:1分配；USD lifetime en UTC；2028-01-10T09:00:00Z 至 2028-01-15T09:00:00Z；Audience 1；Audience 2；Audience 3；帖子100；帖子200；帖子300；身份999'
    return {'draft':{'requirements':[req.model_dump(mode='json')]},'messages':[{'id':'m','role':'user','content':text}]}


def test_budget_action_preserves_all_nodes_and_exact_cents_with_canonical_sources():
    state=allocation_state();allocation=BudgetAllocation(requirement_id='plan',total='100.01',group_weights={'g3':1,'g1':1,'g2':1},message_id='m')
    edits=allocation_edits(state,[allocation]);decision=ChatDecision(answer='',edits=edits)
    validate_proposal_evidence(state,decision)
    changed=Requirement.model_validate({**state['draft']['requirements'][0],**edits[0].values})
    tree,issues=campaign_tree(changed)
    assert not issues
    assert [g['budget']['amount'] for g in tree['adgroups']]==['33.34','33.34','33.33']
    assert [g['ads'][0]['post_id'] for g in tree['adgroups']]==['100','200','300']
    assert all(g['start_at']=='2028-01-10T09:00:00+00:00' for g in tree['adgroups'])
    assert edits[0].sources['structure'].kind=='calculated' and edits[0].sources['structure'].derived_from
    assert state['draft']['requirements'][0]['budget'] is None
    validate_proposal_evidence(state,decision)  # Same verifier can run again at acceptance.


def test_budget_action_rejects_foreign_group_and_unsupported_amount():
    state=allocation_state()
    for weights,total in [({'g1':1,'g2':1,'foreign':1},'100.01'),({'g1':1,'g2':1,'g3':1},'300.03')]:
        with pytest.raises(ValueError):allocation_edits(state,[BudgetAllocation(requirement_id='plan',total=total,group_weights=weights,message_id='m')])


def test_message_raw_value_cannot_replace_actual_message_evidence():
    state=allocation_state()
    offered={'kind':'confirmed','file_id':'','sheet':'','cell':'','message_id':'m','raw_value':'MODEL EXPLANATION'}
    assert supported_source(state,{},'budget',Decimal('100.01'),offered).raw_value=='100.01'
    with pytest.raises(ValueError):supported_source(state,{},'budget',Decimal('300.03'),offered)


def test_truncated_structure_cannot_become_a_pending_validated_proposal():
    state=allocation_state()
    decision=ChatDecision(answer='',edits=[{'requirement_id':'plan','values':{'budget':'100.01','structure':{
        'budget_level':'adgroup','groups':[{'id':f'g{i}','budget':{'allocation_ratio':1}} for i in range(1,4)]}}}])
    with pytest.raises(ValueError,match='完整实体'):validate_proposal_evidence(state,decision)

