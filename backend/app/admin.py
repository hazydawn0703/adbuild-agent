"""Platform operations. Run on the server, never expose service credentials to clients."""
import argparse
import asyncio
import json
from pathlib import Path
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from .settings import settings
from .db import transaction,json_value
from .executor import validate_bindings


async def initialize_checkpoints():
    async with AsyncPostgresSaver.from_conn_string(settings().database_url) as saver:
        await saver.setup()


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    provision=sub.add_parser('provision')
    provision.add_argument('--name',required=True)
    provision.add_argument('--user-id',required=True,help='Already invited Supabase Auth user UUID')
    provision.add_argument('--account-id',required=True)
    sub.add_parser('checkpoints')
    bind=sub.add_parser('bind-tools')
    bind.add_argument('--tenant-id',required=True)
    bind.add_argument('--file',required=True)
    args=parser.parse_args()
    if args.action=='checkpoints':
        asyncio.run(initialize_checkpoints());return
    with transaction() as c:
        if args.action=='provision':
            tenant=c.execute('insert into tenants(name) values(%s) returning id',(args.name,)).fetchone()['id']
            c.execute("insert into memberships(tenant_id,user_id,role) values(%s,%s,'admin')",(tenant,args.user_id))
            c.execute('insert into projects(tenant_id,name,account_id) values(%s,%s,%s)',(tenant,args.name,args.account_id))
            print('Created tenant',tenant)
        if args.action=='bind-tools':
            row=c.execute('select tools from connections where tenant_id=%s',(args.tenant_id,)).fetchone()
            if not row: raise ValueError('先完成 OAuth 连接')
            bindings=json.loads(Path(args.file).read_text(encoding='utf-8'))
            validate_bindings(bindings,row['tools'])
            c.execute('update connections set bindings=%s,verified=true where tenant_id=%s',(json_value(bindings),args.tenant_id))
            print('Bindings installed. Real-account acceptance testing is still required.')


if __name__=='__main__': main()
