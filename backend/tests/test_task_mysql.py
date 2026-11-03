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
        c.execute('delete from audit where tenant_id=%s',(tenant,))
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
            from app.models import IntakeTask,TaskScope
            with db.transaction() as c:
                current=IntakeTask.model_validate(task)
                current.messages=[{'id':'scope-message','role':'user','content':'本表全部新增搭建'}]
                current.scope=TaskScope(action='new',status='resolved',all_records=True,message_ids=['scope-message'])
                from app.intake import save
                save(c,current)
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


def test_archive_restore_and_project_delete(database):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'name':'archive me'})).json()
            url=f"/tasks/{task['id']}/archive"
            archived=await client.post(url,json={'expected_version':1,'archived':True})
            assert archived.status_code==200,archived.text
            assert archived.json()['archived_at']
            assert (await client.post(url,json={'expected_version':1,'archived':False})).status_code==409
            blocked=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':2,'message':'change'})
            assert blocked.status_code==409
            restored=await client.post(url,json={'expected_version':2,'archived':False})
            assert restored.status_code==200
            assert restored.json()['archived_at'] is None
            with db.transaction() as c:
                c.execute("update memberships set role='viewer' where tenant_id=(select tenant_id from projects where id=%s)",(database,))
            assert (await client.delete(f'/projects/{database}')).status_code==403
            assert (await client.post(url,json={'expected_version':3,'archived':True})).status_code==403
            with db.transaction() as c:
                c.execute("update memberships set role='admin' where tenant_id=(select tenant_id from projects where id=%s)",(database,))
            with db.transaction() as c:
                tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
            assert (await client.delete(f'/projects/{database}')).status_code==200
            assert (await client.get(f'/projects/{database}/workspace')).status_code==404
            assert (await client.get(f"/tasks/{task['id']}")).status_code==404
            with db.transaction() as c:
                for table in ('intake_tasks','drafts','files','history','runs','deployments','project_contexts','messages','mapping_templates'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0

                c.execute("delete from jobs where JSON_CONTAINS(message,%s,'$.checkpoint_threads')",(json.dumps(f"{tenant}:{task['id']}"),))
    import json
    asyncio.run(scenario())


def test_archive_blocks_busy_tasks_and_project_cleanup_is_durable(database,tmp_path,monkeypatch):
    import json
    from datetime import datetime,timezone,timedelta
    from app.models import IntakeTask,BuildDraft
    from app.intake import save
    from app.worker import cleanup
    monkeypatch.setenv('STORAGE_DIRECTORY',str(tmp_path))
    from app.settings import settings
    settings.cache_clear()
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'name':'cleanup'})).json()
            draft=BuildDraft(id=str(uuid4()),project_id=database)
            current=IntakeTask.model_validate(task)
            current.draft_id=draft.id
            current.lease={'until':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()}
            with db.transaction() as c:
                tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
                c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,1,%s)',(draft.id,tenant,database,db.json_value(draft)))
                save(c,current)
            url=f"/tasks/{task['id']}/archive"
            assert (await client.post(url,json={'expected_version':1,'archived':True})).status_code==409
            assert (await client.delete(f'/projects/{database}')).status_code==409
            current.lease=None
            with db.transaction() as c: save(c,current)
            run=str(uuid4())
            with db.transaction() as c:
                c.execute("insert into runs(id,tenant_id,project_id,draft_id,draft_version,policy_version,status,frozen) values(%s,%s,%s,%s,1,0,'queued',%s)",(run,tenant,database,draft.id,'{}'))
            assert (await client.post(url,json={'expected_version':1,'archived':True})).status_code==409
            assert (await client.delete(f'/projects/{database}')).status_code==409
            with db.transaction() as c: c.execute("update runs set status='complete' where id=%s",(run,))
            assert (await client.post(url,json={'expected_version':1,'archived':True})).status_code==200
            assert (await client.post(f'/drafts/{draft.id}/edit',json={'expected_version':1,'edits':[]})).status_code==409
            assert (await client.post(f'/projects/{database}/files?kind=requirements&task_id={task["id"]}',files={'file':('test.xlsx',b'blocked')})).status_code==409
            # A completed run with a pending job still blocks project deletion.
            with db.transaction() as c:
                c.execute('insert into jobs(message) values(%s)',(json.dumps({'run_id':run}),))
            assert (await client.delete(f'/projects/{database}')).status_code==409
            relative=f'{tenant}/{database}/test.xlsx'
            file=tmp_path/relative;file.parent.mkdir(parents=True);file.write_bytes(b'private workbook')
            sibling=tmp_path/'other-project.xlsx';sibling.write_bytes(b'keep')
            with db.transaction() as c:
                c.execute("update jobs set status='complete' where JSON_UNQUOTE(JSON_EXTRACT(message,'$.run_id'))=%s",(run,))
                c.execute("insert into files(id,tenant_id,project_id,name,kind,sha256,storage_path,inspection) values(%s,%s,%s,'test.xlsx','requirements',%s,%s,'{}')",(str(uuid4()),tenant,database,'0'*64,relative))
                c.execute("insert into messages(tenant_id,project_id,role,content) values(%s,%s,'user','private')",(tenant,database))
            assert (await client.delete(f'/projects/{database}')).status_code==200
            assert file.exists()  # Files are only removed after the deletion transaction commits.
            with db.transaction() as c:
                job=c.execute("select * from jobs where JSON_CONTAINS(message,%s,'$.checkpoint_threads')",(json.dumps(f'{tenant}:{current.id}'),)).fetchone()
                assert job and job['message']['storage_paths']==[relative]
                assert not c.execute('select id from runs where id=%s',(run,)).fetchone()
                assert not c.execute("select id from jobs where JSON_UNQUOTE(JSON_EXTRACT(message,'$.run_id'))=%s",(run,)).fetchone()
                assert c.execute("select id from audit where event='project_deleted' and tenant_id=%s",(tenant,)).fetchone()
            await cleanup(job['message'])
            await cleanup(job['message'])  # Retrying a cleanup is safe.
            assert not file.exists() and sibling.read_bytes()==b'keep'
            with db.transaction() as c: c.execute('delete from jobs where id=%s',(job['id'],))
    asyncio.run(scenario())


