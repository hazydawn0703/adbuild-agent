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
paths=['backend/app/prompts.py','backend/app/models.py','backend/app/agent.py','backend/app/excel.py','backend/app/rules.py','backend/app/evaluation.py','backend/requirements.lock','frontend/package-lock.json']
manifest={'created_at':datetime.now(timezone.utc).isoformat(),'commit':args.commit,'prompt_hash':PROMPT_HASH,
    'model':settings().llm_model,'thinking':'disabled','method':'function_calling','model_timeout_seconds':60,
    'round_timeout_seconds':180,'schema_attempts':2,'region_read_rounds':2,'execution_enabled':settings().execution_enabled,
    'files':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},
    'validation_set_received':False,'limitation':'Provider model alias may change underlying snapshot'}
assert not manifest['execution_enabled']
Path(args.output).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print('Freeze manifest saved; user validation dataset has not been received')
