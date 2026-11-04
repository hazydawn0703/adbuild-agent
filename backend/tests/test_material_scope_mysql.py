import asyncio,json
from uuid import uuid4
import httpx
from app import db
from app.main import app
from app.models import TaskScope,ScopeSelector,ChatDecision
from model_response_fixture import model_response
from test_entity_answer_mysql import insert_draft
from test_task_mysql import database


def test_message_exclusion_and_reselection_keep_canonical_materials(database,monkeypatch):
    draft=insert_draft(database)
    class ScopeModel:
        async def ainvoke(self,messages):
            payload=json.loads(messages[-1][1]);m=next(m for m in reversed(payload['messages']) if m['role']=='user')
            exclusion='排除' in m['content']
            return model_response(TaskScope(action='exclude' if exclusion else 'new',status='resolved',
                all_records=not exclusion,selectors=[ScopeSelector(field='post_id',values=['200'])] if exclusion else [],message_ids=[m['id']]))
    class Fields:
        async def ainvoke(self,messages):return model_response(ChatDecision(answer='已记录明确范围'))
    monkeypatch.setattr('app.agent.llm',lambda schema:ScopeModel() if schema is TaskScope else Fields())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
            for message,expected in [('排除帖子200',['100','300']),('本次搭建全部记录',['100','200','300'])]:
                state=(await client.get('/tasks/'+task['id'])).json()
                result=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),
                    'expected_version':state['task']['version'],'expected_draft_version':state['draft']['version'],'action':'message','message':message})
                assert result.status_code==200,result.text
                state=(await client.get('/tasks/'+task['id'])).json()
                posts=[a['post_id'] for c in state['preview']['campaigns'] for g in c['adgroups'] for a in g['ads']]
                assert posts==expected
                assert len(state['draft']['requirements'][0]['structure']['groups'])==3
                assert state['draft']['approval'] is None
    asyncio.run(scenario())
