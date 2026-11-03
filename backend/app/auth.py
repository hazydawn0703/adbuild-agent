from dataclasses import dataclass
from pwdlib import PasswordHash
password_hash=PasswordHash.recommended()
from fastapi import Header, HTTPException
from .db import transaction
from .settings import settings


@dataclass
class Actor:
    user_id: str


async def authenticate(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "): raise HTTPException(401,"请先登录")
    from itsdangerous import URLSafeTimedSerializer, BadSignature
    try:
        data=URLSafeTimedSerializer(settings().session_secret,salt="adbuild-session").loads(authorization[7:],max_age=86400)
    except BadSignature: raise HTTPException(401,"登录已失效") from None
    with transaction() as c:
        user=c.execute("select id from users where id=%s and active=true",(data['user_id'],)).fetchone()
    if not user: raise HTTPException(401,"账户不可用")
    return Actor(user['id'])


def project_access(conn, project_id, actor, write=False, admin=False):
    row = conn.execute("select p.*,m.role from projects p join memberships m on m.tenant_id=p.tenant_id where p.id=%s and m.user_id=%s",(project_id,actor.user_id)).fetchone()
    if not row:
        raise HTTPException(404,"项目不存在")
    if admin and row["role"] != "admin" or write and row["role"] == "viewer":
        raise HTTPException(403,"没有操作权限")
    return row
