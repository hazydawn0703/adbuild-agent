from contextlib import contextmanager
from types import SimpleNamespace
import asyncio
import pytest
from app import usage

@pytest.fixture
def records(monkeypatch):
    saved=[]
    class Connection:
        def execute(self,sql,params=()):
            if sql.startswith('insert'): saved.append(__import__('json').loads(params[-1]))
            return self
        def fetchone(self): return {'tenant_id':'tenant'}
    @contextmanager
    def transaction(): yield Connection()
    monkeypatch.setattr(usage,'transaction',transaction)
    return saved

def test_tokens_and_retry(records):
    with usage.stage('task','project','mapping'):
        usage.record_attempt()
        usage.record_usage(SimpleNamespace(usage_metadata={'input_tokens':100,'output_tokens':20,'input_token_details':{'cache_read':70}}))
        usage.record_attempt()
    assert (records[0]['uncached_input'],records[0]['cached_input'],records[0]['output'])==(30,70,20)
    assert (records[0]['calls'],records[0]['reported_calls'])==(2,1)
    assert usage.current.get() is None

def test_failure_and_context(records):
    async def fail(): raise ValueError('test')
    with pytest.raises(ValueError):
        with usage.task_context('task','project'):
            asyncio.run(usage.measured('validate',fail)())
    assert records[0]['task_id']=='task'
    assert records[0]['status']=='failed'
    assert records[0]['duration_ms']>=0
    assert usage.current.get() is None
