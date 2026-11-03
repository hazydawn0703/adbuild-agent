"""Imported history remains queryable without a build draft or write proposals."""
import asyncio
from io import BytesIO
import json
from uuid import uuid4

import httpx
from openpyxl import Workbook
from app import db
from app.main import app
from app.models import TaskScope,ScopeSelector,ChatDecision
from test_task_mysql import database


def test_export_only_task_can_query_history_without_materializing_a_draft(database,monkeypatch,tmp_path):
    monkeypatch.setenv('STORAGE_DIRECTORY',str(tmp_path))
    from app.settings import settings
    settings.cache_clear()
    post='7700000000000000033'
    captured=[]
    class Scope:
        async def ainvoke(self,messages):
            payload=json.loads(messages[-1][1]);source=next(m for m in reversed(payload['messages']) if m['role']=='user')
            return TaskScope(action='history',status='resolved',selectors=[ScopeSelector(field='post_id',values=[post])],message_ids=[source['id']])
    class Fields:
        async def ainvoke(self,messages):
            payload=json.loads(messages[-1][1]);captured.append(payload)
            assert payload['history_available']==1
            assert len(payload['history_query']['items'])==1
            assert 'private-raw-marker' not in json.dumps(payload)
            return ChatDecision(answer='找到一条导入快照，后台实时状态未核实')
    monkeypatch.setattr('app.agent.llm',lambda schema:Scope() if schema is TaskScope else Fields())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={'name':'history-only'})).json()
            wb=Workbook();s=wb.active
            s.append(['Campaign ID','Ad Group ID','Ad ID','Post ID','Campaign Name','Advertising Objective','Private unused column'])
            s.append(['7700000000000000011','7700000000000000022','7700000000000000044',post,'Prior launch','followers','private-raw-marker'])
            data=BytesIO();wb.save(data)
            uploaded=await client.post(f'/projects/{database}/files',params={'kind':'export','task_id':task['id']},files={'file':('history.xlsx',data.getvalue())})
            assert uploaded.status_code==200,uploaded.text
            state=(await client.get('/tasks/'+task['id'])).json()
            queried=await client.post(f'/projects/{database}/chat',json={'task_id':task['id'],'request_id':str(uuid4()),'expected_version':state['task']['version'],
                 'action':'message','message':f'只查询帖子{post}的历史，不修改广告草稿'})
            assert queried.status_code==200,queried.text
            reloaded=(await client.get('/tasks/'+task['id'])).json()
            assert reloaded['draft'] is None and reloaded['task']['proposal'] is None
            result=reloaded['task']['analysis']['history_query']
            assert result['total']==1 and result['coverage']['complete'] is True
            assert result['items'][0]['source_file']==uploaded.json()['id']
            assert captured and result['items'][0]['post_id']==post
            with db.transaction() as c:
                for table in ('drafts','runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())
