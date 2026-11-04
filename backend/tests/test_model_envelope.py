import asyncio
import pytest
from langchain_core.messages import AIMessage
from app.agent import structured, ModelServiceError
from app.models import MappingProposal


def test_missing_tool_result_is_repaired_once_then_reported_as_model_failure(monkeypatch):
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return {'raw':AIMessage(content='invalid output'), 'parsed':None, 'parsing_error':None}
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    with pytest.raises(ModelServiceError) as exc:
        asyncio.run(structured(MappingProposal,[('user','map')]))
    assert exc.value.status_code==502
    assert len(calls)==2
    assert 'invalid output' in calls[1][-1][1]


def test_valid_tool_result_returns_validated_model(monkeypatch):
    proposal=MappingProposal(mappings=[],explanation='Need scope')
    class Model:
        async def ainvoke(self,messages):
            return {'raw':AIMessage(content=''), 'parsed':proposal, 'parsing_error':None}
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    assert asyncio.run(structured(MappingProposal,[]))==proposal


def test_repair_uses_actual_arguments_and_error_paths_without_message_envelope(monkeypatch):
    calls=[]
    raw=AIMessage(content='',tool_calls=[{'name':'MappingProposal','args':{'mappings':[]},'id':'bad'}],response_metadata={'finish_reason':'tool_calls'})
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return {'raw':raw,'parsed':{'mappings':[]},'parsing_error':None} if len(calls)==1 else {'raw':AIMessage(content=''),'parsed':{'mappings':[],'explanation':'Need scope'},'parsing_error':None}
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    result=asyncio.run(structured(MappingProposal,[]))
    feedback=calls[1][-1][1]
    assert result.explanation=='Need scope' and len(calls)==2
    assert 'explanation' in feedback and '"arguments": {"mappings": []}' in feedback
    assert 'response_metadata' not in feedback and 'additional_kwargs' not in feedback
