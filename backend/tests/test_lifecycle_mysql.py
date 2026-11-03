"""Lifecycle safety with real SQL filtering and the official AG-UI transport."""
import asyncio
from uuid import uuid4

import httpx
import pytest

from app import db,worker
from app.main import app
from app.settings import settings
from test_agent_sse import agent_body,sse_events
from test_task_mysql import database
from test_task_recovery_mysql import prepare_task


def test_archived_task_blocks_edit_confirm_and_official_agent_without_changing_draft(database,monkeypatch):
    def forbidden(*_a,**_k):pytest.fail('Archived task must not call the model')
    monkeypatch.setattr('app.agent.llm',forbidden)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task,draft,_=await prepare_task(client,database,'development-'+uuid4().hex[:8])
            archived=await client.post('/tasks/'+task['id']+'/archive',json={'expected_version':1,'archived':True})
            assert archived.status_code==200,archived.text
            assert 'requests' not in archived.json() and 'lease' not in archived.json()
            edits=await client.post('/drafts/'+draft.id+'/edit',json={'expected_version':1,'edits':[]})
            confirm=await client.post('/drafts/'+draft.id+'/confirm',json={'expected_version':1,'digest':'invalid','acknowledged':True})
            structure=await client.post('/drafts/'+draft.id+'/structure-proposal',json={'expected_version':1,'requirement_id':'wanted','structure':{}})
            for response in (edits,confirm,structure):assert response.status_code==409,response.text
            request_id=str(uuid4());payload=agent_body(request_id)
            payload['forwardedProps']={'task_id':task['id'],'request_id':request_id,'expected_version':2,'expected_draft_version':1,
                                       'action':'analyze','message':'Continue'}
            response=await client.post(f'/projects/{database}/agent',headers={'Accept':'text/event-stream'},json=payload)
            events=sse_events(response)
            assert any(e['type']=='RUN_ERROR' and '归档' in e['message'] for e in events),events
            assert not any(e['type']=='RUN_FINISHED' for e in events)
            after=(await client.get('/tasks/'+task['id'])).json()
            assert after['draft']['version']==1 and after['task']['version']==2
            assert after['task']['messages']==[] and not after['task']['proposal']
            with db.transaction() as c:
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())


def test_disabled_worker_consumes_cleanup_and_never_claims_pending_ad_job(database,monkeypatch):
    assert not settings().execution_enabled
    cleaned=[];job_ids=[]
    with db.transaction() as c:
        for payload in ({'run_id':'never-execute-'+uuid4().hex},{'storage_paths':['test/'+uuid4().hex],'checkpoint_threads':[]}):
            result=c.execute('insert into jobs(message) values(%s)',(db.json_value(payload),))
            job_ids.append(result.lastrowid)
    async def cleanup(data):cleaned.append(data)
    async def stop(_):raise asyncio.CancelledError
    async def forbidden(*_a,**_k):pytest.fail('Advertising executor called with execution disabled')
    monkeypatch.setattr(worker,'cleanup',cleanup)
    monkeypatch.setattr(worker.asyncio,'sleep',stop)
    monkeypatch.setattr('app.executor.execute',forbidden)
    try:
        with pytest.raises(asyncio.CancelledError):asyncio.run(worker.main())
        assert len(cleaned)==1
        with db.transaction() as c:
            ad=c.execute('select * from jobs where id=%s',(job_ids[0],)).fetchone()
            cleanup_job=c.execute('select * from jobs where id=%s',(job_ids[1],)).fetchone()
            assert ad['status']=='pending' and ad['lease_until'] is None
            assert cleanup_job['status']=='complete'
            for table in ('runs','deployments'):
                assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    finally:
        with db.transaction() as c:
            for job_id in job_ids:c.execute('delete from jobs where id=%s',(job_id,))
