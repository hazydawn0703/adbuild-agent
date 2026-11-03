from model_response_fixture import model_response
import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import validate_decision,ProposalError,conversation_graph
from app.models import ChatDecision,DemandCandidate,SourceRef,TaskScope,ScopeSelector,Requirement,BuildDraft,IntakeTask,Clarification
from app.scope import compact_state,validate_scope
from app.intake import refresh,question_observation


def messages(text):return [{'id':'user-message','role':'user','content':text}]


def test_message_only_candidate_has_verified_field_provenance():
    post=str(7*10**18+int(uuid4().hex[:8],16))
    state={'messages':messages(f'新增帖子 {post}，目标涨粉，预算45美元，总预算。'),'draft':None,'context':{}}
    decision=ChatDecision(answer='新需求候选',additions=[DemandCandidate(values={'post_id':post,'objective':'followers','budget':'45','budget_mode':'lifetime'})])
    validate_decision(state,decision)
    assert all(s.message_id=='user-message' for s in decision.additions[0].sources.values())
    assert decision.additions[0].values['budget']==Decimal('45')


@pytest.mark.parametrize('values',[{'post_id':'789','objective':'followers'},{'post_id':'789'},{'objective':'followers'}])
def test_new_candidate_cannot_fabricate_identity_or_omit_required_identity(values):
    with pytest.raises(ProposalError):
        validate_decision({'messages':messages('请先讨论需求'),'draft':None},ChatDecision(answer='candidate',additions=[DemandCandidate(values=values)]))


def test_message_candidate_is_checkpointed_as_proposal_without_creation(monkeypatch):
    class Model:
        async def ainvoke(self,_):return model_response(ChatDecision(answer='candidate',additions=[DemandCandidate(values={'post_id':'7700123456789012345','objective':'video_views'})]))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    result=asyncio.run(conversation_graph(InMemorySaver()).ainvoke({'messages':messages('新增帖子7700123456789012345，目标视频观看'),'draft':None,'context':{},'scope':TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='post_id',values=['7700123456789012345'])],message_ids=['user-message']).model_dump(mode='json')},{'configurable':{'thread_id':'candidate'}}))
    assert result['draft'] is None
    assert result['decision']['additions'][0]['values']['post_id']=='7700123456789012345'


def test_prior_multi_goal_instruction_survives_later_continue_message():
    from app.models import RequirementExpansion
    state={'messages':messages('业务编号731同时做涨粉和视频观看，预算各50美元。')+[{'id':'continue','role':'user','content':'继续分析'}],
           'draft':{'requirements':[{'id':'r','business_no':'731','budget':'50','objective':'followers','sources':{}}]}}
    decision=ChatDecision(answer='split',expansions=[RequirementExpansion(base_requirement_id='r',objective='video_views',budget='50')])
    validate_decision(state,decision)


def test_owner_name_is_not_an_implicit_additional_identity_filter():
    scope=TaskScope(action='new',status='resolved',message_ids=['user-message'],selectors=[ScopeSelector(field='business_no',values=['731']),ScopeSelector(field='name',values=['Example operator'])])
    with pytest.raises(ValueError,match='额外名称筛选'):
        validate_scope(scope,messages('只搭业务编号731，负责人Example operator'))


def test_candidate_beyond_catalog_front_is_retrieved_from_all_scope_records():
    rows=[{'id':str(i),'included':False,'business_no':str(i),'post_id':'','name':'reference'} for i in range(350)]
    state={'messages':messages('仅搭建业务编号349'),'draft':{'requirements':rows},
           'scope':{'status':'resolved','selectors':[{'field':'business_no','values':['349']}]}}
    payload=compact_state(state)
    assert payload['candidate_catalog'][0]['business_no']=='349'
    assert payload['draft']['requirements'][0]['business_no']=='349'
    assert payload['catalog_coverage']['scope_matches']==1


def test_changed_verified_field_evidence_resolves_old_question_only_for_that_field():
    req=Requirement(id='r',included=True,budget='50',language='en',sources={'budget':SourceRef(file_id='f',sheet='S',cell='A1',raw_value='50')})
    draft=BuildDraft(id='d',project_id='p',requirements=[req])
    q=Clarification(id='q',field='budget',scope='requirements',requirement_ids=['r'],question='预算冲突，请确认')
    task=IntakeTask(id='t',project_id='p',semantic_questions=[q])
    task.analysis['question_basis']={'q':question_observation(q,draft)}
    req.language='all'
    refresh(task,draft)
    assert task.semantic_questions
    req.sources['budget']=SourceRef(file_id='',sheet='',cell='',kind='confirmed',raw_value='50',message_id='m')
    refresh(task,draft)
    assert not task.semantic_questions


def test_compact_context_includes_bounded_masked_table_evidence():
    state={'messages':[],'draft':None,'documents':[{'id':'file','inspection':{'sheets':[{'sheet':'S','regions':[{'range':'A1:B10','sample':[{'A1':'Budget','B1':'USD'}], 'header_candidates':[{'row':1,'cells':{'A1':'Budget','B1':'USD'}}]}]}]}}]}
    payload=compact_state(state)
    assert payload['documents'][0]['sheets'][0]['regions'][0]['sample'][0]['B1']=='USD'

