"""Task stage measurements persisted in the existing audit ledger."""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from time import monotonic
from functools import wraps
import inspect
from .db import transaction, json_value

current = ContextVar('task_usage', default=None)

@contextmanager
def stage(task_id, project_id, name):
    record = {'task_id':task_id,'project_id':project_id,'stage':name,'started_at':datetime.now(timezone.utc).isoformat(),
              'uncached_input':0,'cached_input':0,'output':0,'calls':0,'reported_calls':0,'status':'complete'}
    token = current.set(record)
    started = monotonic()
    try:
        yield
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        current.reset(token)
        record['duration_ms'] = round((monotonic()-started)*1000)
        with transaction() as c:
            project = c.execute('select tenant_id from projects where id=%s',(project_id,)).fetchone()
            if project:
                c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,'task_stage_usage',%s)",
                          (project['tenant_id'],project_id,json_value(record)))

def record_usage(raw):
    record = current.get()
    if record is None: return
    usage = getattr(raw,'usage_metadata',None)
    if not usage: return
    record['reported_calls'] += 1
    cached = usage.get('input_token_details',{}).get('cache_read',0)
    record['cached_input'] += cached
    record['uncached_input'] += max(0,usage['input_tokens']-cached)
    record['output'] += usage['output_tokens']

def measured(name, fn):
    def scope():
        parent = current.get()
        return stage(parent['task_id'],parent['project_id'],name) if parent else None
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def wrapped(*args,**kwargs):
            ctx=scope()
            if ctx is None: return await fn(*args,**kwargs)
            with ctx: return await fn(*args,**kwargs)
    else:
        @wraps(fn)
        def wrapped(*args,**kwargs):
            ctx=scope()
            if ctx is None: return fn(*args,**kwargs)
            with ctx: return fn(*args,**kwargs)
    return wrapped

@contextmanager
def task_context(task_id, project_id):
    token=current.set({'task_id':task_id,'project_id':project_id})
    try: yield
    finally: current.reset(token)


def record_attempt():
    record=current.get()
    if record is not None: record['calls'] += 1
