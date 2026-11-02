import httpx
from .settings import settings


def headers():
    cfg=settings()
    return {"apikey":cfg.supabase_service_key,"Authorization":"Bearer "+cfg.supabase_service_key}


async def upload(path,data):
    async with httpx.AsyncClient(timeout=60) as client:
        result = await client.post(settings().supabase_url+"/storage/v1/object/workbooks/"+path,
            content=data,headers={**headers(),"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        result.raise_for_status()


async def download(path):
    async with httpx.AsyncClient(timeout=60) as client:
        result=await client.get(settings().supabase_url+"/storage/v1/object/workbooks/"+path,headers=headers())
        result.raise_for_status()
        return result.content
