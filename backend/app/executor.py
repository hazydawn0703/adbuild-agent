"""Fail-closed executor. Deployment bindings are verified against the live MCP schema.

Bindings translate canonical business operations to the authorized server's actual
tool names; they are installed by the platform during account integration, never
generated or edited by the planning model.
"""
from datetime import datetime, timezone
from jsonschema import validate
from .models import Requirement, ProjectPolicy
from .rules import evaluate, check_policy
from .db import transaction, json_value
from .tiktok import session, result_data

OPERATIONS=('find_existing','create_campaign','create_adgroup','create_ad','read_campaign','read_adgroup','read_ad','enable_ad','enable_adgroup','enable_campaign','pause_campaign')


def bind_value(value, context):
    if isinstance(value,str) and value.startswith('$'):
        return context[value[1:]]
    if isinstance(value,dict): return {k:bind_value(v,context) for k,v in value.items()}
    if isinstance(value,list): return [bind_value(v,context) for v in value]
    return value


def extract(value,path):
    for key in path.split('.') if path else []:
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


async def call(s,bindings,tools,op,context):
    binding=bindings[op]
    return await call_binding(s,binding,tools,context)


async def call_binding(s,binding,tools,context):
    tool=next(t for t in tools if t['name']==binding['tool'])
    args=bind_value(binding['arguments'],context)
    validate(args,tool['inputSchema'])
    # SDK timeout exceptions must be reconciled before any future write.
    return result_data(await s.call_tool(binding['tool'],args))


def assertions(result,binding,context):
    for assertion in binding.get('assertions',[]):
        expected=bind_value(assertion['equals'],context)
        if extract(result,assertion['path']) != expected:
            raise ValueError('后台配置回读校验不一致：'+assertion['field'])


def validate_bindings(bindings,tools):
    if set(OPERATIONS)-set(bindings) or not bindings.get('preflight'):
        raise ValueError('缺少必要 TikTok 工具绑定')
    catalog={t['name']:t for t in tools}
    for binding in [*(bindings[o] for o in OPERATIONS),*bindings['preflight']]:
        if binding['tool'] not in catalog or not isinstance(binding.get('arguments'),dict):
            raise ValueError('工具不存在或参数无效')
    # Every critical preflight fact must have a real response-path assertion.
    fields={a['field'] for b in bindings['preflight'] for a in b.get('assertions',[])}
    if not {'account_id','currency','timezone','identity_id','post_id','audience','authorized','supported'} <= fields:
        raise ValueError('前置校验未覆盖全部关键事实')
    required={'read_campaign':{'account_id','budget','budget_mode'},'read_adgroup':{'start_at','end_at','audience','objective'},'read_ad':{'post_id','identity_id'}}
    for op,fields in required.items():
        if not fields <= {a['field'] for a in bindings[op].get('assertions',[])}:
            raise ValueError(op+' 回读校验不完整')
    def leaves(value):
        if isinstance(value,dict): return [leaf for v in value.values() for leaf in leaves(v)]
        if isinstance(value,list): return [leaf for v in value for leaf in leaves(v)]
        return [value]
    for op in ('create_campaign','create_adgroup','create_ad'):
        if not any(v in ('DISABLE','$operation_status') for v in leaves(bindings[op]['arguments'])):
            raise ValueError(op+' 必须显式设置暂停状态')


def gate(c,run_id):
    run=c.execute("select * from runs where id=%s",(run_id,)).fetchone()
    project=c.execute("select * from projects where id=%s",(run['project_id'],)).fetchone()
    if run['cancelled'] or project['policy_version']!=run['policy_version']:
        raise ValueError("任务已取消或项目预授权已变化")
    policy=ProjectPolicy.model_validate(project['policy'])
    if not policy.enabled or policy.valid_until <= datetime.now(timezone.utc):
        raise ValueError("项目预授权已撤销或过期")
    return run,project,policy


async def execute(run_id,action='resume'):
    # A dedicated advisory-lock connection serializes deliveries, including visibility timeout redelivery.
    from .db import transaction
    with transaction() as lock:
        acquired=lock.execute("select GET_LOCK(%s,0) locked",(run_id,)).fetchone()['locked']
        if not acquired: return False
        try:
            return await _execute(run_id,action)
        finally:
            lock.execute("select RELEASE_LOCK(%s)",(run_id,))


