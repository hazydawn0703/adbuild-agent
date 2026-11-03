from model_response_fixture import model_response
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import InternalServerError,RateLimitError,AuthenticationError,BadRequestError,APIConnectionError,APITimeoutError,APIResponseValidationError,APIStatusError

from app.agent import structured,ModelServiceError
from app.models import ChatDecision,MappingProposal,RangeRequest

PRIVATE='private-provider-body-and-credential-marker'


def provider_error(kind,status):
    request=httpx.Request('POST','https://model.invalid/chat/completions',headers={'Authorization':'Bearer '+PRIVATE})
    if kind is APIConnectionError:return kind(message=PRIVATE,request=request)
    if kind is APITimeoutError:return kind(request=request)
    response=httpx.Response(status,request=request,headers={'x-request-id':PRIVATE})
    if kind is APIResponseValidationError:return kind(response,{'private':PRIVATE})
    return kind(PRIVATE,response=response,body={'private':PRIVATE})


@pytest.mark.parametrize('kind,status,retryable,http_status,category',[
    (InternalServerError,503,True,503,'upstream_server'),
    (RateLimitError,429,True,503,'rate_limit'),
    (APIConnectionError,None,True,503,'connection'),
    (APITimeoutError,None,True,503,'timeout'),
    (AuthenticationError,401,False,503,'configuration'),
    (BadRequestError,400,False,502,'request_rejected'),
    (APIStatusError,402,False,503,'insufficient_balance'),
    (APIResponseValidationError,200,False,502,'invalid_response'),
])
def test_sdk_availability_errors_do_not_spend_schema_repair_or_leak_provider_data(monkeypatch,kind,status,retryable,http_status,category,caplog):
    calls=[]
    class Model:
        async def ainvoke(self,_):calls.append(1);raise provider_error(kind,status)
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    with pytest.raises(ModelServiceError) as caught:
        asyncio.run(structured(ChatDecision,[]))
    error=caught.value
    assert len(calls)==1
    assert error.retryable is retryable and error.status_code==http_status and error.category==category
    assert PRIVATE not in str(error) and PRIVATE not in json.dumps(error.public_error()) and PRIVATE not in caplog.text
    assert not hasattr(error,'request') and not hasattr(error,'body')
    assert error.__suppress_context__ is True


def test_chat_service_503_has_safe_retry_metadata_and_retains_error_semantics(monkeypatch):
    from app import main
    from app.auth import authenticate,Actor
    class Model:
        async def ainvoke(self,_):raise provider_error(InternalServerError,503)
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    async def act(*_):return await structured(ChatDecision,[])
    monkeypatch.setattr(main,'act',act)
    previous=main.app.dependency_overrides.get(authenticate)
    main.app.dependency_overrides[authenticate]=lambda:Actor('test-user')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            response=await client.post('/projects/project/chat',json={'task_id':'task','request_id':'request','expected_version':1,'action':'analyze'})
            assert response.status_code==503
            body=response.json()
            assert body['retryable'] is True and body['upstream_status']==503 and body['code']=='model_service_unavailable'
            assert isinstance(body['detail'],str) and '已保留' in body['detail']
            assert PRIVATE not in response.text and 'model.invalid' not in response.text
    try:asyncio.run(scenario())
    finally:
        if previous is None:main.app.dependency_overrides.pop(authenticate,None)
        else:main.app.dependency_overrides[authenticate]=previous


def test_sdk_automatic_retries_remain_disabled_and_configured_model_is_retained(monkeypatch):
    from app import agent
    calls=[]
    class Client:
        def __init__(self,**kwargs):calls.append(kwargs)
        def with_structured_output(self,schema,**kwargs):return self
    monkeypatch.setattr(agent,'settings',lambda:SimpleNamespace(llm_api_key=PRIVATE,llm_model='configured-model',llm_base_url='https://model.invalid'))
    monkeypatch.setattr(agent,'ChatOpenAI',Client)
    agent.llm(ChatDecision)
    assert len(calls)==1 and calls[0]['model']=='configured-model' and calls[0]['max_retries']==0


def test_mapping_evidence_survives_later_sdk_service_unavailability(monkeypatch):
    from app.agent import propose_mapping
    from app.excel import inspect_workbook
    from openpyxl import Workbook
    from io import BytesIO
    wb=Workbook();wb.active.append(['Header']);wb.active.append(['value'])
    stream=BytesIO();wb.save(stream);data=stream.getvalue()
    saved=[];calls=[]
    class Model:
        async def ainvoke(self,_):
            calls.append(1)
            if len(calls)>1:raise provider_error(InternalServerError,503)
            return model_response(MappingProposal(mappings=[],requested_ranges=[RangeRequest(sheet=wb.active.title,range='A1:A2',reason='inspect')],explanation='read'))
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    with pytest.raises(ModelServiceError):
        asyncio.run(propose_mapping(inspect_workbook(data),data,on_progress=lambda p:saved.append(p.model_dump(mode='json'))))
    assert len(calls)==2
    assert saved[-1]['read_state']['evidence'][0]['coverage']['read_cells']==2
    assert PRIVATE not in json.dumps(saved)


def test_provider_503_resumes_same_graph_checkpoint_without_repeating_scope(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent import conversation_graph
    from app.models import TaskScope,ScopeSelector
    calls={'scope':0,'fields':0}
    class Scope:
        async def ainvoke(self,_):
            calls['scope']+=1
            return model_response(TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=['batch-development'])],message_ids=['source']))
    class Fields:
        async def ainvoke(self,_):
            calls['fields']+=1
            if calls['fields']==1:raise provider_error(InternalServerError,503)
            return model_response(ChatDecision(answer='恢复未完成分析'))
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    async def scenario():
        graph=conversation_graph(InMemorySaver())
        config={'configurable':{'thread_id':'provider-recovery'}}
        state={'messages':[{'id':'source','role':'user','content':'本次只搭建业务编号 batch-development'}],
               'draft':{'requirements':[{'id':'row','business_no':'batch-development','included':False}]},'scope':TaskScope().model_dump(mode='json'),'context':{}}
        with pytest.raises(ModelServiceError):await graph.ainvoke(state,config)
        assert calls=={'scope':1,'fields':1}
        checkpoint=await graph.aget_state(config)
        assert checkpoint.next==('understand',)
        assert PRIVATE not in str(checkpoint)
        result=await graph.ainvoke(None,config)
        assert result['scope']['status']=='resolved' and result['decision']['edits']==[]
    asyncio.run(scenario())
    assert calls=={'scope':1,'fields':2}

