import asyncio
from .db import transaction

async def main():
    from .settings import settings
    if not settings().execution_enabled:
        return
    from .executor import execute
    while True:
        with transaction() as c:
            job=c.execute("select * from jobs where status='pending' or (status='processing' and lease_until < UTC_TIMESTAMP()) order by id limit 1 for update skip locked").fetchone()
            if job: c.execute("update jobs set status='processing',lease_until=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 300 SECOND) where id=%s",(job['id'],))
        if not job:
            await asyncio.sleep(2);continue
        data=job['message']
        try: await execute(data['run_id'],data.get('action','resume'))
        except Exception as e:
            safe=str(e) if isinstance(e,ValueError) else '执行失败，请检查服务端配置与连接状态'
            with transaction() as c: c.execute("update runs set status='attention',error=%s where id=%s",(safe,data['run_id']))
        with transaction() as c: c.execute("update jobs set status='complete' where id=%s",(job['id'],))

if __name__=='__main__': asyncio.run(main())
