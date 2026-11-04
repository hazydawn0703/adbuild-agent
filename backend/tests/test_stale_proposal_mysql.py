import asyncio
from uuid import uuid4
import httpx
from app import db
from app.main import app
from app.models import BuildDraft,Requirement,ChatDecision,ChatEdit
from model_response_fixture import model_response
from test_task_mysql import database


def test_new_user_message_invalidates_old_pending_proposal(database,monkeypatch):
    class Model:
        async def ainvoke(self,messages):return model_response(ChatDecision(answer='新条件已记录'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            draft=BuildDraft(id=str(uuid4()),project_id=database,requirements=[Requirement(id='r',budget=50)])
            with db.transaction() as c:
                tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
                c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,1,%s)',(draft.id,tenant,database,db.json_value(draft)))
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            from app.models import IntakeTask,ChangeProposal
            from app.intake import save
            current=IntakeTask.model_validate(task)
            current.proposal=ChangeProposal(id='old',draft_version=1,reason='old budget',message_id='before',edits=[ChatEdit(requirement_id='r',values={'budget':'40'})])
            with db.transaction() as c:save(c,current)
            body={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':task['version'],'expected_draft_version':1,
                'action':'message','message':'预算仍待讨论，请先记录新条件'}
            response=await client.post(f'/projects/{database}/chat',json=body)
            assert response.status_code==200,response.text
            state=response.json()
            assert state['task']['proposal'] is None
            body.update(request_id=str(uuid4()),expected_version=state['task']['version'],action='accept',message='',proposal_id='old')
            assert (await client.post(f'/projects/{database}/chat',json=body)).status_code==409
            assert state['draft']['requirements'][0]['budget']=='50'
    asyncio.run(scenario())
