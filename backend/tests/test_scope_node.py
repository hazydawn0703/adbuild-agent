import asyncio
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from app.agent import conversation_graph,validate_decision,ProposalError
from app.models import TaskScope,ScopeSelector,ChatDecision,ChatEdit,DemandCandidate


def fixture_state():
    code='request-'+uuid4().hex[:8]
    return code,{'messages':[{'id':'source','role':'user','content':f'本次只搭建业务编号 {code}，其他记录仅作参考'}],
                 'draft':{'requirements':[{'id':'wanted','business_no':code,'included':False,'objective':'video_views','post_id':'7700000000000000001'},
                                           {'id':'reference','business_no':'other','included':False,'post_id':'7700000000000000002'}]},
                 'context':{},'scope':TaskScope().model_dump(mode='json')}


def test_scope_has_own_schema_and_is_not_optional_in_field_response(monkeypatch):
    code,state=fixture_state();calls=[]
    class ScopeModel:
        async def ainvoke(self,_):
            calls.append('scope')
            return TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source'])
    class FieldModel:
        async def ainvoke(self,_):calls.append('fields');return ChatDecision(answer='范围已识别')
    monkeypatch.setattr('app.agent.llm',lambda schema:ScopeModel() if schema is TaskScope else FieldModel())
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'dedicated'}}))
    assert calls==['scope','fields']
    assert result['scope']['status']=='resolved'
    assert result['decision']['scope']['selectors'][0]['values']==[code]


def test_incomplete_scope_returns_structured_question_without_field_call(monkeypatch):
    class ScopeModel:
        async def ainvoke(self,_):return TaskScope(reason='请选择本次业务编号')
    def model(schema):
        assert schema is TaskScope
        return ScopeModel()
    monkeypatch.setattr('app.agent.llm',model)
    _,state=fixture_state()
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'pending'}}))
    assert result['decision']['questions'][0]['field']=='scope'
    assert result['progress']['phase']=='scope_pending'


def test_normal_field_edit_skips_unrelated_scope_resolution(monkeypatch):
    class Fields:
        async def ainvoke(self,_):return ChatDecision(answer='edit',edits=[ChatEdit(requirement_id='wanted',values={'budget':'40'})])
    def model(schema):
        assert schema is ChatDecision
        return Fields()
    monkeypatch.setattr('app.agent.llm',model)
    code,state=fixture_state();state['messages']=[{'id':'source','role':'user','content':f'仅将业务编号 {code} 的预算改为40'}]
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'edit'}}))
    assert result['decision']['edits'][0]['values']=={'budget':'40'}


def test_included_is_action_grounded_in_scope_and_noop_is_removed():
    code,state=fixture_state()
    state['scope']=TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source']).model_dump(mode='json')
    decision=ChatDecision(answer='selection',edits=[ChatEdit(requirement_id='wanted',values={'included':True}),ChatEdit(requirement_id='reference',values={'included':False})])
    validate_decision(state,decision)
    assert len(decision.edits)==1
    assert decision.edits[0].sources['included'].message_id=='source'
    state['scope']=TaskScope().model_dump(mode='json')
    state['messages']=[{'id':'source','role':'user','content':f'仅将业务编号 {code} 的预算改为40'}]
    with pytest.raises(ProposalError,match='范围改变'):
        validate_decision(state,ChatDecision(answer='bad',edits=[ChatEdit(requirement_id='wanted',values={'included':True})]))


def test_duplicate_addition_is_repaired_before_checkpoint_success(monkeypatch):
    code,state=fixture_state();scope=TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source'])
    state['scope']=scope.model_dump(mode='json');attempts=[]
    class Fields:
        async def ainvoke(self,_):
            attempts.append(1)
            return ChatDecision(answer='duplicate',additions=[DemandCandidate(values={'post_id':'7700000000000000001','objective':'video_views'})]) if len(attempts)==1 else ChatDecision(answer='请核对现有需求')
    monkeypatch.setattr('app.agent.llm',lambda schema:Fields())
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'duplicate'}}))
    assert len(attempts)==2 and result['decision']['additions']==[]


def test_checkpoint_resume_reuses_scope_and_execution_position(monkeypatch):
    code,state=fixture_state();calls={'scope':0,'fields':0}
    class Scope:
        async def ainvoke(self,_):
            calls['scope']+=1
            return TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source'])
    class Fields:
        async def ainvoke(self,_):
            calls['fields']+=1
            if calls['fields']==1:raise TimeoutError('injected')
            return ChatDecision(answer='恢复完成')
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    async def scenario():
        graph=conversation_graph(InMemorySaver());config={'configurable':{'thread_id':'resume'}}
        with pytest.raises(TimeoutError):await graph.ainvoke(state,config)
        saved=await graph.aget_state(config)
        assert saved.next==('understand',)
        result=await graph.ainvoke(None,config)
        assert result['decision']['scope']['status']=='resolved'
    asyncio.run(scenario())
    assert calls=={'scope':1,'fields':2}


