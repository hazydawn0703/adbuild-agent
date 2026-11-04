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
    feedback=calls[1][-1].content
    assert result.explanation=='Need scope' and len(calls)==2
    assert 'explanation' in feedback and calls[1][-1].tool_call_id=='bad' and calls[1][-1].status=='error'
    assert calls[1][-2].tool_calls[0]['args']=={'mappings':[]}
    assert 'response_metadata' not in feedback and 'additional_kwargs' not in feedback


def test_invalid_json_repair_reports_actual_line_and_column(monkeypatch):
    calls=[]
    raw=AIMessage(content='',invalid_tool_calls=[{'name':'MappingProposal','args':'{"mappings": [], "explanation": "ok"}}','id':'bad','error':'invalid JSON'}])
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return {'raw':raw,'parsed':None,'parsing_error':None} if len(calls)==1 else {'raw':AIMessage(content=''),'parsed':{'mappings':[],'explanation':'ok'},'parsing_error':None}
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    assert asyncio.run(structured(MappingProposal,[])).explanation=='ok'
    feedback=calls[1][-1].content
    assert 'Extra data' in feedback and '"line": 1' in feedback and '"column":' in feedback and len(calls)==2


def test_custom_validator_error_context_does_not_break_repair_serialization(monkeypatch):
    from pydantic import BaseModel,field_validator
    class Checked(BaseModel):
        value:int
        @field_validator('value')
        @classmethod
        def positive(cls,value):
            if value<0:raise ValueError('Positive value required')
            return value
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return {'raw':AIMessage(content=''),'parsed':{'value':-1 if len(calls)==1 else 1},'parsing_error':None}
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    assert asyncio.run(structured(Checked,[])).value==1 and len(calls)==2


def test_credentials_are_redacted_inside_invalid_tool_json():
    from app.scope import redact_private
    value={'args':'{"authorization_code":"private-material-code"}}','error':'Error repeats private-material-code'}
    sanitized=redact_private(value)
    assert 'private-material-code' not in str(sanitized) and 'authorization_code' in sanitized['args']
