"""Real MySQL integration. TEST_DATABASE_URL must point to a disposable *_test DB."""
import asyncio
import os
from uuid import uuid4
from io import BytesIO
import pytest
import httpx
from openpyxl import Workbook
from sqlalchemy import create_engine
from app import db
from app.auth import Actor,authenticate
from app.main import app


@pytest.fixture
def database(monkeypatch):
    url=os.getenv('TEST_DATABASE_URL','')
    if not url: pytest.skip('TEST_DATABASE_URL required for isolated MySQL integration')
    from sqlalchemy.engine import make_url
    assert make_url(url).database.endswith('_test'),'Refuse non-test database'
    monkeypatch.setattr(db,'_engine',None)
    monkeypatch.setenv('DATABASE_URL',url)
    from app.settings import settings
    settings.cache_clear()
    user,tenant,project=[str(uuid4()) for _ in range(3)]
    with db.transaction() as c:
        c.execute('insert into users(id,email,password_hash) values(%s,%s,%s)',(user,user+'@test.invalid','unused'))
        c.execute('insert into tenants(id,name) values(%s,%s)',(tenant,'integration'))
        c.execute('insert into memberships values(%s,%s,%s)',(tenant,user,'admin'))
        c.execute('insert into projects(id,tenant_id,name,account_id) values(%s,%s,%s,%s)',(project,tenant,'test','account'))
    app.dependency_overrides[authenticate]=lambda:Actor(user)
    yield project
    app.dependency_overrides.clear()
    with db.transaction() as c:
        for table in ('intake_tasks','files','history','drafts','audit','project_contexts'):
            c.execute('delete from '+table+' where project_id=%s',(project,))
        c.execute('delete from mapping_templates where project_id=%s',(project,))
        c.execute('delete from projects where id=%s',(project,))
        c.execute('delete from memberships where tenant_id=%s',(tenant,))
        c.execute('delete from tenants where id=%s',(tenant,))
        c.execute('delete from users where id=%s',(user,))
    settings.cache_clear()


def test_task_upload_mapping_answers_replay_and_tenant_scope(database,tmp_path,monkeypatch):
    monkeypatch.setenv('STORAGE_DIRECTORY',str(tmp_path))
    from app.settings import settings
    settings.cache_clear()
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'name':'batch'})).json()
            taskid=task['id']
            w=Workbook();s=w.active;s.append(['Post','Goal','Budget']);s.append(['https://www.tiktok.com/@a/video/123','followers',50]);buf=BytesIO();w.save(buf)
            upload=await client.post(f'/projects/{database}/files?kind=requirements&task_id={taskid}',files={'file':('batch.xlsx',buf.getvalue())})
            assert upload.status_code==200,upload.text
            fileid=upload.json()['id']
            mapping={'sheet':'Sheet','header_row':1,'first_row':2,'last_row':2,'columns':{'post_url':'A','objective':'B','budget':'C'}}
            result=await client.post(f'/files/{fileid}/mapping/confirm',json={'mappings':[mapping],'expected_task_version':2,'request_id':str(uuid4())})
            assert result.status_code==200,result.text
            state=(await client.get('/tasks/'+taskid)).json()
            q=next(q for q in state['task']['questions'] if q['field']=='currency')
            body={'task_id':taskid,'request_id':str(uuid4()),'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],'action':'answer','answers':{q['id']:'USD'}}
            first=await client.post(f'/projects/{database}/chat',json=body)
            assert first.status_code==200,first.text
            assert (await client.post(f'/projects/{database}/chat',json=body)).json()==first.json()
            body['answers'][q['id']]='EUR'
            assert (await client.post(f'/projects/{database}/chat',json=body)).status_code==409
            assert (await client.post('/projects/not-owned/chat',json=body)).status_code==404
            with db.transaction() as c:
                for table in ('jobs','runs','deployments'):
                    assert c.execute('select count(*) n from '+table).fetchone()['n']==0
                assert c.execute('select reserved from projects where id=%s',(database,)).fetchone()['reserved']==0
    asyncio.run(scenario())


def test_checkpoint_proposal_requires_exact_accept_and_survives_reload(database,monkeypatch):
    from app.models import ChatDecision,ChatEdit,BuildDraft,Requirement
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(1)
            return ChatDecision(answer='预算修改提案',edits=[ChatEdit(requirement_id='r',values={'budget':'40'})])
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            draft=BuildDraft(id=str(uuid4()),project_id=database,requirements=[Requirement(id='r',business_no='101',budget='50')])
            with db.transaction() as c:
                tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
                c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,%s,%s)',(draft.id,tenant,database,1,db.json_value(draft)))
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            request={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':task['version'],'expected_draft_version':1,'action':'message','message':'仅将业务编号101的预算改为40'}
            result=await client.post(f'/projects/{database}/chat',json=request)
            assert result.status_code==200,result.text
            reloaded=(await client.get('/tasks/'+task['id'])).json()
            assert reloaded['draft']['requirements'][0]['budget']=='50'
            pending=reloaded['task']['proposal']
            request.update(request_id=str(uuid4()),expected_version=reloaded['task']['version'],action='accept',message='',proposal_id='wrong')
            assert (await client.post(f'/projects/{database}/chat',json=request)).status_code==409
            request['proposal_id']=pending['id']
            accepted=await client.post(f'/projects/{database}/chat',json=request)
            assert accepted.status_code==200,accepted.text
            assert accepted.json()['draft']['requirements'][0]['budget']=='40'
            assert accepted.json()['draft']['approval'] is None
            assert len(calls)==1
    asyncio.run(scenario())


def test_predraft_question_answer_and_request_replay_use_task_scope(database,monkeypatch):
    from app.models import ChatDecision, Clarification
    class Model:
        async def ainvoke(self,messages):
            return ChatDecision(answer='请先明确本次范围',questions=[Clarification(id='model-id',field='scope',scope='task',question='本次选择哪些业务编号？')])
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'name':'predraft'})).json()
            request={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':task['version'],'action':'message','message':'先讨论本次需求'}
            first=await client.post(f'/projects/{database}/chat',json=request)
            assert first.status_code==200,first.text
            assert (await client.post(f'/projects/{database}/chat',json=request)).json()==first.json()
            reloaded=(await client.get('/tasks/'+task['id'])).json()
            assert reloaded['draft'] is None
            question=reloaded['task']['questions'][0]
            request={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':reloaded['task']['version'],'action':'answer','answers':{question['id']:'只搭建业务编号 731、732'}}
            answered=await client.post(f'/projects/{database}/chat',json=request)
            assert answered.status_code==200,answered.text
            assert (await client.post(f'/projects/{database}/chat',json=request)).json()==answered.json()
            again=(await client.get('/tasks/'+task['id'])).json()
            assert again['task']['answers'][question['id']]['value']=='只搭建业务编号 731、732'
            assert not again['task']['questions']
            assert (await client.get(f'/projects/{database}/context')).json()['defaults']=={}
            with db.transaction() as c:
                for table in ('jobs','runs','deployments'):
                    assert c.execute('select count(*) n from '+table).fetchone()['n']==0
    asyncio.run(scenario())