async def _execute(run_id,action):
    with transaction() as c:
        run=c.execute("select * from runs where id=%s",(run_id,)).fetchone()
        connection=c.execute("select * from connections where tenant_id=%s",(run['tenant_id'],)).fetchone()
        deployments=c.execute("select * from deployments where run_id=%s",(run_id,)).fetchall()
    if not connection or not connection['verified']:
        raise ValueError("TikTok 工具绑定尚未通过平台接入验证")
    bindings,tools=connection['bindings'],connection['tools']
    async with session(run['tenant_id']) as s:
        for dep in deployments:
            req=Requirement.model_validate(dep['requirement'])
            steps=dep['steps']
            context={**req.model_dump(mode='json'),'request_id':dep['fingerprint'],'operation_status':'DISABLE','budget':float(req.budget)}
            context.update({k:v['id'] for k,v in steps.items() if isinstance(v,dict) and 'id' in v})
            if action=='pause':
                if context.get('campaign_id'):
                    await call(s,bindings,tools,'pause_campaign',context)
                continue
            if dep['status'] in ('complete','existing'): continue
            if dep['status']=='uncertain' or any(v.get('state')=='started' for v in steps.values()):
                await reconcile(s,connection,run_id,dep,context)
                continue
            with transaction() as c:
                _,_,policy=gate(c,run_id)
                if check_policy(evaluate(req,[]),policy): raise ValueError("执行前需求或规则校验未通过")
            validate_bindings(bindings,tools)
            for probe in bindings['preflight']:
                assertions(await call_binding(s,probe,tools,context),probe,context)
            existing=await call(s,bindings,tools,'find_existing',context)
            if extract(existing,bindings['find_existing']['result_path']):
                with transaction() as c:
                    c.execute("update deployments set status='existing' where project_id=%s and fingerprint=%s",(dep['project_id'],dep['fingerprint']))
                continue
            for op,id_key in [('create_campaign','campaign_id'),('create_adgroup','adgroup_id'),('create_ad','ad_id'),('read_campaign',None),('read_adgroup',None),('read_ad',None),('enable_ad',None),('enable_adgroup',None),('enable_campaign',None)]:
                if steps.get(op,{}).get('state')=='done': continue
                write=op.startswith(('create','enable'))
                with transaction() as c:
                    gate(c,run_id)
                    if write:
                        steps[op]={'state':'started'}
                        c.execute("update deployments set status='executing',steps=%s where project_id=%s and fingerprint=%s",(json_value(steps),dep['project_id'],dep['fingerprint']))
                try:
                    result=await call(s,bindings,tools,op,context)
                    if id_key:
                        context[id_key]=str(extract(result,bindings[op]['result_path']))
                    if op.startswith('read'):
                        assertions(result,bindings[op],context)
                    steps[op]={'state':'done',**({'id':context[id_key]} if id_key else {})}
                    if id_key: steps[id_key]={'id':context[id_key]}
                except Exception:
                    with transaction() as c:
                        c.execute("update deployments set status=%s where project_id=%s and fingerprint=%s",('uncertain' if write else 'attention',dep['project_id'],dep['fingerprint']))
                    raise ValueError("写操作结果待核实，禁止重复创建" if write else "后台回读失败，已保留暂停结构") from None
                with transaction() as c:
                    c.execute("update deployments set steps=%s where project_id=%s and fingerprint=%s",(json_value(steps),dep['project_id'],dep['fingerprint']))
            with transaction() as c:
                c.execute("update deployments set status='complete' where project_id=%s and fingerprint=%s",(dep['project_id'],dep['fingerprint']))
    with transaction() as c:
        statuses=[r['status'] for r in c.execute("select status from deployments where run_id=%s",(run_id,))]
        status='complete' if all(x in ('complete','existing') for x in statuses) else 'attention'
        c.execute("update runs set status=%s,error=null where id=%s",('paused' if action=='pause' else status,run_id))
    return True


async def reconcile(s,connection,run_id,dep,context):
    # A read is allowed; never treat an empty/ambiguous lookup as permission to replay a write.
    result=await call(s,connection['bindings'],connection['tools'],'find_existing',context)
    matches=extract(result,connection['bindings']['find_existing']['result_path'])
    with transaction() as c:
        c.execute("insert into audit(tenant_id,project_id,event,data) values(%s,%s,'reconciliation',%s)",(dep['tenant_id'],dep['project_id'],json_value({'run_id':run_id,'found':bool(matches)})))
    raise ValueError("已回查不确定写入，请平台核对远端 ID 后恢复；不会自动重放")
