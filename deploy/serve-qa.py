"""Serve an isolated QA database for real browser verification. Never serves production."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

parser=argparse.ArgumentParser()
parser.add_argument('--release',required=True)
parser.add_argument('--workflow',required=True)
parser.add_argument('--login-file',required=True)
parser.add_argument('--port',type=int,default=18792)
args=parser.parse_args()
release=Path(args.release).resolve()
workflow=Path(args.workflow).resolve()
login_file=Path(args.login_file).resolve()
assert release.is_relative_to('/opt/adbuild-agent/releases') and release.is_dir()
assert workflow.is_relative_to('/opt/adbuild-agent/qa') and workflow.is_dir()
assert login_file.is_relative_to('/opt/adbuild-agent/qa') and not login_file.exists()
assert 18000<=args.port<=18999
load_dotenv('/opt/adbuild-agent/qa/robustness/.env',override=True)
assert (make_url(os.environ['DATABASE_URL']).database or '').endswith('_test')
assert os.getenv('EXECUTION_ENABLED','false').lower() in ('false','0')
os.environ['EXECUTION_ENABLED']='false'
os.environ['STORAGE_DIRECTORY']=str(workflow/'workbooks')
os.umask(0o077)
sys.path.insert(0,str(release/'backend'))
from app.settings import settings
from app.db import transaction
from app.auth import password_hash
assert not settings().execution_enabled
results=json.loads((workflow/'results.json').read_text())
case=next(c for c in results['cases'] if c['complete'])
tenant=results['tenant_id']
user=str(uuid4());email=user+'@browser.test.invalid';password=secrets.token_urlsafe(32)
with transaction() as connection:
    project=connection.execute('select id,name from projects where tenant_id=%s and name=%s',(tenant,case['case'])).fetchone()
    assert project,'Successful workflow project is required'
    task=connection.execute('select data from intake_tasks where project_id=%s order by created_at desc limit 1',(project['id'],)).fetchone()
    assert task
    connection.execute('insert into users(id,email,password_hash) values(%s,%s,%s)',(user,email,password_hash.hash(password)))
    connection.execute('insert into memberships values(%s,%s,%s)',(tenant,user,'admin'))
with login_file.open('x') as stream:
    json.dump({'email':email,'password':password,'project_name':project['name'],'task_name':task['data']['name']},stream)
login_file.chmod(0o600)
from app.main import app
import uvicorn
print('Isolated QA API ready; advertising disabled',flush=True)
uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False)
