import asyncio
from uuid import uuid4

import httpx
from app import db
from app.main import app
from app.models import BuildDraft,Requirement
from test_task_mysql import database


def test_one_structured_answer_updates_whole_exact_record_set_and_questions_stay_retired(database):
    async def scenario():
        draft=BuildDraft(id=str(uuid4()),project_id=database,requirements=[Requirement(id=rid,included=True,post_id='7700000000000000033') for rid in ('row-a','row-b')])
        with db.transaction() as c:
            tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
            c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,%s,%s)',(draft.id,tenant,database,1,db.json_value(draft)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            state=(await client.get('/tasks/'+task['id'])).json()
            questions=[q for q in state['task']['questions'] if q['field'] in ('audience','identity_id')]
            assert len(questions)==2
            assert all(q['requirement_ids']==['row-a','row-b'] for q in questions)
            answers={q['id']:'Explicit audience' if q['field']=='audience' else '7700000000000000044' for q in questions}
            body={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':state['task']['version'],
                  'expected_draft_version':state['draft']['version'],'action':'answer','answers':answers}
            answered=await client.post(f'/projects/{database}/chat',json=body)
            assert answered.status_code==200,answered.text
            assert (await client.post(f'/projects/{database}/chat',json=body)).json()==answered.json()
            final=(await client.get('/tasks/'+task['id'])).json()
            assert all(row['audience']=='Explicit audience' and row['identity_id']=='7700000000000000044' for row in final['draft']['requirements'])
            assert not any(q['field'] in ('audience','identity_id') for q in final['task']['questions'])
            with db.transaction() as c:
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())
