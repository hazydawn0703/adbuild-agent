"""Release preflight; credentials never printed."""
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('/etc/adbuild-agent/runtime.env',override=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.settings import settings
from app.db import transaction
assert not settings().execution_enabled,'Real execution must remain disabled'
with transaction() as c:
    for table in ('intake_tasks','project_contexts'):
        c.execute('select * from '+table+' limit 0')
print('Release preflight passed; execution disabled')
