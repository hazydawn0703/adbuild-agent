from contextlib import contextmanager
from types import SimpleNamespace
from time import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from app.main import app
from app import ops, auth


@pytest.fixture
def client(monkeypatch):
    config = SimpleNamespace(session_secret='test-only-secret-' * 3, app_origin='https://ops.test',
                             ops_password_hash=auth.password_hash.hash('test-platform-password'))
    monkeypatch.setattr(ops, 'settings', lambda: config)
    monkeypatch.setattr(auth, 'settings', lambda: config)
    ops._sessions.clear()
    ops._attempts.clear()
    with TestClient(app, base_url='https://ops.test', headers={'Origin': 'https://ops.test'}) as client:
        yield client
    ops._sessions.clear()
    ops._attempts.clear()


def login(client):
    return client.post('/ops/login', json={'password': 'test-platform-password'})


def test_all_platform_endpoints_require_platform_session(client):
    for method, path, body in [
        ('GET', '/ops/session', None), ('GET', '/ops/usage', None), ('GET', '/ops/tenants', None),
        ('POST', '/ops/tenants', {}), ('POST', '/ops/tenants/t/name', {'name':'new'}), ('POST', '/ops/tenants/t/close', None), ('POST', '/ops/tenants/t/admins', {}),
        ('POST', '/ops/tenants/t/admins/u/status', {'active': False}),
        ('POST', '/ops/tenants/t/admins/u/password', {'password': 'new-password-123456'}),
    ]:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, (path, response.text)
    customer_token = URLSafeTimedSerializer(ops.settings().session_secret, salt='adbuild-session').dumps({'user_id': 'customer'})
    assert client.get('/ops/tenants', headers={'Authorization': 'Bearer ' + customer_token}).status_code == 401
    client.cookies.set(ops.COOKIE, customer_token, path=ops.COOKIE_PATH)
    assert client.get('/ops/session').status_code == 401


def test_login_cookie_expiry_and_server_side_logout(client):
    assert client.post('/ops/login', json={'password': 'wrong'}).status_code == 401
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert all(value in cookie for value in ('HttpOnly', 'Secure', 'SameSite=strict', 'Max-Age=3600', 'Path=/apps/adbuild-agent'))
    token = client.cookies.get(ops.COOKIE)
    # TestClient requests use unprefixed paths; forward the path-scoped cookie explicitly.
    headers = {'Cookie': f'{ops.COOKIE}={token}'}
    assert client.get('/ops/session', headers=headers).json()['role'] == 'platform_admin'
    assert client.get('/ops/session', headers=headers).headers['cache-control'] == 'no-store'
    assert client.get('/projects', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert client.post('/ops/logout', headers=headers).status_code == 200
    assert client.get('/ops/session', headers=headers).status_code == 401
    login(client)
    token = client.cookies.get(ops.COOKIE)
    for session in ops._sessions: ops._sessions[session] = time() - 1
    assert client.get('/ops/session', headers={'Cookie': f'{ops.COOKIE}={token}'}).status_code == 401


def test_csrf_and_login_throttling(client):
    assert client.post('/ops/login', headers={'Origin': 'https://evil.test'}, json={'password':'test-platform-password'}).status_code == 403
    for _ in range(10):
        assert client.post('/ops/login', json={'password':'wrong'}).status_code == 401
    assert login(client).status_code == 429


def test_password_reset_revokes_customer_token_and_disabled_users(client, monkeypatch):
    state = {'id':'customer', 'password_hash':auth.password_hash.hash('old-password-123456'), 'active':True}
    class Cursor:
        def fetchone(self): return state if state['active'] else None
    class DB:
        def execute(self, *args): return Cursor()
    @contextmanager
    def transaction(): yield DB()
    monkeypatch.setattr(auth, 'transaction', transaction)
    @app.get('/_test/customer-session')
    def customer_session(actor=__import__('fastapi').Depends(auth.authenticate)):
        return {'id': actor.user_id}
    token = URLSafeTimedSerializer(ops.settings().session_secret, salt='adbuild-session').dumps({
        'user_id':'customer', 'credential_version':auth.credential_version(state['password_hash'])})
    headers = {'Authorization':'Bearer '+token}
    try:
        assert client.get('/_test/customer-session', headers=headers).status_code == 200
        state['active'] = False
        assert client.get('/_test/customer-session', headers=headers).status_code == 401
        state['active'] = True
        state['password_hash'] = auth.password_hash.hash('new-password-123456')
        assert client.get('/_test/customer-session', headers=headers).status_code == 401
    finally:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route,'path','') != '/_test/customer-session']


