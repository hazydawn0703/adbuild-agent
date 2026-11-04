"""Actual batch-answer and edit APIs must retain independent entity ownership."""
import asyncio
from uuid import uuid4
import httpx
from app import db
from app.main import app
from app.models import BuildDraft,SourceRef
from test_task_mysql import database
from test_budget_action import allocation_state


def insert_draft(project,missing_posts=False):
    draft=BuildDraft(id=str(uuid4()),project_id=project,requirements=allocation_state()['draft']['requirements'])
    if missing_posts:
        for group in draft.requirements[0].structure.groups:
            for ad in group.ads:ad.post_id='';ad.post_url='';ad.sources={}
    with db.transaction() as c:
        tenant=c.execute('select tenant_id from projects where id=%s',(project,)).fetchone()['tenant_id']
        c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,1,%s)',(draft.id,tenant,project,db.json_value(draft)))
    return draft


def test_batch_distinct_material_answers_update_their_actual_nodes(database):
    draft=insert_draft(database,True)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            state=(await client.get('/tasks/'+task['id'])).json()
            questions=[q for q in state['task']['questions'] if q['field']=='post_id']
            assert len(questions)==3
            answers={q['id']:str(1000+i) for i,q in enumerate(questions)}
            result=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),
                'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],'action':'answer','answers':answers})
            assert result.status_code==200,result.text
            state=(await client.get('/tasks/'+task['id'])).json()
            actual={ad['id']:ad['post_id'] for group in state['draft']['requirements'][0]['structure']['groups'] for ad in group['ads']}
            assert actual=={q['target_ids'][0]:answers[q['id']] for q in questions}
            assert not any(q['field']=='post_id' for q in state['task']['questions'])
    asyncio.run(scenario())


def test_root_schedule_edit_is_rejected_and_targeted_edit_keeps_siblings(database):
    draft=insert_draft(database)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            groups=draft.requirements[0].structure.groups
            original=groups[0].end_at.isoformat()
            edit={'requirement_id':draft.requirements[0].id,'values':{'end_at':'2028-01-20T09:00:00Z'}}
            url='/drafts/'+draft.id+'/edit'
            assert (await client.post(url,json={'expected_version':1,'edits':[edit]})).status_code==422
            edit['target_id']=groups[1].id
            result=await client.post(url,json={'expected_version':1,'edits':[edit]})
            assert result.status_code==200,result.text
            actual=result.json()['requirements'][0]['structure']['groups']
            from datetime import datetime
            assert datetime.fromisoformat(actual[0]['end_at'].replace('Z','+00:00')).isoformat()==original
            assert actual[1]['end_at']=='2028-01-20T09:00:00Z'
    asyncio.run(scenario())


def test_budget_level_answer_then_group_amounts_completes_existing_tree(database):
    draft=insert_draft(database);req=draft.requirements[0]
    req.name='Explicit plan';req.structure.budget_level=None
    # Known common currency/mode, but level and group amounts are genuinely absent.
    with db.transaction() as c:c.execute('update drafts set data=%s where id=%s',(db.json_value(draft),draft.id))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            async def answer(field,value):
                state=(await client.get('/tasks/'+task['id'])).json()
                questions=[q for q in state['task']['questions'] if q['field']==field]
                assert questions
                result=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),
                    'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],
                    'action':'answer','answers':{q['id']:value for q in questions}})
                assert result.status_code==200,result.text
            await answer('budget_level','广告组预算')
            state=(await client.get('/tasks/'+task['id'])).json()
            assert len([q for q in state['task']['questions'] if q['field']=='budget' and q['target_ids']!=[req.id]])==3
            await answer('budget','100')
            state=(await client.get('/tasks/'+task['id'])).json()
            final=state['draft']['requirements'][0]
            assert final['structure']['budget_level']=='adgroup' and final['budget']=='300'
            assert [g['budget']['amount'] for g in final['structure']['groups']]==['100']*3
            assert [g['ads'][0]['post_id'] for g in final['structure']['groups']]==['100','200','300']
            assert not any(q['field'] in ('budget_level','budget') for q in state['task']['questions'])
    asyncio.run(scenario())