def test_project_rename_permissions_conflicts_and_audit(database):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            url=f'/projects/{database}'
            assert (await client.patch(url,json={'name':'   ','expected_name':'test'})).status_code==422
            renamed=await client.patch(url,json={'name':'  新项目名称  ','expected_name':'test'})
            assert renamed.status_code==200 and renamed.json()['name']=='新项目名称'
            assert (await client.patch(url,json={'name':'overwrite','expected_name':'test'})).status_code==409
            assert (await client.patch('/projects/not-owned',json={'name':'invalid','expected_name':'test'})).status_code==404
            with db.transaction() as c:
                row=c.execute('select name,tenant_id from projects where id=%s',(database,)).fetchone()
                assert row['name']=='新项目名称'
                audit=c.execute("select data from audit where project_id=%s and event='project_renamed'",(database,)).fetchone()
                assert audit['data']['old_name']=='test' and audit['data']['name']=='新项目名称'
                c.execute("update memberships set role='operator' where tenant_id=%s",(row['tenant_id'],))
            assert (await client.patch(url,json={'name':'invalid','expected_name':'新项目名称'})).status_code==403
    asyncio.run(scenario())


def test_task_rename_is_versioned_and_preserves_batch_data(database):
    from datetime import datetime,timezone,timedelta
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'name':'original batch'})).json()
            url=f"/tasks/{task['id']}"
            with db.transaction() as c:
                before=c.execute('select data from intake_tasks where id=%s',(task['id'],)).fetchone()['data']
                tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
                c.execute("update memberships set role='operator' where tenant_id=%s",(tenant,))
            for name in ('   ','x'*256):
                assert (await client.patch(url,json={'name':name,'expected_version':1})).status_code==422
            renamed=await client.patch(url,json={'name':'  第一批 · 11月活动  ','expected_version':1})
            assert renamed.status_code==200,renamed.text
            assert renamed.json()=={'id':task['id'],'name':'第一批 · 11月活动','version':2}
            assert (await client.patch(url,json={'name':'stale','expected_version':1})).status_code==409
            assert (await client.patch('/tasks/not-owned',json={'name':'no','expected_version':1})).status_code==404
            with db.transaction() as c:
                after=c.execute('select data from intake_tasks where id=%s',(task['id'],)).fetchone()['data']
                assert after=={**before,'name':'第一批 · 11月活动','version':2}
                audit=c.execute("select data from audit where project_id=%s and event='task_renamed'",(database,)).fetchone()['data']
                assert audit['task_id']==task['id'] and audit['old_name']=='original batch'
                c.execute("update memberships set role='viewer' where tenant_id=%s",(tenant,))
            assert (await client.patch(url,json={'name':'denied','expected_version':2})).status_code==403
            with db.transaction() as c:
                c.execute("update memberships set role='admin' where tenant_id=%s",(tenant,))
                after['lease']={'until':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()}
                c.execute('update intake_tasks set data=%s where id=%s',(db.json_value(after),task['id']))
            assert (await client.patch(url,json={'name':'busy','expected_version':2})).status_code==409
            with db.transaction() as c:
                after['lease']=None;after['archived_at']=datetime.now(timezone.utc).isoformat()
                c.execute('update intake_tasks set data=%s where id=%s',(db.json_value(after),task['id']))
            assert (await client.patch(url,json={'name':'archived','expected_version':2})).status_code==409
    asyncio.run(scenario())
