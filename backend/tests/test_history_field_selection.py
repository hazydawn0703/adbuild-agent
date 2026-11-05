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


def test_adopting_audience_does_not_authorize_overwriting_the_current_budget():
    req,group=historical_group();row=evidence_values(req,group.id);row['budget']='100'
    h={**req.history[1],'budget':'200'};row['history']=[h]
    from app.provenance import history_reference
    source=history_reference(h,'budget')
    state={'messages':[{'id':'m','role':'user','content':'受众沿用历史Batch 2，其他字段以本次表为准，不沿用历史预算或排期。'}]}
    with pytest.raises(ValueError,match='不能覆盖'):supported_source(state,row,'budget','200',source)
    state['messages'].append({'id':'new','role':'user','content':'预算改用历史Batch 2，采纳预算200'})
    assert supported_source(state,row,'budget','200',source).kind=='history'


def test_explicit_batch_has_independent_group_values_without_reconfirmation():
    from app.history_fields import history_suggestions
    req,first=historical_group();second=req.structure.groups[1];second.audience=None
    req.history += [{**h,'target_id':second.ads[0].id,'post_id':second.ads[0].post_id,'audience':'Other '+h['audience'],'source_row':h['source_row']+10} for h in list(req.history)]
    state={'draft':{'requirements':[req.model_dump(mode='json')]},'messages':[{'id':'m','role':'user','content':'所有广告组的受众仅沿用历史批次「Batch 2」，其他字段以本次表为准，不沿用历史预算或排期。'}]}
    suggestions=history_suggestions(state)
    assert [(s['target_id'],s['value']) for s in suggestions]==[(first.id,'Audience 2'),(second.id,'Other Audience 2')]
    for suggestion in suggestions:
        assert supported_source(state,evidence_values(req,suggestion['target_id']),'audience',suggestion['value'],suggestion['source']).kind=='history'
    state['messages'].append({'id':'later','role':'user','content':'受众不要沿用历史Batch 2。'})
    assert not history_suggestions(state)


def test_explicit_batch_cannot_cross_group_business_or_missing_material():
    from app.history_fields import history_suggestions
    req,group=historical_group();state={'draft':{'requirements':[req.model_dump(mode='json')]},'messages':[{'id':'m','role':'user','content':'其他业务编号 OTHER 的受众沿用历史Batch 2'}]}
    assert not history_suggestions(state)
    state['messages'][0]['content']=f'{group.name}的受众沿用历史Batch 2'
    assert len(history_suggestions(state))==1
    req.structure.groups[0].ads.append(req.structure.groups[1].ads[0])
    state['draft']['requirements']=[req.model_dump(mode='json')]
    assert not history_suggestions(state)
    value,ref=select(*historical_group(),'audience',PREFIX+'Batch 2')
    with pytest.raises(ValueError,match='完整覆盖'):supported_source(state,evidence_values(req,group.id),'audience',value,ref)
