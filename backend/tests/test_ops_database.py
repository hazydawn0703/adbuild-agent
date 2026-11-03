"""Opt-in MySQL smoke test: every write stays inside one rolled-back transaction."""
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import auth, db, main, ops


@pytest.mark.skipif(os.getenv('OPS_ROLLBACK_TEST') != '1', reason='Requires explicit rollback-only MySQL smoke test')
def test_platform_customer_lifecycle_and_isolation(monkeypatch):
    raw = db.engine().raw_connection()
    raw.begin()
    connection = db.Connection(raw)
    @contextmanager
    def transaction():
        # Preserve request-level atomicity while preventing any test data from committing.
        name = 'ops_' + uuid4().hex
        connection.execute('SAVEPOINT ' + name)
        try:
            yield connection
        except BaseException:
            connection.execute('ROLLBACK TO SAVEPOINT ' + name)
            raise
        finally:
            connection.execute('RELEASE SAVEPOINT ' + name)
    for module in (ops, main, auth): monkeypatch.setattr(module, 'transaction', transaction)
    password = 'test-only-password-' + uuid4().hex
    monkeypatch.setattr(ops, 'settings', lambda: type('Config', (), {
        'app_origin':'https://ops.test', 'session_secret':'test-only-secret-'*3,
        'ops_password_hash':auth.password_hash.hash(password)})())
    # Keep the hash stable across session checks.
    config = ops.settings()
    monkeypatch.setattr(ops, 'settings', lambda: config)
    monkeypatch.setattr(main, 'settings', lambda: config)
    monkeypatch.setattr(auth, 'settings', lambda: config)
    ops._sessions.clear()
    ops._attempts.clear()
    try:
        with TestClient(main.app, base_url='https://ops.test', headers={'Origin':'https://ops.test'}) as client:
            assert client.post('/ops/login', json={'password':password}).status_code == 200
            token = client.cookies.get(ops.COOKIE)
            platform = {'Cookie':f'{ops.COOKIE}={token}'}
            email = uuid4().hex + '@test.invalid'
            body = {'name':'rollback-only-'+uuid4().hex, 'project_name':'test project', 'account_id':'',
                    'email':email, 'password':'customer-password-123456'}
            response = client.post('/ops/tenants', headers=platform, json=body)
            assert response.status_code == 201, response.text
            tenant, user, project = [response.json()[key] for key in ('id','user_id','project_id')]
            # Duplicate account must roll back the new tenant as well.
            assert client.post('/ops/tenants', headers=platform, json=body).status_code == 409
            assert connection.execute('select count(*) as n from tenants where name=%s',(body['name'],)).fetchone()['n'] == 1
            customer = client.post('/auth/login',json={'email':email,'password':body['password']}).json()['access_token']
            customer_headers = {'Authorization':'Bearer '+customer}
            assert client.get('/projects',headers=customer_headers).json()[0]['id'] == project
            assert client.get('/ops/tenants',headers=customer_headers).status_code == 401
            account_path = f'/ops/tenants/{tenant}/admins/{user}'
            assert client.patch(account_path, headers=platform, json={'active':False}).status_code == 409
            second = client.post(f'/ops/tenants/{tenant}/admins',headers=platform,
                                 json={'email':uuid4().hex+'@test.invalid','password':body['password']})
            assert second.status_code == 201
            assert client.patch(account_path,headers=platform,json={'active':False}).status_code == 200
            assert client.get('/projects',headers=customer_headers).status_code == 401
            assert client.patch(account_path,headers=platform,json={'active':True}).status_code == 200
            assert client.post(account_path+'/password',headers=platform,json={'password':'replacement-password-123456'}).status_code == 200
            assert client.get('/projects',headers=customer_headers).status_code == 401
            assert client.post('/auth/login',json={'email':email,'password':body['password']}).status_code == 401
            assert client.post('/auth/login',json={'email':email,'password':'replacement-password-123456'}).status_code == 200
            assert client.post(f'/ops/tenants/{uuid4()}/admins/{user}/password',headers=platform,
                               json={'password':'another-password-123456'}).status_code == 404
            # All mutations produce audit records without credentials.
            records = connection.execute('select data from audit where tenant_id=%s',(tenant,)).fetchall()
            assert len(records) == 5
            assert all('password' not in str(row['data']) for row in records)
    finally:
        raw.rollback()
        raw.close()
        ops._sessions.clear()
        ops._attempts.clear()
