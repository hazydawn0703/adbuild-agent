"""Platform-only operations; customer bearer tokens never grant platform access."""
from collections import OrderedDict
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from threading import Lock
from time import monotonic, time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field, field_validator
from pymysql.err import IntegrityError

from .auth import password_hash
from .db import transaction, json_value
from .settings import settings

COOKIE = 'adbuild-ops-session'
COOKIE_PATH = '/apps/adbuild-agent'
SESSION_SECONDS = 3600
_lock = Lock()
_sessions: dict[str, float] = {}
_attempts: OrderedDict[str, list[float]] = OrderedDict()


def same_origin(request: Request):
    if request.method in ('GET', 'HEAD'): return
    expected = urlsplit(settings().app_origin)
    if request.headers.get('origin') != f'{expected.scheme}://{expected.netloc}':
        raise HTTPException(403, '请求来源无效，请从平台管理页面操作')


router = APIRouter(prefix='/ops', dependencies=[Depends(same_origin)])


def serializer():
    return URLSafeTimedSerializer(settings().session_secret, salt='adbuild-platform')


def authenticate_platform(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    try:
        data = serializer().loads(request.cookies.get(COOKIE, ''), max_age=SESSION_SECONDS)
        with _lock:
            expires = _sessions.get(data.get('session', ''), 0)
        if expires <= time() or not settings().ops_password_hash or not compare_digest(
            data.get('credential', ''), sha256(settings().ops_password_hash.encode()).hexdigest()
        ): raise BadSignature('Expired session')
    except BadSignature:
        raise HTTPException(401, '请先验证平台管理员密码') from None
    return data['session']


class PlatformLogin(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(body: PlatformLogin, request: Request, response: Response):
    configured = settings().ops_password_hash
    if not configured: raise HTTPException(503, '平台管理密码尚未配置')
    address = request.client.host if request.client else 'unknown'
    # Only the loopback reverse proxy may supply the real client address.
    if address in ('127.0.0.1', '::1'):
        address = request.headers.get('x-real-ip', address)
    now = monotonic()
    with _lock:
        attempts = [stamp for stamp in _attempts.get(address, []) if stamp > now - 900]
        if len(attempts) >= 10:
            raise HTTPException(429, '尝试次数过多，请在 15 分钟后重试', headers={'Retry-After': '900'})
        attempts.append(now)
        _attempts[address] = attempts
        _attempts.move_to_end(address)
        while len(_attempts) > 4096: _attempts.popitem(last=False)
    if not password_hash.verify(body.password, configured):
        raise HTTPException(401, '平台管理员密码不正确')
    session = token_urlsafe(32)
    with _lock:
        _attempts.pop(address, None)
        for key in list(_sessions):
            if _sessions[key] <= time(): del _sessions[key]
        if len(_sessions) >= 256: raise HTTPException(429, '平台会话过多，请稍后重试')
        _sessions[session] = time() + SESSION_SECONDS
    token = serializer().dumps({'session': session, 'credential': sha256(configured.encode()).hexdigest()})
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, path=COOKIE_PATH,
                        secure=urlsplit(settings().app_origin).scheme == 'https', httponly=True, samesite='strict')
    response.headers['Cache-Control'] = 'no-store'
    return {'role': 'platform_admin', 'expires_in': SESSION_SECONDS}


@router.get('/session')
def session(_=Depends(authenticate_platform)):
    return {'role': 'platform_admin'}


@router.post('/logout')
def logout(response: Response, session=Depends(authenticate_platform)):
    with _lock: _sessions.pop(session, None)
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=urlsplit(settings().app_origin).scheme == 'https',
                           httponly=True, samesite='strict')
    return {'ok': True}


class AdminAccount(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=16, max_length=256)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if len(value.split('@')) != 2 or any(char.isspace() for char in value) or not all(value.split('@')):
            raise ValueError('请输入有效邮箱')
        return value


