import pytest
from app.models import Requirement
from app.entities import evidence_values
from app.history_fields import PREFIX,choices,select
from app.provenance import supported_source
from evals.prepare_campaign_answers import permitted_facts
from test_budget_action import allocation_state


def historical_group():
    req=Requirement.model_validate(allocation_state()['draft']['requirements'][0]);group=req.structure.groups[0];group.audience=None
    req.history=[{'target_id':group.ads[0].id,'post_id':group.ads[0].post_id,'account_id':req.account_id,'name':'Batch '+str(i),'audience':'Audience '+str(i),'source_file':'export','source_sheet':'Ads','source_row':i+1} for i in (1,2)]
    return req,group


def test_batch_options_and_answer_keep_actual_group_owned_reference():
    req,group=historical_group();assert choices(req,group,'audience')==[PREFIX+'Batch 1',PREFIX+'Batch 2']
    value,source=select(req,group,'audience',PREFIX+'Batch 2');assert value=='Audience 2' and source.cell=='audience:3'
    state={'messages':[{'id':'answer','role':'user','answers':[{'field':'audience','target_ids':[group.id],'answer':PREFIX+'Batch 2'}]}]}
    assert supported_source(state,evidence_values(req,group.id),'audience',value,source).kind=='history'
    wrong_value,wrong_source=select(req,group,'audience',PREFIX+'Batch 1')
    with pytest.raises(ValueError,match='选定'):supported_source(state,evidence_values(req,group.id),'audience',wrong_value,wrong_source)
    with pytest.raises(ValueError,match='不同批次'):supported_source({'messages':[]},evidence_values(req,group.id),'audience',value,source)


def test_missing_sibling_foreign_account_and_conflicting_snapshot_are_not_choices():
    req,group=historical_group();req.history[1]['account_id']='other'
    assert choices(req,group,'audience')==[PREFIX+'Batch 1']
    with pytest.raises(ValueError):select(req,req.structure.groups[1],'audience',PREFIX+'Batch 1')
    req.history.append({**req.history[0],'audience':'Conflict','source_row':9})
    assert not choices(req,group,'audience')


def test_declared_history_answer_is_a_field_source_choice_not_candidate_scope():
    facts,entities=permitted_facts({'batch_name':'Batch 2','adopt_fields':['audience']})
    assert facts=={'audience':PREFIX+'Batch 2'} and not entities
