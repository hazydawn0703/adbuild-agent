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


def test_echoed_budget_level_does_not_mask_independent_edit_validation():
    state=allocation_state()
    decision=ChatDecision(answer='',edits=[{'requirement_id':'plan','values':{'budget_level':'adgroup','currency':'USD'}}])
    validate_proposal_evidence(state,decision)
    assert decision.edits[0].values=={'currency':'USD'}
    state['draft']['requirements'][0]['issues']=[{'code':'invalid_budget_level_conflict','field':'budget_level','message':'Conflicting explicit levels'}]
    decision=ChatDecision(answer='',edits=[{'requirement_id':'plan','values':{'budget_level':'adgroup','currency':'USD'}}])
    with pytest.raises(ValueError,match='单独'):validate_proposal_evidence(state,decision)


def test_existing_missing_fields_do_not_prevent_an_independent_structure_edit():
    state=allocation_state();row=state['draft']['requirements'][0]
    row['structure']['groups'][0]['audience']=None
    structure=__import__('copy').deepcopy(row['structure'])
    decision=ChatDecision(answer='',edits=[{'requirement_id':'plan','values':{'structure':structure}}])
    validate_proposal_evidence(state,decision)
    assert decision.edits[0].values['structure']['groups'][0]['audience'] is None


def test_budget_action_rejects_foreign_group_and_unsupported_amount():
    state=allocation_state()
    for weights,total in [({'g1':1,'g2':1,'foreign':1},'100.01'),({'g1':1,'g2':1,'g3':1},'300.03')]:
        with pytest.raises(ValueError):allocation_edits(state,[BudgetAllocation(requirement_id='plan',total=total,group_weights=weights,message_id='m')])


@pytest.mark.parametrize('instruction',['三个广告组各占1/3分配','三个广告组按表顺序1/3:1/3:1/3分配','三个广告组平均分配'])
def test_exact_fraction_and_equal_split_do_not_need_decimal_approximations(instruction):
    state=allocation_state();state['messages'][0]['content']=state['messages'][0]['content'].replace('三个广告组按表顺序1:1:1分配',instruction)
    allocation=BudgetAllocation(requirement_id='plan',total='100.01',group_weights={f'g{i}':'1/3' for i in range(1,4)},message_id='m')
    edits=allocation_edits(state,[allocation]);decision=ChatDecision(answer='',edits=edits);validate_proposal_evidence(state,decision)
    changed=Requirement.model_validate({**state['draft']['requirements'][0],**edits[0].values})
    assert [g['budget']['amount'] for g in campaign_tree(changed)[0]['adgroups']]==['33.34','33.34','33.33']


def test_message_raw_value_cannot_replace_actual_message_evidence():
    state=allocation_state()
    offered={'kind':'confirmed','file_id':'','sheet':'','cell':'','message_id':'m','raw_value':'MODEL EXPLANATION'}
    assert supported_source(state,{},'budget',Decimal('100.01'),offered).raw_value=='100.01'
    with pytest.raises(ValueError):supported_source(state,{},'budget',Decimal('300.03'),offered)


def test_message_budget_uses_same_amount_rules_without_truncating_numeric_commas():
    from app.provenance import message_supports
    for text,values in [('预算1,250美元',['1','1250']),('预算1.250美元',['1.25','1250']),('预算-100美元',['100'])]:
        for value in values:assert not message_supports('budget',value,text)
    assert message_supports('budget','1250.00','预算1,250.00美元')


def test_truncated_structure_cannot_become_a_pending_validated_proposal():
    state=allocation_state()
    decision=ChatDecision(answer='',edits=[{'requirement_id':'plan','values':{'budget':'100.01','structure':{
        'budget_level':'adgroup','groups':[{'id':f'g{i}','budget':{'allocation_ratio':1}} for i in range(1,4)]}}}])
    with pytest.raises(ValueError,match='完整实体'):validate_proposal_evidence(state,decision)
