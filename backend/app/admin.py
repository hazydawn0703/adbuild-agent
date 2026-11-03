"""Platform operations. Run on the server, never expose service credentials to clients."""
import argparse
import asyncio
import json
from pathlib import Path
from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
from .settings import settings
from .db import transaction,json_value
from .executor import validate_bindings


async def initialize_checkpoints():
    async with AIOMySQLSaver.from_conn_string(settings().database_url.replace("mysql+pymysql://","mysql://")) as saver:
        await saver.setup()


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    provision=sub.add_parser('provision')
    provision.add_argument('--name',required=True)
    provision.add_argument('--email',required=True)
    provision.add_argument('--password-file',required=True)
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
            from uuid import uuid4
            from .auth import password_hash
            user_id=str(uuid4())
            password=Path(args.password_file).read_text().strip()
            if len(password)<16: raise ValueError('Initial password must be at least 16 characters')
            c.execute('insert into users(id,email,password_hash) values(%s,%s,%s)',(user_id,args.email.lower(),password_hash.hash(password)))
            tenant=str(uuid4())
            c.execute('insert into tenants(id,name) values(%s,%s)',(tenant,args.name))
            c.execute("insert into memberships(tenant_id,user_id,role) values(%s,%s,'admin')",(tenant,user_id))
            c.execute('insert into projects(id,tenant_id,name,account_id) values(%s,%s,%s,%s)',(str(uuid4()),tenant,args.name,args.account_id))
            print('Created tenant',tenant)
        if args.action=='bind-tools':
            row=c.execute('select tools from connections where tenant_id=%s',(args.tenant_id,)).fetchone()
            if not row: raise ValueError('先完成 OAuth 连接')
            bindings=json.loads(Path(args.file).read_text(encoding='utf-8'))
            validate_bindings(bindings,row['tools'])
            c.execute('update connections set bindings=%s,verified=true where tenant_id=%s',(json_value(bindings),args.tenant_id))
            print('Bindings installed. Real-account acceptance testing is still required.')


if __name__=='__main__': main()
