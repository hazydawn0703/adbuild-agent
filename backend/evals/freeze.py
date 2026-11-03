"""Write a reproducibility manifest before accepting the user's withheld dataset."""
import argparse,json,hashlib,importlib.metadata
from pathlib import Path
from datetime import datetime,timezone
from app.prompts import PROMPT_HASH
from app.settings import settings

parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
parser.add_argument('--commit',required=True)
parser.add_argument('--evidence',action='append',default=[],help='Sealed private run directory; never sent to a model')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
paths=sorted(str(p.relative_to(root)).replace('\\','/') for directory in ('backend','frontend','deploy','docs','scripts') for p in (root/directory).rglob('*') if p.is_file() and not any(x in p.parts for x in ('node_modules','.next','__pycache__','.pytest_cache','.venv','test-results','playwright-report')) and p.suffix not in ('.pyc','.tsbuildinfo') and p.name not in ('.env',))
paths += [p for p in ('README.md','.gitignore') if (root/p).is_file()]
evidence=[]
observed_models=set()
for directory in args.evidence:
    folder=Path(directory).resolve()
    seal=json.loads((folder/'seal.json').read_text(encoding='utf-8'))
    for name,digest in seal['files'].items():
        file=(folder/name).resolve()
        assert file.is_relative_to(folder) and hashlib.sha256(file.read_bytes()).hexdigest()==digest, 'Evidence seal mismatch'
    evidence.append({'run':folder.name,'seal_sha256':hashlib.sha256((folder/'seal.json').read_bytes()).hexdigest(),
                     'protocol_sha256':hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest()})
    events=folder/'model-events.jsonl'
    if events.is_file():
        for line in events.read_text(encoding='utf-8').splitlines():
            event=json.loads(line)
            if event.get('event')=='model_end':
                metadata=event['payload']['response'].get('llm_output') or {}
                if metadata.get('model_name'):
                    observed_models.add((metadata['model_name'],metadata.get('system_fingerprint','')))
manifest={'created_at':datetime.now(timezone.utc).isoformat(),'commit':args.commit,'prompt_hash':PROMPT_HASH,
    'model':settings().llm_model,'thinking':'disabled','method':'function_calling','model_timeout_seconds':60,
    'round_timeout_seconds':180,'schema_attempts':2,'region_read_rounds':2,'execution_enabled':settings().execution_enabled,
    'files':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},
    'dependency_versions':{p:importlib.metadata.version(p) for p in ('openpyxl','langgraph','langchain-openai','fastapi','PyMySQL')},
    'observed_models':[{'model_name':name,'system_fingerprint':fingerprint} for name,fingerprint in sorted(observed_models)],
    'sealed_evidence':evidence,'sdk_model_retries':0,
    'evaluation_label':'post_holdout_regression','exposed_dataset':'94878ec917fa67aac6373c742dede79d452e0d97f84f80512735d68d6dc2b737','new_independent_validation_set_received':False,'limitation':'Provider model alias may change underlying snapshot'}
assert not manifest['execution_enabled']
with Path(args.output).open('x',encoding='utf-8') as stream:
    json.dump(manifest,stream,ensure_ascii=False,indent=2)
print('Complete release freeze saved; exposed cases are regression only')
