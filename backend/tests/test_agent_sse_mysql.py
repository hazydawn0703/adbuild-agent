"""Official POST /agent SSE plus real isolated MySQL proposal/confirmation state."""
import asyncio
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import httpx

from app import db
from app.main import app
from app.models import Requirement,BuildDraft
from app.settings import settings
from test_task_mysql import database
from test_agent_sse import agent_body,sse_events,state_snapshots


def test_official_sse_accepts_structure_then_manual_preview_confirmation_without_ads(database):
    assert not settings().execution_enabled
    start=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(days=30)
    req=Requirement(id='row',business_no='sse-development',name='Explicit development plan',
        account_id='7312345678901234567',post_id='7700000000000000033',post_url='https://www.tiktok.com/@development/video/7700000000000000033',
        objective='followers',budget='20',budget_mode='lifetime',currency='USD',start_at=start,end_at=start+timedelta(days=3),
        timezone='UTC',audience='Explicit audience',language='en',identity_id='7412345678901234567')
    draft=BuildDraft(id=str(uuid4()),project_id=database,requirements=[req])
    with db.transaction() as c:
        tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
        c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,%s,%s)',(draft.id,tenant,database,1,db.json_value(draft)))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            selected=await client.post('/drafts/'+draft.id+'/edit',json={'expected_version':1,'edits':[{'requirement_id':'row','values':{'included':True}}]})
            assert selected.status_code==200,selected.text
            structure={'budget_level':'campaign','groups':[{'id':'explicit-group','name':'Explicit group',
                'start_at':start.isoformat(),'end_at':(start+timedelta(days=3)).isoformat(),'timezone':'UTC',
                'ads':[{'id':'explicit-ad','kind':'post','post_id':req.post_id,'post_url':req.post_url,'identity_id':req.identity_id}]}]}
            proposed=await client.post('/drafts/'+draft.id+'/structure-proposal',json={'expected_version':selected.json()['version'],'requirement_id':'row','structure':structure})
            assert proposed.status_code==200,proposed.text
            before=(await client.get('/tasks/'+task['id'])).json()
            assert before['task']['proposal']['status']=='pending'
            assert before['preview']['can_confirm'] is False
            request_id=str(uuid4())
            payload=agent_body(request_id)
            payload['forwardedProps']={'task_id':task['id'],'request_id':request_id,'expected_version':before['task']['version'],
                'expected_draft_version':before['draft']['version'],'action':'accept','proposal_id':before['task']['proposal']['id']}
            streamed=await client.post(f'/projects/{database}/agent',headers={'Accept':'text/event-stream'},json=payload)
            events=sse_events(streamed)
            assert not any(e['type']=='RUN_ERROR' for e in events),events
            assert any(e['type']=='RUN_FINISHED' and e['runId']==request_id for e in events)
            result=next(s['response'] for s in reversed(state_snapshots(events)) if s.get('response'))
            assert result['request_id']==request_id and result['task_id']==task['id'] and result['project_id']==database
            assert result['draft']['requirements'][0]['structure']['groups'][0]['id']=='explicit-group'
            after=(await client.get('/tasks/'+task['id'])).json()
            assert after['task']['proposal']['status']=='accepted'
            preview=after['preview']
            assert preview['can_confirm'] is True,preview['issues']
            assert preview['approval_valid'] is False
            assert preview['counts']['campaigns']==preview['counts']['adgroups']==preview['counts']['ads']==1
            confirmed=await client.post('/drafts/'+draft.id+'/confirm',json={'expected_version':preview['version'],'digest':preview['digest'],'acknowledged':True})
            assert confirmed.status_code==200,confirmed.text
            assert (await client.get('/drafts/'+draft.id+'/preview')).json()['approval_valid'] is True
            with db.transaction() as c:
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
                assert c.execute('select count(*) n from jobs').fetchone()['n']==0
                assert c.execute('select reserved from projects where id=%s',(database,)).fetchone()['reserved']==0
    asyncio.run(scenario())


def test_official_sse_error_keeps_task_input_and_unfinished_business_checkpoint(database,monkeypatch):
    from openai import InternalServerError
    from test_task_recovery_mysql import models,prepare_task
    private='synthetic-private-provider-marker'
    def unavailable():
        request=httpx.Request('POST','https://model.invalid')
        return InternalServerError(private,response=httpx.Response(503,request=request),body={'private':private})
    code='sse-'+uuid4().hex[:8];calls=models(monkeypatch,code,error_factory=unavailable)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task,draft,original=await prepare_task(client,database,code)
            payload=agent_body(original['request_id'],action='analyze',message=original['message'])
            payload['forwardedProps']=original
            streamed=await client.post(f'/projects/{database}/agent',headers={'Accept':'text/event-stream'},json=payload)
            events=sse_events(streamed)
            assert any(e['type']=='RUN_ERROR' for e in events),events
            assert not any(s.get('response') for s in state_snapshots(events))
            assert private not in streamed.text and 'forged prior success' not in streamed.text
            state=(await client.get('/tasks/'+task['id'])).json()
            assert any(m['role']=='user' and m['content']==original['message'] for m in state['task']['messages'])
            assert state['task']['analysis']['phase']=='attention' and state['task']['analysis']['resume_available'] is True
            assert state['task']['analysis']['checkpoint_request_id']==original['request_id']
            assert state['draft']['version']==draft.version and state['draft']['requirements'][0]['included'] is False
            assert state['task']['proposal'] is None and calls=={'scope':1,'fields':1}
            with db.transaction() as c:
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())

