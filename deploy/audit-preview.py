"""Read-only deployment evidence; never prints credentials or business file contents."""
import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('/etc/adbuild-agent/runtime.env', override=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.settings import settings
from app.db import transaction

assert not settings().execution_enabled, 'Real advertising must remain disabled'
with transaction() as connection:
    counts = {table: connection.execute('select count(*) n from ' + table).fetchone()['n']
              for table in ('projects', 'drafts', 'intake_tasks', 'runs', 'deployments', 'jobs')}
    counts['advertising_jobs'] = connection.execute("select count(*) n from jobs where not JSON_CONTAINS_PATH(message,'one','$.storage_paths')").fetchone()['n']
    counts['cleanup_jobs'] = connection.execute("select count(*) n from jobs where JSON_CONTAINS_PATH(message,'one','$.storage_paths')").fetchone()['n']
    projects = connection.execute('select id,reserved from projects order by id').fetchall()
    reserved = str(sum(row['reserved'] for row in projects))
report = {'time': datetime.now(timezone.utc).isoformat(),
          'auditor_source': str(Path(__file__).resolve().parents[1]),
          'deployed_release': str(Path('/opt/adbuild-agent/current').resolve()),
          'deployed_commit': (Path('/opt/adbuild-agent/current')/'REVISION').read_text().strip(),
          'execution_enabled': settings().execution_enabled,
          'counts': counts, 'project_ids': [row['id'] for row in projects], 'reserved': reserved}
report['worker_role'] = 'cleanup_only'
for name, action in (('worker_active', 'is-active'), ('worker_enabled', 'is-enabled')):
    report[name] = subprocess.run(['systemctl', action, 'adbuild-agent-worker'], text=True,
                                  capture_output=True, check=False).stdout.strip()
assert report['worker_active'] == 'active' and report['worker_enabled'] == 'enabled'
print(json.dumps(report, ensure_ascii=False))
