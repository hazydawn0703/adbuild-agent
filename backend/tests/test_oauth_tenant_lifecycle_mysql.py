"""Real isolated MySQL check of an OAuth writer held across tenant closure."""
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
import pytest

from app import db,ops,tiktok
from test_task_mysql import database


def test_tenant_closure_prevents_existing_oauth_storage_from_recreating_credentials(database,monkeypatch):
    with db.transaction() as c:
        tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
        prior_jobs={row['id'] for row in c.execute('select id from jobs').fetchall()}
    monkeypatch.setattr(tiktok,'settings',lambda:SimpleNamespace(token_encryption_key=Fernet.generate_key().decode()))
    storage=tiktok.EncryptedStorage(tenant)
    storage.update(status='authorizing')
    assert storage.read()['status']=='authorizing'
    # This is the same transactional platform closure used by POST /ops/.../close.
    assert ops.delete_tenant(tenant,_=object())=={'deleted':True}
    with pytest.raises(HTTPException) as error:storage.update(status='connected',tokens='late-'+uuid4().hex)
    assert error.value.status_code==404
    assert storage.read()=={}
    with db.transaction() as c:
        assert not c.execute('select id from tenants where id=%s',(tenant,)).fetchone()
        assert not c.execute('select tenant_id from connections where tenant_id=%s',(tenant,)).fetchone()
        # Closure still queues durable checkpoint/file cleanup while ads stay untouched.
        cleanup_jobs=[row['id'] for row in c.execute('select id,message from jobs').fetchall()
                      if row['id'] not in prior_jobs and row['message']=={'storage_paths':[],'checkpoint_threads':[]}]
        assert len(cleanup_jobs)==1
        c.execute('delete from jobs where id=%s',(cleanup_jobs[0],))
