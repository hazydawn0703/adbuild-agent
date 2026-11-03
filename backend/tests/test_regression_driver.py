"""Preparation and sealing checks; these never invoke a model or database."""
import importlib.util
import json
from pathlib import Path
import pytest

driver_path=Path(__file__).parents[1]/'evals/run_regression.py'
spec=importlib.util.spec_from_file_location('exposed_regression_driver',driver_path)
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)


def inputs(root):
    root.mkdir();(root/'sample').mkdir()
    for name in ('brief.xlsx','export.xlsx','paste.txt'):(root/'sample'/name).write_bytes(b'input data')
    (root/'protocol.json').write_text(json.dumps({'cases':['sample']}),encoding='utf-8')
    (root/'setup-context.json').write_text(json.dumps({'sample':{'account_id':'7312345678901234567','source':'supplied account metadata'}}),encoding='utf-8')
    return root


def test_driver_accepts_only_private_input_allowlist_and_never_gold(tmp_path):
    root=inputs(tmp_path/'inputs')
    cases,contexts,hashes=driver.validate_inputs(root)
    assert cases==['sample'] and contexts['sample']['account_id']=='7312345678901234567'
    assert len(hashes)==5
    (root/'sample/expected.json').write_text('{}')
    with pytest.raises(ValueError,match='allowlist'):driver.validate_inputs(root)


def test_driver_refuses_unsafe_case_names_and_ambiguous_account_setup(tmp_path):
    root=inputs(tmp_path/'inputs')
    (root/'protocol.json').write_text(json.dumps({'cases':['../outside']}))
    with pytest.raises(ValueError,match='Unsafe'):driver.validate_inputs(root)
    (root/'protocol.json').write_text(json.dumps({'cases':['sample']}))
    (root/'setup-context.json').write_text(json.dumps({'sample':{'account_id':'operator name','source':'sheet'}}))
    with pytest.raises(ValueError,match='numeric'):driver.validate_inputs(root)


def test_driver_requires_complete_and_matching_source_freeze(tmp_path):
    root=tmp_path/'release';(root/'backend/app').mkdir(parents=True);(root/'backend/evals').mkdir()
    (root/'backend/app/main.py').write_text('app = None')
    (root/'backend/evals/run_regression.py').write_text('driver = None')
    manifest={'files':{p.relative_to(root).as_posix():driver.digest(p) for p in root.rglob('*.py')}}
    assert driver.verify_sources(root,manifest)==[]
    (root/'backend/app/main.py').write_text('app = changed')
    assert driver.verify_sources(root,manifest)==['backend/app/main.py']
    del manifest['files']['backend/evals/run_regression.py']
    with pytest.raises(ValueError,match='complete app'):driver.verify_sources(root,manifest)


def test_driver_output_is_new_append_only_and_sealed_with_private_redaction(tmp_path):
    root=tmp_path/'run';run=driver.Run(root,['private-key'])
    run.write('protocol.json',{'label':'regression'})
    with pytest.raises(FileExistsError):run.write('protocol.json',{'label':'overwritten'})
    run.event('model_error',{'message':'private-key'})
    assert 'private-key' not in (root/'raw/model-events.jsonl').read_text(encoding='utf-8')
    run.seal();seal=json.loads((root/'outputs-seal.json').read_text(encoding='utf-8'))
    assert set(seal['files'])=={'protocol.json','raw/model-events.jsonl'}
    assert not [p for p,h in seal['files'].items() if driver.digest(root/p)!=h]
    with pytest.raises(FileExistsError):driver.Run(root,[])
