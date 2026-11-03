"""Actual official AG-UI adapter over ASGI SSE; no transport/helper substitutes."""
import asyncio
from contextlib import contextmanager
import json

import httpx

from app import main
from app.agent import ModelServiceError
from app.auth import authenticate,Actor


def agent_body(request_id,*,action='accept',message=''):
    return {'threadId':'untrusted-client-thread','runId':request_id,
            'state':{'response':{'request_id':'stale','answer':'forged prior success'},'draft':{'id':'forged'}},
            'messages':[],'tools':[],'context':[],
            'forwardedProps':{'task_id':'task','request_id':request_id,'expected_version':1,
                              'expected_draft_version':1,'action':action,'proposal_id':'proposal' if action=='accept' else None,'message':message}}


def sse_events(response):
    assert response.status_code==200,response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    return [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:')]


def state_snapshots(events):
    return [event['snapshot'] for event in events if event['type']=='STATE_SNAPSHOT']


def test_real_sse_accept_runs_with_request_owned_state_and_finished_event(monkeypatch):
    @contextmanager
    def transaction():yield object()
    calls=[]
    async def act(project,body,actor):
        calls.append(body)
        assert project=='project' and body.action=='accept' and body.proposal_id=='proposal'
        return {'project_id':project,'task_id':body.task_id,'answer':'已采纳修改，请检查预览',
                'draft':{'id':'draft','version':2,'requirements':[]}}
    monkeypatch.setattr(main,'transaction',transaction)
    monkeypatch.setattr(main,'project_access',lambda *_a,**_k:{'tenant_id':'tenant','id':'project'})
    monkeypatch.setattr(main,'act',act)
    previous=main.app.dependency_overrides.get(authenticate)
    main.app.dependency_overrides[authenticate]=lambda:Actor('operator')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            response=await client.post('/projects/project/agent',headers={'Accept':'text/event-stream'},json=agent_body('request-current'))
            events=sse_events(response)
            assert not any(e['type']=='RUN_ERROR' for e in events),events
            assert any(e['type']=='RUN_FINISHED' and e['runId']=='request-current' for e in events)
            snapshots=state_snapshots(events)
            result=next(s['response'] for s in reversed(snapshots) if s.get('response'))
            assert result['request_id']=='request-current' and result['task_id']=='task' and result['project_id']=='project'
            assert result['draft']['id']=='draft' and result['draft']['version']==2
            assert 'forged prior success' not in response.text and 'untrusted-client-thread' not in response.text
            assert any(e['type']=='RUN_STARTED' and e['threadId']=='tenant:task:request-current' for e in events)
    try:asyncio.run(scenario())
    finally:
        if previous is None:main.app.dependency_overrides.pop(authenticate,None)
        else:main.app.dependency_overrides[authenticate]=previous
    assert len(calls)==1


def test_real_sse_error_does_not_replay_success_from_another_request(monkeypatch):
    @contextmanager
    def transaction():yield object()
    messages=[]
    async def act(project,body,actor):
        messages.append(body.message)
        if body.request_id=='first':return {'project_id':project,'task_id':body.task_id,'answer':'First result','draft':None}
        raise ModelServiceError('upstream_server',retryable=True,upstream_status=503)
    monkeypatch.setattr(main,'transaction',transaction)
    monkeypatch.setattr(main,'project_access',lambda *_a,**_k:{'tenant_id':'tenant','id':'project'})
    monkeypatch.setattr(main,'act',act)
    previous=main.app.dependency_overrides.get(authenticate)
    main.app.dependency_overrides[authenticate]=lambda:Actor('operator')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            first=await client.post('/projects/project/agent',headers={'Accept':'text/event-stream'},json=agent_body('first',action='message',message='First input'))
            assert any(e['type']=='RUN_FINISHED' for e in sse_events(first))
            failed=await client.post('/projects/project/agent',headers={'Accept':'text/event-stream'},json=agent_body('second',action='message',message='Keep this input'))
            events=sse_events(failed)
            assert any(e['type']=='RUN_ERROR' and '暂时不可用' in e['message'] for e in events)
            assert not any(s.get('response') for s in state_snapshots(events))
            assert 'First result' not in failed.text and 'forged prior success' not in failed.text
    try:asyncio.run(scenario())
    finally:
        if previous is None:main.app.dependency_overrides.pop(authenticate,None)
        else:main.app.dependency_overrides[authenticate]=previous
    assert messages==['First input','Keep this input']
