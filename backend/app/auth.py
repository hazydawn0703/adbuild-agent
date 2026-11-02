from dataclasses import dataclass
import httpx
from fastapi import Header, HTTPException
from .db import transaction
from .settings import settings


@dataclass
class Actor:
    user_id: str


async def authenticate(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401,"请先登录")
    async with httpx.AsyncClient(timeout=15) as client:
        result = await client.get(settings().supabase_url+"/auth/v1/user",headers={"apikey":settings().supabase_service_key,"Authorization":authorization})
    if result.status_code != 200:
        raise HTTPException(401,"登录已失效")
    return Actor(result.json()["id"])


def project_access(conn, project_id, actor, write=False, admin=False):
    row = conn.execute("select p.*,m.role from projects p join memberships m on m.tenant_id=p.tenant_id where p.id=%s and m.user_id=%s",(project_id,actor.user_id)).fetchone()
    if not row:
        raise HTTPException(404,"项目不存在")
    if admin and row["role"] != "admin" or write and row["role"] == "viewer":
        raise HTTPException(403,"没有操作权限")
    return row
