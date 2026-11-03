"""Late OAuth state writes must never recreate a deleted customer connection."""
from contextlib import contextmanager
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import HTTPException
import pytest

from app import tiktok


@pytest.mark.parametrize('exists',[False,True])
def test_oauth_storage_locks_existing_tenant_before_upserting(monkeypatch,exists):
    statements=[]
    class DB:
        def execute(self,sql,args=()):
            statements.append((sql,args))
            if sql.startswith('select id from tenants'):
                assert sql.endswith('for update') and args==('tenant',)
                return SimpleNamespace(fetchone=lambda:{'id':'tenant'} if exists else None)
            assert exists and sql.startswith('insert into connections')
            return SimpleNamespace(fetchone=lambda:None)
    @contextmanager
    def transaction():yield DB()
    monkeypatch.setattr(tiktok,'transaction',transaction)
    monkeypatch.setattr(tiktok,'settings',lambda:SimpleNamespace(token_encryption_key=Fernet.generate_key().decode()))
    storage=tiktok.EncryptedStorage('tenant')
    if exists:
        storage.update(status='connected')
        assert len(statements)==2
    else:
        with pytest.raises(HTTPException) as error:storage.update(tokens='synthetic-private-token')
        assert error.value.status_code==404
        assert 'synthetic-private-token' not in str(error.value)
        assert len(statements)==1


def test_late_oauth_update_after_tenant_deletion_is_rejected(monkeypatch):
    state={'exists':True,'connection':None}
    class DB:
        def execute(self,sql,args=()):
            if sql.startswith('select id from tenants'):
                return SimpleNamespace(fetchone=lambda:{'id':'tenant'} if state['exists'] else None)
            assert sql.startswith('insert into connections')
            state['connection']=args[1]
    @contextmanager
    def transaction():yield DB()
    monkeypatch.setattr(tiktok,'transaction',transaction)
    monkeypatch.setattr(tiktok,'settings',lambda:SimpleNamespace(token_encryption_key=Fernet.generate_key().decode()))
    storage=tiktok.EncryptedStorage('tenant')
    storage.update(status='authorizing')
    assert state['connection']
    state.update(exists=False,connection=None)
    with pytest.raises(HTTPException) as error:storage.update(status='connected',tokens='late-private-token')
    assert error.value.status_code==404 and state['connection'] is None
