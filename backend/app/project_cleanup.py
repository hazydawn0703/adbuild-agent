"""Transactional project deletion shared by customer and platform operations."""
from datetime import datetime, timezone
from fastapi import HTTPException
from .db import json_value

def delete_project_data(c, p):
    project_id = p['id']
    tasks=c.execute('select data from intake_tasks where project_id=%s for update',(project_id,)).fetchall()
    if any(t['data'].get('lease') and datetime.fromisoformat(t['data']['lease']['until'])>datetime.now(timezone.utc) for t in tasks):
        raise HTTPException(409,'项目中有正在分析的任务，请稍后重试')
    if c.execute("select id from runs where project_id=%s and status not in ('complete','paused','cancelled') for update",(project_id,)).fetchone():
        raise HTTPException(409,'请先处理未结束的执行批次')
    if c.execute("select j.id from jobs j join runs r on JSON_UNQUOTE(JSON_EXTRACT(j.message,'$.run_id'))=r.id where r.project_id=%s and j.status!='complete' for update",(project_id,)).fetchone():
        raise HTTPException(409,'项目中有待处理的后台作业')
    files=c.execute('select storage_path from files where project_id=%s',(project_id,)).fetchall()
    threads=[f"{p['tenant_id']}:{t['data']['id']}"+suffix for t in tasks
         for suffix in ['',*[':'+r for r in t['data'].get('requests',{})]]]
    c.execute('insert into jobs(message) values(%s)',(json_value({'storage_paths':[f['storage_path'] for f in files],'checkpoint_threads':threads}),))
    c.execute("delete j from jobs j join runs r on JSON_UNQUOTE(JSON_EXTRACT(j.message,'$.run_id'))=r.id where r.project_id=%s",(project_id,))
    for table in ('deployments','runs','intake_tasks','drafts','files','history','messages','mapping_templates','project_contexts'):
        c.execute('delete from '+table+' where project_id=%s',(project_id,))
    c.execute('update audit set project_id=null where project_id=%s',(project_id,))
    c.execute('delete from projects where id=%s',(project_id,))

