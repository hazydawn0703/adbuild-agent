from model_response_fixture import row_bindings
"""Mapping availability retries preserve real task/file/database state."""
import asyncio
from io import BytesIO

import httpx
from openpyxl import Workbook
import pytest

from app import db,main
from app.agent import ModelServiceError
from app.models import MappingProposal,SheetMapping
from test_task_mysql import database


@pytest.mark.parametrize('failure',['timeout','service_unavailable'])
def test_mapping_availability_failure_can_continue_with_saved_evidence(database,monkeypatch,tmp_path,failure):
    monkeypatch.setenv('STORAGE_DIRECTORY',str(tmp_path))
    from app.settings import settings
    settings.cache_clear()
    calls=[]
    async def propose(inspection,data,task,on_progress):
        calls.append(1)
        if len(calls)==1:
            on_progress(MappingProposal(mappings=[],complete=False,explanation='progress saved',
                                        read_state={'file_sha256':inspection['sha256'],'evidence':[{'coverage':{'read_cells':2}}]}))
            if failure=='timeout':raise TimeoutError('injected timeout')
            raise ModelServiceError('upstream_server',retryable=True,upstream_status=503)
        assert task.analysis['mapping']['proposal']['read_state']['evidence'][0]['coverage']['read_cells']==2
        return MappingProposal(mappings=[SheetMapping(sheet='Sheet',header_row=1,first_row=2,last_row=2,bindings=row_bindings({'post_url':'A','objective':'B','budget':'C'}))],explanation='continued saved mapping')
    monkeypatch.setattr(main,'propose_mapping',propose)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            task=(await client.post(f'/projects/{database}/tasks',json={})).json()
            wb=Workbook();wb.active.append(['Post','Goal','Budget']);wb.active.append(['https://www.tiktok.com/@a/video/123','followers',50])
            stream=BytesIO();wb.save(stream)
            uploaded=await client.post(f'/projects/{database}/files',params={'task_id':task['id'],'kind':'requirements'},files={'file':('input.xlsx',stream.getvalue())})
            assert uploaded.status_code==200,uploaded.text
            file_id=uploaded.json()['id']
            first=await client.post('/files/'+file_id+'/mapping/propose')
            assert first.status_code==(504 if failure=='timeout' else 503),first.text
            assert first.json()['retryable'] is True
            state=(await client.get('/tasks/'+task['id'])).json()
            assert state['task']['analysis']['mapping_error']['retryable'] is True
            assert state['task']['analysis']['mapping']['proposal']['read_state']['evidence'][0]['coverage']['read_cells']==2
            continued=await client.post('/files/'+file_id+'/mapping/propose')
            assert continued.status_code==200,continued.text
            assert continued.json()['complete'] is True and len(calls)==2
            final=(await client.get('/tasks/'+task['id'])).json()
            assert 'mapping_error' not in final['task']['analysis'] and final['draft'] is None
            with db.transaction() as c:
                for table in ('runs','deployments'):
                    assert c.execute('select count(*) n from '+table+' where project_id=%s',(database,)).fetchone()['n']==0
    asyncio.run(scenario())