def test_explicit_new_post_is_not_lost_when_existing_candidates_do_not_contain_it(monkeypatch):
    post='7700000000000000099'
    _,state=fixture_state()
    state['messages']=[{'id':'source','role':'user','content':f'新增帖子{post}的广告，目标涨粉，预算40美元'}]
    class Scope:
        async def ainvoke(self,_):return TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='post_id',values=[post])],message_ids=['source'])
    class Fields:
        async def ainvoke(self,_):return ChatDecision(answer='新增候选',additions=[DemandCandidate(values={'post_id':post,'objective':'followers','budget':'40'})])
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'new-post'}}))
    assert result['decision']['additions'][0]['values']['post_id']==post


def test_new_batch_cannot_bypass_duplicate_check_by_datetime_spelling():
    _,state=fixture_state()
    state['messages']=[{'id':'source','role':'user','content':'请给同帖新批次投放，帖子7700000000000000001，目标视频观看，开始2027-01-01T10:00:00Z，结束2027-01-03T10:00:00Z'}]
    state['draft']['requirements'][0].update(start_at='2027-01-01T10:00:00+00:00',end_at='2027-01-03T10:00:00+00:00',timezone='UTC')
    with pytest.raises(ProposalError,match='当前草稿已有'):
        validate_decision(state,ChatDecision(answer='duplicate',additions=[DemandCandidate(values={'post_id':'7700000000000000001','objective':'video_views','start_at':'2027-01-01T10:00:00Z','end_at':'2027-01-03T10:00:00Z','timezone':'UTC'})]))


def test_empty_intake_records_known_scope_and_waits_for_real_input(monkeypatch):
    code,state=fixture_state();state['draft']=None;calls=[]
    class Scope:
        async def ainvoke(self,_):
            calls.append('scope')
            return TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source'])
    def model(schema):
        assert schema is TaskScope,'No field model is needed before material input exists'
        return Scope()
    monkeypatch.setattr('app.agent.llm',model)
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'awaiting-input'}}))
    assert calls==['scope']
    assert result['progress']['phase']=='awaiting_input'
    assert result['decision']['scope']['status']=='resolved'
    assert result['decision']['additions']==[]
    assert result['decision']['edits']==[]
    state['scope']=result['scope']
    def no_model(schema):pytest.fail('Known scope with no new input must not invoke a model')
    monkeypatch.setattr('app.agent.llm',no_model)
    repeated=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'awaiting-input-again'}}))
    assert repeated['progress']['phase']=='awaiting_input'


@pytest.mark.parametrize('write',['edits','additions'])
def test_history_query_cannot_modify_or_add_draft(write):
    _,state=fixture_state()
    state['scope']={'action':'history','status':'resolved'}
    change={'edits':[ChatEdit(requirement_id='wanted',values={'budget':'50'})]} if write=='edits' else {'additions':[DemandCandidate(values={'post_id':'7700000000000000009','objective':'followers'})]}
    with pytest.raises(ProposalError,match='历史查询为只读'):
        validate_decision(state,ChatDecision(answer='bad',**change))


def test_latest_history_query_reenters_scope_even_when_build_range_is_known(monkeypatch):
    code,state=fixture_state()
    state['scope']=TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source']).model_dump(mode='json')
    state['messages'].append({'id':'query','role':'user','content':'请查询这条需求的历史投放记录'})
    state['history_records']=[{'post_id':'7700000000000000001','business_no':code,'ad_id':'7700000000000000091'}]
    calls=[]
    class Scope:
        async def ainvoke(self,_):
            calls.append('scope')
            return TaskScope(action='history',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source','query'])
    class Fields:
        async def ainvoke(self,messages):
            import json
            payload=json.loads(messages[-1][1]);calls.append('fields')
            assert payload['history_query']['total']==1
            return ChatDecision(answer='当前项目导入快照中有一条记录')
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'switch-history'}}))
    assert calls==['scope','fields'] and result['decision']['scope']['action']=='history'


def test_explicit_edit_after_history_returns_to_edit_without_changing_selection(monkeypatch):
    code,state=fixture_state()
    state['scope']=TaskScope(action='history',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['source']).model_dump(mode='json')
    state['messages'].append({'id':'edit','role':'user','content':f'仅将业务编号 {code} 的预算改为40'})
    calls=[]
    class Scope:
        async def ainvoke(self,_):
            calls.append('scope')
            return TaskScope(action='edit',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=['edit'])
    class Fields:
        async def ainvoke(self,_):calls.append('fields');return ChatDecision(answer='预算提案',edits=[ChatEdit(requirement_id='wanted',values={'budget':'40'})])
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke(state,{'configurable':{'thread_id':'switch-edit'}}))
    assert calls==['scope','fields'] and result['decision']['scope']['action']=='edit'
    assert result['decision']['edits'][0]['values']=={'budget':'40'}
    assert [r['included'] for r in result['draft']['requirements']]==[False,False]
