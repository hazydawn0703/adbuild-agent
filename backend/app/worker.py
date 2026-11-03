import asyncio
from .db import transaction
from .executor import execute

async def cleanup(data):
    from .storage import target
    from .settings import settings
    from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
    for path in data['storage_paths']: await asyncio.to_thread(target(path).unlink,missing_ok=True)
    async with AIOMySQLSaver.from_conn_string(settings().database_url.replace('mysql+pymysql://','mysql://')) as saver:
        for thread in data['checkpoint_threads']: await saver.adelete_thread(thread)


async def main():
    from .settings import settings
    enabled=settings().execution_enabled
    while True:
        with transaction() as c:
            job=c.execute("select * from jobs where (status='pending' or (status='processing' and lease_until < UTC_TIMESTAMP())) and (%s or JSON_CONTAINS_PATH(message,'one','$.storage_paths')) order by id limit 1 for update skip locked",(enabled,)).fetchone()
            if job: c.execute("update jobs set status='processing',lease_until=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 300 SECOND) where id=%s",(job['id'],))
        if not job:
            await asyncio.sleep(2);continue
        data=job['message']
        try:
            if 'storage_paths' in data:
                await cleanup(data)
            else: await execute(data['run_id'],data.get('action','resume'))
        except Exception as e:
            if 'storage_paths' in data:
                await asyncio.sleep(2)
                continue
            safe=str(e) if isinstance(e,ValueError) else '执行失败，请检查服务端配置与连接状态'
            with transaction() as c: c.execute("update runs set status='attention',error=%s where id=%s",(safe,data['run_id']))
        with transaction() as c: c.execute("update jobs set status='complete' where id=%s",(job['id'],))

if __name__=='__main__': asyncio.run(main())
