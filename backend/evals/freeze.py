"""Write a reproducibility manifest before accepting the user's withheld dataset."""
import argparse,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from app.prompts import PROMPT_HASH
from app.settings import settings

parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
parser.add_argument('--commit',required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
paths=sorted(str(p.relative_to(root)).replace('\\','/') for directory in ('backend','frontend','deploy','docs','scripts') for p in (root/directory).rglob('*') if p.is_file() and not any(x in p.parts for x in ('node_modules','.next','__pycache__','.pytest_cache','.venv','test-results','playwright-report')) and p.suffix not in ('.pyc','.tsbuildinfo') and p.name not in ('.env',))
manifest={'created_at':datetime.now(timezone.utc).isoformat(),'commit':args.commit,'prompt_hash':PROMPT_HASH,
    'model':settings().llm_model,'thinking':'disabled','method':'function_calling','model_timeout_seconds':60,
    'round_timeout_seconds':180,'schema_attempts':2,'region_read_rounds':2,'execution_enabled':settings().execution_enabled,
    'files':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},
    'evaluation_label':'post_holdout_regression','exposed_dataset':'94878ec917fa67aac6373c742dede79d452e0d97f84f80512735d68d6dc2b737','new_independent_validation_set_received':False,'limitation':'Provider model alias may change underlying snapshot'}
assert not manifest['execution_enabled']
Path(args.output).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print('Complete release freeze saved; exposed cases are regression only')
