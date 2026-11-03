"""Private launcher; never emits environment values or changes production state."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

parser=argparse.ArgumentParser()
parser.add_argument('--release',required=True)
parser.add_argument('--output',required=True)
parser.add_argument('--stage',choices=['all','tests','workflow'],default='all')
parser.add_argument('--repeats',type=int,choices=[1,2,3],default=1)
args=parser.parse_args()
release=Path(args.release).resolve()
output=Path(args.output).resolve()
assert release.is_relative_to('/opt/adbuild-agent/releases') and release.is_dir()
assert output.is_relative_to('/opt/adbuild-agent/qa')
load_dotenv('/opt/adbuild-agent/qa/robustness/.env',override=True)
assert (make_url(os.environ['DATABASE_URL']).database or '').endswith('_test')
assert os.getenv('EXECUTION_ENABLED','false').lower() in ('false','0')
os.environ['EXECUTION_ENABLED']='false'
os.environ['TEST_DATABASE_URL']=os.environ['DATABASE_URL']
os.environ['OPS_ROLLBACK_TEST']='1'
os.environ['PYTHONPATH']=str(release/'backend')
os.environ['PYTHONIOENCODING']='utf-8'
os.environ['LANGSMITH_TRACING']='false'
os.umask(0o077)
if args.stage in ('all','tests'):
    subprocess.run([sys.executable,'-m','pytest','tests','-q'],cwd=release/'backend',env=os.environ,check=True)
if args.stage in ('all','workflow'):
    subprocess.run([sys.executable,'evals/run_workflow.py','--output',str(output),'--repeats',str(args.repeats)],cwd=release/'backend',env=os.environ,check=True)
