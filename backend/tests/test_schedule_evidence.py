from datetime import datetime,timezone
import pytest
from app.models import Requirement,ChatDecision,ChatEdit,SourceRef
from app.entities import evidence_values
from app.provenance import validate_proposal_evidence,supported_source
from app.schedule_evidence import schedule_sources,schedule_suggestions
from test_budget_action import allocation_state


def state_and_group():
    state=allocation_state();req=Requirement.model_validate(state['draft']['requirements'][0]);group=req.structure.groups[0]
    group.end_at=None;group.sources.pop('end_at',None)
    group.sources['start_at']=SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=group.start_at.isoformat(),message_id='start')
    state['draft']['requirements']=[req.model_dump(mode='json')]
    state['messages']=[{'id':'duration','role':'user','content':'跑三天。','business_time':'2026-11-03T09:00:00+00:00'}]
    return state,req,group


def test_elapsed_end_uses_owned_start_and_duration_as_calculation_not_quoted_fact():
    state,req,group=state_and_group();row=evidence_values(req,group.id)
    start=datetime.fromisoformat(row['start_at'].replace('Z','+00:00'))
    from datetime import timedelta
    end=start+timedelta(days=3)
    source=supported_source(state,row,'end_at',end.isoformat(),{'kind':'confirmed','file_id':'','sheet':'','cell':'','raw_value':'invented raw text','message_id':'duration'})
    assert source.kind=='calculated' and source.raw_value==end.isoformat()
    assert group.id+':start_at' in source.derived_from and 'elapsed_seconds:259200' in source.derived_from
    decision=ChatDecision(answer='',edits=[ChatEdit(requirement_id=req.id,target_id=group.id,values={'end_at':end.isoformat()},sources={'end_at':source})])
    validate_proposal_evidence(state,decision)


def test_relative_time_uses_received_business_clock_and_interpretation_zone_not_display_zone():
    state,req,group=state_and_group();group.start_at=None;group.sources.pop('start_at',None)
    state['messages'][0]['content']='以我的上海时区理解：明晚8点开始，跑三天；广告账户展示仍用UTC。'
    state['draft']['requirements']=[req.model_dump(mode='json')]
    values=schedule_sources(state,evidence_values(req,group.id))
    assert values['start_at'][0].astimezone(timezone.utc).isoformat()=='2026-11-04T12:00:00+00:00'
    assert values['end_at'][0].astimezone(timezone.utc).isoformat()=='2026-11-07T12:00:00+00:00'
    assert group.timezone=='UTC'
    assert any(s['field']=='end_at' for s in schedule_suggestions(state))
    state['messages'][0].pop('business_time')
    assert not schedule_sources(state,evidence_values(req,group.id))


@pytest.mark.parametrize('text',['大约跑三天','跑三天或跑五天','跑三个自然日','跑三天，结束时间另行确定'])
def test_uncertain_duration_is_not_resolved(text):
    state,req,group=state_and_group();state['messages'][0]['content']=text
    assert 'end_at' not in schedule_sources(state,evidence_values(req,group.id))


def test_calculation_never_uses_a_sibling_instruction_or_overwrites_invalid_original_end():
    state,req,group=state_and_group();state['messages'][0]['content']=req.structure.groups[1].name+' 跑三天'
    assert not schedule_sources(state,evidence_values(req,group.id))
    state['messages'][0]['content']='跑三天'
    group.sources['end_at']=group.sources['start_at'].model_copy(update={'raw_value':'2028-04-20'})
    assert not schedule_sources(state,evidence_values(req,group.id))


def test_fabricated_calculation_and_wrong_message_do_not_pass():
    state,req,group=state_and_group();row=evidence_values(req,group.id)
    for value,message in [('2040-01-01T09:00:00Z','duration'),('2028-01-04T09:00:00Z','foreign')]:
        with pytest.raises(ValueError):supported_source(state,row,'end_at',value,{'kind':'calculated','file_id':'','sheet':'','cell':'','raw_value':value,'message_id':message})
