"""The real answer → analyze → adopt path must complete owned fields without HTTP errors."""
import asyncio,json
from io import BytesIO
from uuid import uuid4
import httpx
from openpyxl import Workbook
from app import db
from app.main import app
from app.models import BuildDraft,Requirement,BudgetSpec,IntakeTask,TaskScope,DraftDecision,ChatEdit
from model_response_fixture import model_response
from test_budget_action import allocation_state
from test_task_mysql import database


def insert(project,missing):
    req=Requirement.model_validate(allocation_state()['draft']['requirements'][0]);req.name='Owned plan';req.account_id='7312345678901234567'
    for g in req.structure.groups:
        g.budget=BudgetSpec(amount='100',currency='USD',mode='lifetime')
        for field in missing:setattr(g,field,None);g.sources.pop(field,None)
    draft=BuildDraft(id=str(uuid4()),project_id=project,requirements=[req])
    with db.transaction() as c:
        tenant=c.execute('select tenant_id from projects where id=%s',(project,)).fetchone()['tenant_id']
        c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,1,%s)',(draft.id,tenant,project,db.json_value(draft)))
    return draft


async def task_with_message(client,project,draft,text):
    response=await client.post(f'/projects/{project}/tasks',json={'draft_id':draft.id});assert response.status_code==200,response.text
    task=IntakeTask.model_validate(response.json());task.messages=[{'id':'intent','role':'user','content':text,'business_time':'2026-11-03T09:00:00+00:00'}]
    task.scope=TaskScope(action='new',status='resolved',all_records=True,message_ids=['intent'])
    from app.intake import save
    with db.transaction() as c:save(c,task)
    return task


def request(state,action,**kwargs):
    return {'task_id':state['task']['id'],'request_id':str(uuid4()),'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],'action':action,**kwargs}


def test_start_answer_then_server_duration_proposal_can_be_adopted_and_previewed(database,monkeypatch):
    draft=insert(database,['start_at','end_at']);calls=[]
    class Model:
        async def ainvoke(self,messages):
            payload=json.loads(messages[-1][1]);facts=payload['calculated_schedule'];calls.append(facts)
            return model_response(DraftDecision(answer='计算得到的结束时间，待采纳',edits=[ChatEdit(requirement_id=f['requirement_id'],target_id=f['target_id'],values={f['field']:f['value']},sources={f['field']:f['source']}) for f in facts]))
    monkeypatch.setattr('app.agent.llm',lambda schema:Model())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=await task_with_message(client,database,draft,'本表全部新增搭建，跑三天。')
            state=(await client.get('/tasks/'+task.id)).json();answers={q['id']:'2028-01-10T09:00:00Z' for q in state['task']['questions'] if q['field']=='start_at'}
            result=await client.post(f'/projects/{database}/chat',json=request(state,'answer',answers=answers));assert result.status_code==200,result.text
            state=(await client.get('/tasks/'+task.id)).json();assert not state['preview']['can_confirm']
            body=request(state,'analyze');result=await client.post(f'/projects/{database}/chat',json=body);assert result.status_code==200,result.text
            assert (await client.post(f'/projects/{database}/chat',json=body)).json()==result.json()
            state=(await client.get('/tasks/'+task.id)).json();proposal=state['task']['proposal'];assert proposal and len(proposal['edits'])==3
            result=await client.post(f'/projects/{database}/chat',json=request(state,'accept',proposal_id=proposal['id']));assert result.status_code==200,result.text
            state=(await client.get('/tasks/'+task.id)).json();assert state['preview']['can_confirm'],state['preview']['issues']
            assert all(g['end_at']=='2028-01-13T09:00:00Z' and g['sources']['end_at']['kind']=='calculated' for g in state['draft']['requirements'][0]['structure']['groups'])
    asyncio.run(scenario());assert len(calls)==1


def test_history_batch_answer_reads_actual_owned_export_and_finishes_all_groups(database,tmp_path,monkeypatch):
    monkeypatch.setenv('STORAGE_DIRECTORY',str(tmp_path))
    from app.settings import settings
    settings.cache_clear();draft=insert(database,['audience'])
    wb=Workbook();s=wb.active;s.title='Ads';s.append(['Campaign ID','Ad Group ID','Ad ID','Post ID','Account ID','Campaign Name','Audience','Advertising Objective','Start Time','End Time','Timezone'])
    for batch in (1,2):
        for i in (1,2,3):s.append([str(batch*100+i),str(batch*1000+i),str(batch*10000+i),str(i*100),draft.requirements[0].account_id,'Batch '+str(batch),'Audience '+str(batch),'followers','2026-01-01T09:00:00Z','2026-01-04T09:00:00Z','UTC'])
    buf=BytesIO();wb.save(buf)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=await task_with_message(client,database,draft,'本表全部新增搭建，受众沿用上次，历史有两批。')
            upload=await client.post(f'/projects/{database}/files?kind=export&task_id={task.id}',files={'file':('export.xlsx',buf.getvalue())});assert upload.status_code==200,upload.text
            state=(await client.get('/tasks/'+task.id)).json();questions=[q for q in state['task']['questions'] if q['field']=='audience'];assert len(questions)==3
            assert all(q['options']==['沿用历史批次：Batch 1','沿用历史批次：Batch 2'] for q in questions)
            result=await client.post(f'/projects/{database}/chat',json=request(state,'answer',answers={q['id']:'沿用历史批次：Batch 2' for q in questions}));assert result.status_code==200,result.text
            state=(await client.get('/tasks/'+task.id)).json();assert state['preview']['can_confirm'],state['preview']['issues']
            assert all(g['audience']=='Audience 2' and g['sources']['audience']['kind']=='history' and g['sources']['audience']['accepted_by'] for g in state['draft']['requirements'][0]['structure']['groups'])
            assert not any(q['field']=='audience' for q in state['task']['questions'])
    asyncio.run(scenario())
