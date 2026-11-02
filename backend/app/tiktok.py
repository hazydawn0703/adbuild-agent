"""Official MCP transport and encrypted per-tenant OAuth storage."""
import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import httpx
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata, OAuthToken, OAuthClientInformationFull
from .settings import settings
from .db import transaction, json_value


class EncryptedStorage:
    def __init__(self, tenant_id):
        self.tenant_id=tenant_id
        self.cipher=Fernet(settings().token_encryption_key.encode())

    def read(self):
        with transaction() as c:
            row=c.execute("select state from connections where tenant_id=%s",(self.tenant_id,)).fetchone()
        return row['state'] if row else {}

    def update(self, **values):
        with transaction() as c:
            c.execute("insert into connections(tenant_id,state) values(%s,%s) on conflict(tenant_id) do update set state=connections.state || excluded.state",(self.tenant_id,json_value(values)))

    async def get_tokens(self):
        value=self.read().get('tokens')
        return OAuthToken.model_validate_json(self.cipher.decrypt(value.encode())) if value else None

    async def set_tokens(self, tokens):
        self.update(tokens=self.cipher.encrypt(tokens.model_dump_json().encode()).decode())

    async def get_client_info(self):
        value=self.read().get('client')
        return OAuthClientInformationFull.model_validate_json(self.cipher.decrypt(value.encode())) if value else None

    async def set_client_info(self, client_info):
        self.update(client=self.cipher.encrypt(client_info.model_dump_json().encode()).decode())


def provider(tenant_id, interactive=False):
    store=EncryptedStorage(tenant_id)
    async def redirect(url):
        if not interactive:
            raise ValueError("TikTok 授权失效，请管理员重新连接")
        store.update(authorization_url=url,oauth_state=parse_qs(urlparse(url).query).get('state',[''])[0],code=None,status='authorizing')
    async def callback():
        for _ in range(150):
            state=store.read()
            if state.get('code'):
                store.update(code=None)
                return state['code'],state.get('oauth_state')
            await asyncio.sleep(2)
        raise TimeoutError("TikTok 授权等待超时，请重新连接")
    return OAuthClientProvider(server_url=settings().tiktok_mcp_url,
        client_metadata=OAuthClientMetadata(client_name="Adbuild",redirect_uris=[settings().api_origin+"/oauth/tiktok/callback"],
            grant_types=['authorization_code','refresh_token'],response_types=['code'],token_endpoint_auth_method='none'),
        storage=store,redirect_handler=redirect,callback_handler=callback)


@asynccontextmanager
async def session(tenant_id,interactive=False):
    async with httpx.AsyncClient(auth=provider(tenant_id,interactive),timeout=60) as client:
        async with streamable_http_client(settings().tiktok_mcp_url,http_client=client) as (read,write,_):
            async with ClientSession(read,write) as s:
                await s.initialize()
                yield s


async def connect(tenant_id):
    try:
        async with session(tenant_id,True) as s:
            tools=[]
            cursor=None
            while True:
                page=await s.list_tools(cursor=cursor)
                tools.extend([t.model_dump(mode='json') for t in page.tools])
                cursor=page.nextCursor
                if not cursor: break
        with transaction() as c:
            c.execute("update connections set tools=%s,verified=false where tenant_id=%s",(json_value(tools),tenant_id))
        EncryptedStorage(tenant_id).update(status='connected',authorization_url=None)
    except Exception:
        # Do not persist OAuth HTTP exceptions which can contain secrets.
        EncryptedStorage(tenant_id).update(status='failed',authorization_url=None)


def result_data(result):
    if result.isError:
        raise ValueError("TikTok 工具返回错误，请检查平台授权与配置")
    if result.structuredContent:
        data=result.structuredContent
    else:
        texts=[c.text for c in result.content if c.type=='text']
        data=json.loads(''.join(texts))
    if isinstance(data,dict) and data.get('code',0)!=0:
        raise ValueError("TikTok 业务接口拒绝请求")
    return data.get('data',data) if isinstance(data,dict) else data
