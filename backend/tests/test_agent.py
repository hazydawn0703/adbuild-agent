from app.agent import ModelServiceError
from model_response_fixture import row_bindings
from model_response_fixture import model_response
import asyncio
from langgraph.checkpoint.memory import InMemorySaver
from app.models import ChatDecision,ChatEdit
from app.agent import conversation_graph
from app.agent import propose_mapping
from app.models import MappingProposal,SheetMapping
import pytest


def test_graph_persists_proposal_without_applying_edits(monkeypatch):
    class Model:
        async def ainvoke(self,messages):
            return model_response(ChatDecision(answer='proposal',edits=[ChatEdit(requirement_id='r',values={'budget':'40'})]))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    async def scenario():
        graph=conversation_graph(InMemorySaver())
        config={'configurable':{'thread_id':'tenant:task:request'}}
        result=await graph.ainvoke({'messages':[{'id':'message','role':'user','content':'预算改为40'}],'draft':{'requirements':[{'id':'r','budget':'50'}]},'context':{}},config)
        assert result['draft']['requirements'][0]['budget']=='50'
        snapshot=await graph.aget_state(config)
        assert snapshot.values['decision']['edits'][0]['values']['budget']=='40'
    asyncio.run(scenario())


def test_graph_rejects_unknown_scope(monkeypatch):
    class Model:
        async def ainvoke(self,messages):
            return model_response(ChatDecision(answer='bad',edits=[ChatEdit(requirement_id='other',values={'budget':'40'})]))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    with pytest.raises(ModelServiceError):
        asyncio.run(conversation_graph(InMemorySaver()).ainvoke({'messages':[{'id':'m','role':'user','content':'业务编号101'}],'draft':None,'context':{},'scope':{'status':'resolved','action':'edit','selectors':[{'field':'business_no','values':['101']}],'message_ids':['m']}},{'configurable':{'thread_id':'t'}}))


def test_mapping_uses_content_not_sheet_name(monkeypatch):
    class Model:
        async def ainvoke(self,messages):
            return model_response(MappingProposal(mappings=[SheetMapping(sheet='Sheet42',header_row=1,first_row=2,last_row=40,bindings=row_bindings({'post_url':'J'}))],explanation='content'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    result=asyncio.run(propose_mapping({'sheets':[{'sheet':'Sheet42','rows':40}]}))
    assert result.mappings[0].last_row==40


def test_scope_does_not_confuse_unchanged_with_excluded():
    from app.agent import validate_decision,ProposalError
    state={'messages':[{'role':'user','content':'仅将业务编号101的预算改为100'}],
           'draft':{'requirements':[{'id':'r1','business_no':'101'},{'id':'r2','business_no':'102'}]}}
    decision=ChatDecision(answer='proposal',edits=[ChatEdit(requirement_id='r1',values={'budget':'100'}),ChatEdit(requirement_id='r2',values={'included':False})])
    with pytest.raises(ProposalError):validate_decision(state,decision)


def test_empty_structured_output_gets_one_bounded_repair(monkeypatch):
    from app.agent import structured
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(1)
            return model_response(None if len(calls)==1 else ChatDecision(answer='已识别'))
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    assert asyncio.run(structured(ChatDecision,[])).answer=='已识别'
    assert len(calls)==2

