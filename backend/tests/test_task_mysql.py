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
            result=await client.post(f'/files/{fileid}/mapping/confirm',json={'mappings':[mapping]})
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