class NewTenant(AdminAccount):
    name: str = Field(min_length=1, max_length=255)
    project_name: str = Field(min_length=1, max_length=255)
    account_id: str = Field(default='', max_length=100)

    @field_validator('name', 'project_name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('名称不能为空')
        return value.strip()


class UpdateAdmin(BaseModel):
    active: bool


class RenameTenant(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('名称不能为空')
        return value.strip()


class ResetPassword(BaseModel):
    password: str = Field(min_length=16, max_length=256)


def audit(conn, tenant_id, event, data):
    conn.execute('insert into audit(tenant_id,event,data) values(%s,%s,%s)',
                 (tenant_id, event, json_value({'actor': 'platform_admin', **data})))


def create_admin(conn, tenant_id, body):
    user_id = str(uuid4())
    try:
        conn.execute('insert into users(id,email,password_hash) values(%s,%s,%s)',
                     (user_id, body.email, password_hash.hash(body.password)))
    except IntegrityError:
        raise HTTPException(409, '该邮箱已存在，请使用其他邮箱') from None
    conn.execute("insert into memberships(tenant_id,user_id,role) values(%s,%s,'admin')", (tenant_id, user_id))
    return user_id


def require_tenant(conn, tenant_id):
    row = conn.execute('select id from tenants where id=%s for update', (tenant_id,)).fetchone()
    if not row: raise HTTPException(404, '客户空间不存在')


def require_admin(conn, tenant_id, user_id):
    require_tenant(conn, tenant_id)
    row = conn.execute("select u.id,u.active from users u join memberships m on m.user_id=u.id where m.tenant_id=%s and u.id=%s and m.role='admin' for update", (tenant_id, user_id)).fetchone()
    if not row: raise HTTPException(404, '管理员不属于该客户空间')
    return row


@router.get('/tenants')
def list_tenants(_=Depends(authenticate_platform)):
    with transaction() as c:
        tenants = c.execute('select t.id,t.name,(select count(*) from projects p where p.tenant_id=t.id) as project_count from tenants t order by t.name,t.id').fetchall()
        admins = c.execute("select m.tenant_id,u.id,u.email,u.active from memberships m join users u on u.id=m.user_id where m.role='admin' order by u.email").fetchall()
    for tenant in tenants:
        tenant['admins'] = [admin for admin in admins if admin['tenant_id'] == tenant['id']]
    return tenants


@router.post('/tenants', status_code=201)
def new_tenant(body: NewTenant, _=Depends(authenticate_platform)):
    tenant_id, project_id = str(uuid4()), str(uuid4())
    with transaction() as c:
        c.execute('insert into tenants(id,name) values(%s,%s)', (tenant_id, body.name))
        user_id = create_admin(c, tenant_id, body)
        c.execute('insert into projects(id,tenant_id,name,account_id) values(%s,%s,%s,%s)',
                   (project_id, tenant_id, body.project_name, body.account_id.strip()))
        audit(c, tenant_id, 'ops_tenant_created', {'user_id': user_id, 'project_id': project_id})
    return {'id': tenant_id, 'user_id': user_id, 'project_id': project_id}


@router.post('/tenants/{tenant_id}/admins', status_code=201)
def add_admin(tenant_id: str, body: AdminAccount, _=Depends(authenticate_platform)):
    with transaction() as c:
        require_tenant(c, tenant_id)
        user_id = create_admin(c, tenant_id, body)
        audit(c, tenant_id, 'ops_admin_created', {'user_id': user_id})
    return {'id': user_id}


@router.patch('/tenants/{tenant_id}')
def rename_tenant(tenant_id: str, body: RenameTenant, _=Depends(authenticate_platform)):
    with transaction() as c:
        require_tenant(c, tenant_id)
        c.execute('update tenants set name=%s where id=%s', (body.name, tenant_id))
        audit(c, tenant_id, 'ops_tenant_renamed', {'name': body.name})
    return {'ok': True}


@router.delete('/tenants/{tenant_id}')
def delete_tenant(tenant_id: str, _=Depends(authenticate_platform)):
    from .project_cleanup import delete_project_data
    with transaction() as c:
        require_tenant(c, tenant_id)
        projects = c.execute('select id,tenant_id,name from projects where tenant_id=%s order by id for update', (tenant_id,)).fetchall()
        for project in projects:
            delete_project_data(c, project)
        users = c.execute('select user_id from memberships where tenant_id=%s for update', (tenant_id,)).fetchall()
        c.execute('delete from memberships where tenant_id=%s', (tenant_id,))
        for user in users:
            c.execute('delete from users where id=%s and not exists (select 1 from memberships where user_id=%s)', (user['user_id'], user['user_id']))
        for table in ('connections', 'audit'):
            c.execute('delete from '+table+' where tenant_id=%s', (tenant_id,))
        c.execute('delete from tenants where id=%s', (tenant_id,))
    return {'deleted': True}


@router.post('/tenants/{tenant_id}/admins/{user_id}/status')
def update_admin(tenant_id: str, user_id: str, body: UpdateAdmin, _=Depends(authenticate_platform)):
    with transaction() as c:
        user = require_admin(c, tenant_id, user_id)
        if user['active'] and not body.active:
            remaining = c.execute("select count(*) as total from memberships m join users u on u.id=m.user_id where m.tenant_id=%s and m.role='admin' and u.active=true and u.id<>%s", (tenant_id, user_id)).fetchone()
            if not remaining['total']: raise HTTPException(409, '请先添加其他启用的管理员，不能停用最后一名管理员')
        c.execute('update users set active=%s where id=%s', (body.active, user_id))
        audit(c, tenant_id, 'ops_admin_status_changed', {'user_id': user_id, 'active': body.active})
    return {'ok': True}


@router.post('/tenants/{tenant_id}/admins/{user_id}/password')
def reset_password(tenant_id: str, user_id: str, body: ResetPassword, _=Depends(authenticate_platform)):
    with transaction() as c:
        require_admin(c, tenant_id, user_id)
        c.execute('update users set password_hash=%s where id=%s', (password_hash.hash(body.password), user_id))
        audit(c, tenant_id, 'ops_admin_password_reset', {'user_id': user_id})
    return {'ok': True}