def test_status_action_and_last_admin_protection(client, monkeypatch):
    state = {'active': True, 'remaining': 1}
    class DB:
        def execute(self, sql, params=()):
            self.row = {'id':'u','active':state['active']}
            if 'count(*)' in sql: self.row = {'total':state['remaining']}
            if sql.startswith('update users set active='): state['active'] = params[0]
            return self
        def fetchone(self): return self.row
    @contextmanager
    def transaction(): yield DB()
    monkeypatch.setattr(ops, 'transaction', transaction)
    login(client)
    headers = {'Cookie':f'{ops.COOKIE}={client.cookies.get(ops.COOKIE)}'}
    path = '/ops/tenants/t/admins/u/status'
    assert client.post(path, headers=headers, json={'active':False}).status_code == 200
    assert state['active'] is False
    assert client.post(path, headers=headers, json={'active':True}).status_code == 200
    state['remaining'] = 0
    assert client.post(path, headers=headers, json={'active':False}).status_code == 409
    assert state['active'] is True


def test_rename_and_delete_tenant(client, monkeypatch):
    statements = []
    class DB:
        def execute(self, sql, params=()):
            statements.append((sql,params))
            return self
        def fetchone(self): return {'id':'t'}
        def fetchall(self): return []
    @contextmanager
    def transaction(): yield DB()
    monkeypatch.setattr(ops, 'transaction', transaction)
    login(client)
    headers = {'Cookie':f'{ops.COOKIE}={client.cookies.get(ops.COOKIE)}'}
    assert client.post('/ops/tenants/t/name',headers=headers,json={'name':'  新名称  '}).status_code == 200
    assert ('update tenants set name=%s where id=%s',('新名称','t')) in statements
    assert client.post('/ops/tenants/t/name',headers=headers,json={'name':'   '}).status_code == 422
    assert client.post('/ops/tenants/t/close',headers=headers).status_code == 200
    assert ('delete from tenants where id=%s',('t',)) in statements
    assert ('delete from connections where tenant_id=%s',('t',)) in statements


def test_project_cleanup_queues_files_and_threads_and_blocks_active_runs():
    import json
    from fastapi import HTTPException
    from app.project_cleanup import delete_project_data
    class DB:
        def __init__(self, active=False): self.active=active;self.statements=[]
        def execute(self, sql, params=()):
            self.statements.append((sql,params));self.sql=sql
            return self
        def fetchone(self): return {'id':'run'} if 'select id from runs' in self.sql and self.active else None
        def fetchall(self):
            if 'intake_tasks' in self.sql: return [{'data':{'id':'task','requests':{'request':{}}}}]
            if 'storage_path' in self.sql: return [{'storage_path':'tenant/file.xlsx'}]
            return []
    blocked=DB(active=True)
    with pytest.raises(HTTPException) as error: delete_project_data(blocked,{'id':'p','tenant_id':'tenant'})
    assert error.value.status_code == 409
    assert not any(sql.startswith(('delete','insert')) for sql,_ in blocked.statements)
    c=DB()
    delete_project_data(c,{'id':'p','tenant_id':'tenant'})
    cleanup=next(json.loads(params[0]) for sql,params in c.statements if sql.startswith('insert into jobs'))
    assert cleanup == {'storage_paths':['tenant/file.xlsx'],'checkpoint_threads':['tenant:task','tenant:task:request']}
    for table in ('deployments','runs','intake_tasks','drafts','files','history','messages','mapping_templates','project_contexts'):
        assert ('delete from '+table+' where project_id=%s',('p',)) in c.statements

