"""Lifecycle authorization stays separate from task/model recovery."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import intake,main,service,worker
from app.auth import Actor
from app.models import IntakeTask,BuildDraft


class Result:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


@pytest.mark.parametrize('operation',['edit','confirm'])
def test_archived_draft_state_is_checked_only_after_tenant_access(monkeypatch,operation):
    queries=[]
    draft=BuildDraft(id='foreign-draft',project_id='foreign-project')
    class DB:
        def execute(self,sql,args=()):
            queries.append(sql)
            if sql.startswith('select * from drafts'):
                return Result({'project_id':draft.project_id,'data':draft.model_dump(mode='json')})
            pytest.fail('Foreign archive state must not be inspected before authorization')
    def denied(*_a,**_k):raise HTTPException(404,'项目不存在')
    module=service if operation=='edit' else main
    monkeypatch.setattr(module,'project_access',denied)
    with pytest.raises(HTTPException) as error:
        if operation=='edit':service.edit_draft(DB(),draft.id,Actor('other-tenant'),1,[])
        else:main.draft_access(DB(),draft.id,Actor('other-tenant'),write=True)
    assert error.value.status_code==404 and len(queries)==1


@pytest.mark.parametrize('operation',['edit','confirm'])
def test_owned_archived_draft_rejects_writes_before_mutation(monkeypatch,operation):
    draft=BuildDraft(id='draft',project_id='project')
    class DB:
        def execute(self,sql,args=()):
            if sql.startswith('select * from drafts'):
                return Result({'project_id':draft.project_id,'data':draft.model_dump(mode='json')})
            if sql.startswith('select data from intake_tasks'):
                return Result({'data':{'archived_at':'2026-11-03T00:00:00+00:00'}})
            pytest.fail('Archived draft cannot mutate or inspect unrelated business values: '+sql)
    module=service if operation=='edit' else main
    monkeypatch.setattr(module,'project_access',lambda *_a,**_k:{'id':'project'})
    with pytest.raises(HTTPException) as error:
        if operation=='edit':service.edit_draft(DB(),draft.id,Actor('operator'),1,[])
        else:main.draft_access(DB(),draft.id,Actor('operator'),write=True)
    assert error.value.status_code==409 and '归档' in error.value.detail


def test_archive_response_uses_public_projection(monkeypatch):
    task=IntakeTask(id='task',project_id='project',
        analysis={'authorization_code':'private-material'},requests={'old':{'result':{'api_key':'private-key'}}})
    @contextmanager
    def transaction():yield SimpleNamespace(execute=lambda *_a:None)
    monkeypatch.setattr(intake,'transaction',transaction)
    monkeypatch.setattr(intake,'access',lambda *_a,**_k:(task,{'tenant_id':'tenant'}))
    monkeypatch.setattr(intake,'project_access',lambda *_a,**_k:{'tenant_id':'tenant'})
    monkeypatch.setattr(intake,'save',lambda *_a:None)
    result=intake.archive_task('task',intake.ArchiveTask(expected_version=1,archived=True),Actor('operator'))
    assert result['version']==2 and result['archived_at']
    assert 'requests' not in result and 'lease' not in result
    assert 'private-material' not in str(result) and 'private-key' not in str(result)


def test_worker_can_complete_cleanup_while_advertising_is_disabled(monkeypatch):
    calls=[];complete=set()
    monkeypatch.setattr('app.settings.settings',lambda:SimpleNamespace(execution_enabled=False))
    class DB:
        def execute(self,sql,args=()):
            calls.append((sql,args))
            if sql.startswith('select * from jobs'):
                assert args==(False,) and "JSON_CONTAINS_PATH(message,'one','$.storage_paths')" in sql
                return Result(None if complete else {'id':7,'message':{'storage_paths':['owned/file.xlsx'],'checkpoint_threads':[]}})
            if "set status='complete'" in sql:complete.add(args[0])
            else:assert "set status='processing'" in sql
            return Result(None)
    @contextmanager
    def transaction():yield DB()
    async def cleanup(data):calls.append(('cleanup',data))
    async def stop(_):raise asyncio.CancelledError
    monkeypatch.setattr(worker,'transaction',transaction)
    monkeypatch.setattr(worker,'cleanup',cleanup)
    monkeypatch.setattr(worker.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):asyncio.run(worker.main())
    assert complete=={7}
    assert sum(sql=='cleanup' for sql,_ in calls)==1
    assert not any('runs' in sql for sql,_ in calls)
