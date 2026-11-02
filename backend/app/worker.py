import asyncio
from .db import transaction
from .executor import execute


async def main():
    while True:
        with transaction() as c:
            message=c.execute("select * from pgmq.read('adbuild',300,1)").fetchone()
        if not message:
            await asyncio.sleep(2)
            continue
        data=message['message']
        try:
            await execute(data['run_id'],data.get('action','resume'))
        except Exception as e:
            # Application-level errors contain no raw HTTP request/response or token.
            safe=str(e) if isinstance(e,ValueError) else '执行失败，请检查服务端配置与连接状态'
            with transaction() as c:
                c.execute("update runs set status='attention',error=%s where id=%s",(safe,data['run_id']))
        with transaction() as c:
            c.execute("select pgmq.archive('adbuild',%s)",(message['msg_id'],))


if __name__=='__main__':
    asyncio.run(main())
