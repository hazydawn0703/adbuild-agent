import asyncio
import json
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.models import Requirement, TaskScope, ScopeSelector, ChatDecision, Clarification
from app.scope import select_requirements, validate_scope, compact_state
from app.agent import conversation_graph, validate_decision, ProposalError


def selection(value, message_id='m'):
    return TaskScope(action='new', status='resolved', selectors=[ScopeSelector(field='business_no',values=[value])], message_ids=[message_id])


@pytest.mark.parametrize('count',[3,31,301])
def test_task_selection_is_independent_of_irrelevant_record_count(count):
    wanted=str(uuid4())
    messages=[{'id':'m','role':'user','content':f'本次只搭建业务编号 {wanted}，其他作为历史资料'}]
    rows=[Requirement(id=str(i),business_no=str(uuid4())) for i in range(count)]
    rows.insert(count//2,Requirement(id='target',business_no=wanted))
    selected=select_requirements(rows,selection(wanted),messages)
    assert [r.id for r in selected if r.included]==['target']
    assert len(selected)==count+1


def test_unresolved_task_never_defaults_to_all_records():
    result=select_requirements([Requirement(id='r')],TaskScope(),[])
    assert not result[0].included


def test_selection_requires_actual_user_evidence():
    scope=selection('237')
    with pytest.raises(ValueError,match='引用'):
        validate_scope(scope,[{'id':'m','role':'assistant','content':'237'}])
    with pytest.raises(ValueError,match='依据'):
        validate_scope(scope,[{'id':'m','role':'user','content':'只搭建业务编号 238'}])


def test_all_records_requires_explicit_scope_and_applies_platform_filter():
    messages=[{'id':'m','role':'user','content':'这批全部 TikTok 需求需要新搭建'}]
    scope=TaskScope(action='new',status='resolved',all_records=True,message_ids=['m'],selectors=[ScopeSelector(field='platform',values=['tiktok'])])
    rows=[Requirement(id='a',post_url='https://www.tiktok.com/@a/video/123'),Requirement(id='b',post_url='https://example.org/video/123')]
    assert [r.id for r in select_requirements(rows,scope,messages) if r.included]==['a']
    scope.selectors=[]
    with pytest.raises(ValueError,match='明确依据'):
        select_requirements(rows,scope,[{'id':'m','role':'user','content':'看看文件'}])


def test_predraft_task_question_is_valid_but_requirement_question_is_not():
    state={'messages':[],'draft':None}
    validate_decision(state,ChatDecision(answer='需要范围',questions=[Clarification(id='q',field='scope',scope='task',question='本次选择哪些记录？')]))
    with pytest.raises(ProposalError,match='有效需求'):
        validate_decision(state,ChatDecision(answer='bad',questions=[Clarification(id='q',field='budget',scope='requirements',requirement_ids=['fake'],question='预算？')]))


def test_invalid_question_never_enters_completed_checkpoint(monkeypatch):
    class Model:
        async def ainvoke(self,messages):
            return ChatDecision(answer='bad',questions=[Clarification(id='q',field='budget',requirement_ids=['fake'],question='预算？')])
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    async def scenario():
        graph=conversation_graph(InMemorySaver())
        config={'configurable':{'thread_id':'invalid-question'}}
        with pytest.raises(ProposalError):
            await graph.ainvoke({'messages':[],'draft':None,'context':{}},config)
        checkpoint=await graph.aget_state(config)
        assert not checkpoint.values.get('decision')
        assert checkpoint.values['progress']['phase']=='evidence'
    asyncio.run(scenario())


def test_predraft_question_survives_graph_checkpoint(monkeypatch):
    question=Clarification(id='q',field='scope',scope='task',question='本次选择哪些记录？')
    class Model:
        async def ainvoke(self,messages):return ChatDecision(answer='先明确范围',questions=[question])
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    async def scenario():
        graph=conversation_graph(InMemorySaver())
        config={'configurable':{'thread_id':'predraft'}}
        result=await graph.ainvoke({'messages':[],'draft':None,'context':{}},config)
        checkpoint=await graph.aget_state(config)
        assert result['decision']['questions'][0]['scope']=='task'
        assert checkpoint.values['decision']==result['decision']
    asyncio.run(scenario())


def test_context_only_contains_selected_or_explicitly_referenced_rows():
    state={'messages':[{'role':'user','content':'检查本次方案'}],'draft':{'id':'d','requirements':[
        {'id':'a','included':True,'post_id':'1','history':[{'ad_id':str(i),'raw':{'large':'x'*1000}} for i in range(20)]},
        *[{'id':f'other-{i}','included':False,'post_id':str(i+2),'history':[{'raw':{'large':'x'*1000}}]} for i in range(500)]
    ]}}
    payload=compact_state(state)
    assert len(payload['draft']['requirements'])==1
    assert payload['draft']['requirements'][0]['history_coverage']=={'returned':8,'total':20,'complete':False}
    assert payload['coverage']['excluded_records']==500
    assert len(json.dumps(payload))<20000
    assert payload['catalog_coverage']=={'returned':100,'total':501,'complete':False}
    assert 'x'*1000 not in json.dumps(payload)


def test_refresh_task_questions_without_draft():
    from app.intake import refresh
    from app.models import IntakeTask
    task=IntakeTask(id='t',project_id='p',semantic_questions=[Clarification(id='q',field='scope',scope='task',question='选哪些？')])
    refresh(task,None)
    assert task.stage=='clarifying' and task.questions[0].id=='q'


def test_material_credential_and_its_source_never_enter_model_context():
    secret='private-material-value'
    payload=compact_state({'messages':[{'role':'user','content':f'素材凭证为 {secret}'}],'draft':{'requirements':[{
        'id':'r','included':True,'structure':{'groups':[{'ads':[{'authorization_code':secret,'sources':{'authorization_code':{'raw_value':secret}}}]}]}
    }]}})
    assert secret not in json.dumps(payload)
    ad=payload['draft']['requirements'][0]['structure']['groups'][0]['ads'][0]
    assert ad['authorization_present'] is True
    assert 'authorization_code' not in ad
