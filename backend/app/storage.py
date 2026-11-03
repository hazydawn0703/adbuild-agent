import asyncio
from pathlib import Path
from .settings import settings

def target(path):
    root=Path(settings().storage_directory).resolve()
    result=(root/path).resolve()
    if not result.is_relative_to(root): raise ValueError("非法文件路径")
    return result

async def upload(path,data):
    dest=target(path)
    dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    def write():
        with dest.open('xb') as stream: stream.write(data)
        dest.chmod(0o600)
    await asyncio.to_thread(write)

async def download(path):
    return await asyncio.to_thread(target(path).read_bytes)
