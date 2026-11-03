from model_response_fixture import model_response
"""Request-level recovery against real isolated MySQL and persistent checkpoints."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app import db
from app.main import app
from app.models import BuildDraft,Requirement,TaskScope,ScopeSelector,ChatDecision
from test_task_mysql import database


async def prepare_task(client,project,code):
    draft=BuildDraft(id=str(uuid4()),project_id=project,requirements=[Requirement(id='wanted',business_no=code,included=False)])
    with db.transaction() as c:
        tenant=c.execute('select tenant_id from projects where id=%s',(project,)).fetchone()['tenant_id']
        c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,%s,%s)',(draft.id,tenant,project,1,db.json_value(draft)))
    task=(await client.post(f'/projects/{project}/tasks',json={'draft_id':draft.id})).json()
    request={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':task['version'],'expected_draft_version':1,
             'action':'analyze','message':f'本次只搭建业务编号 {code}，其他作为参考'}
    return task,draft,request


def models(monkeypatch,code,failures=1,error_factory=None):
    calls={'scope':0,'fields':0}
    class Scope:
        async def ainvoke(self,messages):
            calls['scope']+=1
            payload=json.loads(messages[-1][1])
            source=next(m for m in reversed(payload['messages']) if m['role']=='user')
            return model_response(TaskScope(action='new',status='resolved',selectors=[ScopeSelector(field='business_no',values=[code])],message_ids=[source['id']]))
    class Fields:
        async def ainvoke(self,_):
            calls['fields']+=1
            if calls['fields']<=failures:raise error_factory() if error_factory else TimeoutError('injected field timeout')
            return model_response(ChatDecision(answer='从保存的执行位置恢复'))
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    return calls


@pytest.mark.parametrize('failures',[1,2])
def test_new_request_resumes_original_checkpoint_once_after_reload(database,monkeypatch,failures):
    code='recover-'+uuid4().hex[:8];calls=models(monkeypatch,code,failures)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as client:
            task,draft,original=await prepare_task(client,database,code)
            response=await client.post(f'/projects/{database}/chat',json=original)
            assert response.status_code==504,response.text
            for attempt in range(failures):
                state=(await client.get('/tasks/'+task['id'])).json()
                analysis=state['task']['analysis']
                assert state['task']['stage']=='attention'
                assert analysis['checkpoint_request_id']==original['request_id']
                assert analysis['base_task_version']==task['version']
                assert analysis['base_draft_version']==draft.version
                request={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':state['task']['version'],
                         'expected_draft_version':state['draft']['version'],'action':'resume'}
                response=await client.post(f'/projects/{database}/chat',json=request)
                assert response.status_code==(200 if attempt==failures-1 else 504),response.text
            assert (await client.post(f'/projects/{database}/chat',json=request)).json()==response.json()
            final=(await client.get('/tasks/'+task['id'])).json()
            assert final['task']['analysis']['checkpoint_request_id']==original['request_id']
            assert final['task']['analysis']['resume_available'] is False
            assert [r['id'] for r in final['draft']['requirements'] if r['included']]==['wanted']
            assert not final['task']['proposal']
            assert calls=={'scope':1,'fields':failures+1}
            with db.transaction() as c:
                assert c.execute('select count(*) n from drafts where project_id=%s',(database,)).fetchone()['n']==1
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())


def test_edit_after_checkpoint_failure_requires_new_analysis(database,monkeypatch):
    code='recover-'+uuid4().hex[:8];calls=models(monkeypatch,code)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as client:
            task,draft,request=await prepare_task(client,database,code)
            assert (await client.post(f'/projects/{database}/chat',json=request)).status_code==504
            edited=await client.post('/drafts/'+draft.id+'/edit',json={'expected_version':1,'edits':[{'requirement_id':'wanted','values':{'budget':'50'}}]})
            assert edited.status_code==200,edited.text
            state=(await client.get('/tasks/'+task['id'])).json()
            resume={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':state['task']['version'],
                    'expected_draft_version':state['draft']['version'],'action':'resume'}
            rejected=await client.post(f'/projects/{database}/chat',json=resume)
            assert rejected.status_code==409 and '重新分析' in rejected.text
            assert calls=={'scope':1,'fields':1}
    asyncio.run(scenario())


def test_failed_task_without_versioned_checkpoint_does_not_use_legacy_resume(database):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={})).json()
            with db.transaction() as c:
                row=c.execute('select data from intake_tasks where id=%s',(task['id'],)).fetchone()['data']
                row['stage']='attention';row['analysis']={'phase':'attention','resume_available':True}
                c.execute('update intake_tasks set data=%s where id=%s',(db.json_value(row),task['id']))
            response=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':task['version'],'action':'resume'})
            assert response.status_code==409 and '重新分析' in response.text
    asyncio.run(scenario())


def test_sdk_service_503_retains_same_checkpoint_and_can_resume_after_provider_recovers(database,monkeypatch):
    from openai import InternalServerError
    private='private-upstream-body-and-key'
    def unavailable():
        request=httpx.Request('POST','https://model.invalid',headers={'Authorization':'Bearer '+private})
        return InternalServerError(private,response=httpx.Response(503,request=request),body={'private':private})
    code='recover-'+uuid4().hex[:8];calls=models(monkeypatch,code,error_factory=unavailable)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as client:
            task,draft,original=await prepare_task(client,database,code)
            failed=await client.post(f'/projects/{database}/chat',json=original)
            assert failed.status_code==503 and failed.json()['retryable'] is True,failed.text
            assert private not in failed.text
            state=(await client.get('/tasks/'+task['id'])).json()
            assert state['task']['analysis']['error']['upstream_status']==503
            assert state['task']['analysis']['checkpoint_request_id']==original['request_id']
            assert state['draft']['requirements'][0]['included'] is False
            with db.transaction() as c:
                stored=c.execute('select data from intake_tasks where id=%s',(task['id'],)).fetchone()['data']
                assert private not in json.dumps(stored)
                assert stored['requests'][original['request_id']]['error']['retryable'] is True
            request={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':state['task']['version'],
                     'expected_draft_version':state['draft']['version'],'action':'resume'}
            response=await client.post(f'/projects/{database}/chat',json=request)
            assert response.status_code==200,response.text
            assert (await client.post(f'/projects/{database}/chat',json=request)).json()==response.json()
            final=(await client.get('/tasks/'+task['id'])).json()
            assert final['task']['analysis']['checkpoint_request_id']==original['request_id']
            assert 'error' not in final['task']['analysis']
            assert calls=={'scope':1,'fields':2}
            assert not final['task']['proposal'] and final['task']['analysis']['resume_available'] is False
            with db.transaction() as c:
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())


@pytest.mark.parametrize('changed',['flow_version','prompt_hash','missing_version'])
def test_resume_rejects_unknown_or_changed_flow_and_prompt_versions(database,monkeypatch,changed):
    from app.agent import FLOW_VERSION
    from app.prompts import PROMPT_HASH
    monkeypatch.setattr('app.agent.llm',lambda _:pytest.fail('Obsolete checkpoint must be rejected before model use'))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={})).json()
            analysis={'phase':'attention','resume_available':True,'checkpoint_request_id':str(uuid4()),
                      'base_task_version':task['version'],'base_draft_version':None,'flow_version':FLOW_VERSION,'prompt_hash':PROMPT_HASH}
            if changed=='missing_version':analysis.pop('flow_version')
            else:analysis[changed]='obsolete-analysis-version'
            with db.transaction() as c:
                row=c.execute('select data from intake_tasks where id=%s',(task['id'],)).fetchone()['data']
                row['stage']='attention';row['analysis']=analysis
                c.execute('update intake_tasks set data=%s where id=%s',(db.json_value(row),task['id']))
            response=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':task['version'],'action':'resume'})
            assert response.status_code==409 and '重新分析' in response.text
    asyncio.run(scenario())

