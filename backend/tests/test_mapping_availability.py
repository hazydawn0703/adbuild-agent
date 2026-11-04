import asyncio
from contextlib import contextmanager
from copy import deepcopy

import httpx
from app import main,intake
from app.auth import Actor,authenticate
from app.models import IntakeTask,MappingProposal


def test_mapping_timeout_has_safe_retry_metadata_and_keeps_saved_progress(monkeypatch):
    task=IntakeTask(id='task',project_id='project')
    saved=[]
    row={'id':'file','project_id':'project','storage_path':'private/file.xlsx','inspection':{'sheets':[]}}
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchone(self):return self.rows[0] if self.rows else None
        def fetchall(self):return self.rows
    class Connection:
        def execute(self,sql,*_):
            if sql.startswith('select tenant_id from projects'):return Result([{'tenant_id':'tenant'}])
            if 'select * from files' in sql:return Result([row])
            if 'select id from intake_tasks' in sql:return Result([{'id':task.id}])
            return Result([])
    @contextmanager
    def transaction():yield Connection()
    async def download(_):return b'unchanged-workbook'
    async def propose(inspection,data,task,on_progress):
        on_progress(MappingProposal(mappings=[],complete=False,explanation='read saved',read_state={'evidence':[{'coverage':{'read_cells':2}}]}))
        raise TimeoutError('private-provider-error-must-not-leak')
    monkeypatch.setattr('app.usage.transaction',transaction)
    monkeypatch.setattr(main,'transaction',transaction)
    monkeypatch.setattr(main,'project_access',lambda *_a,**_k:{'id':'project'})
    monkeypatch.setattr(intake,'access',lambda *_a,**_k:(task,{'id':'project'}))
    monkeypatch.setattr(intake,'save',lambda _c,current:saved.append(deepcopy(current.model_dump(mode='json'))))
    monkeypatch.setattr(main.storage,'download',download)
    monkeypatch.setattr(main,'propose_mapping',propose)
    previous=main.app.dependency_overrides.get(authenticate)
    main.app.dependency_overrides[authenticate]=lambda:Actor('operator')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            response=await client.post('/files/file/mapping/propose')
            assert response.status_code==504 and response.json()['retryable'] is True
            assert response.json()['code']=='analysis_timeout'
            assert 'private-provider-error' not in response.text
    try:asyncio.run(scenario())
    finally:
        if previous is None:main.app.dependency_overrides.pop(authenticate,None)
        else:main.app.dependency_overrides[authenticate]=previous
    assert saved[-1]['stage']=='attention' and saved[-1]['lease'] is None
    assert saved[-1]['analysis']['mapping']['proposal']['read_state']['evidence'][0]['coverage']['read_cells']==2
    assert saved[-1]['analysis']['mapping_error']['code']=='analysis_timeout'
    assert saved[-1]['analysis']['mapping_error']['retryable'] is True
