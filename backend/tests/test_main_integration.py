"""The platform administration and intake workspaces share one authenticated API."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer
import pytest

from app import main,auth


def test_platform_admin_and_intake_routes_are_both_available():
    schema=main.app.openapi()
    routes=[(method.upper(),path) for path,operations in schema['paths'].items() for method in operations if method in ('get','post','patch','put','delete')]
    required={('POST','/auth/login'),('POST','/ops/login'),('GET','/ops/tenants'),('POST','/ops/tenants'),
              ('POST','/ops/tenants/{tenant_id}/admins'),('PATCH','/ops/tenants/{tenant_id}/admins/{user_id}'),
              ('POST','/ops/tenants/{tenant_id}/admins/{user_id}/password'),
              ('POST','/projects/{project_id}/tasks'),('GET','/tasks/{task_id}'),
              ('POST','/projects/{project_id}/chat'),('POST','/projects/{project_id}/agent'),
              ('POST','/files/{file_id}/mapping/propose'),('POST','/files/{file_id}/mapping/confirm'),
              ('POST','/drafts/{draft_id}/structure-proposal'),('POST','/drafts/{draft_id}/confirm'),
              ('GET','/projects/{project_id}/history')}
    assert required<=set(routes)
    assert all(routes.count(key)==1 for key in required)


def test_login_issues_password_version_bound_token_that_authentication_honors(monkeypatch):
    secret='integration-session-secret-with-at-least-32-characters'
    password='development-only-long-login-password'
    account={'id':'user','email':'user@example.invalid','password_hash':auth.password_hash.hash(password),'active':True}
    class Result:
        def fetchone(self):return account
    class Connection:
        def execute(self,*_):return Result()
    @contextmanager
    def transaction():yield Connection()
    cfg=SimpleNamespace(session_secret=secret)
    monkeypatch.setattr(main,'transaction',transaction)
    monkeypatch.setattr(auth,'transaction',transaction)
    monkeypatch.setattr(main,'settings',lambda:cfg)
    monkeypatch.setattr(auth,'settings',lambda:cfg)
    response=main.login(main.LoginBody(email=account['email'],password=password))
    data=URLSafeTimedSerializer(secret,salt='adbuild-session').loads(response['access_token'])
    assert data['user_id']==account['id']
    assert data['credential_version']==auth.credential_version(account['password_hash'])
    actor=asyncio.run(auth.authenticate('Bearer '+response['access_token']))
    assert actor.user_id==account['id']
    account['password_hash']=auth.password_hash.hash('different-development-only-password')
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(auth.authenticate('Bearer '+response['access_token']))
    assert rejected.value.status_code==401
