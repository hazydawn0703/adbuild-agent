"""Choose a field snapshot within the actual group, never an arbitrary history row."""
from .provenance import history_reference,equivalent
import re

PREFIX='沿用历史批次：'


def overwrite_requested(state,row,field,value,batch):
    from .provenance import message_supports
    from .schedule_evidence import applicable_messages
    applicable={m.get('id') for m in applicable_messages(state,row)}
    labels={'budget':r'预算|budget','budget_mode':r'预算类型|预算口径|budget','audience':r'受众|audience',
            'start_at':r'排期|开始|start','end_at':r'排期|结束|end','language':r'语言|language','objective':r'目标|objective'}
    label=labels.get(field,re.escape(field))
    for message in reversed(state.get('messages',[])):
        if message.get('role')!='user':continue
        for answer in message.get('answers',[]):
            owners=answer.get('target_ids') or answer.get('requirement_ids') or []
            if answer.get('field')!=field or row.get('id') not in owners:continue
            return answer['answer']==PREFIX+batch or equivalent(field,value,answer['answer'],row.get('timezone',''))
        if 'answers' in message or message.get('id') not in applicable:continue
        for clause in reversed(re.split(r'[，,；;。\n]',str(message.get('content','')))):
            all_fields=bool(re.search(r'全部配置|所有配置|all settings',clause,re.I))
            if not all_fields and not re.search(label,clause,re.I):continue
            if message_supports(field,value,clause,row.get('timezone','')):return True
            return bool(re.search(r'历史|上次|上批|history',clause,re.I) and re.search(r'沿用|采纳|使用|reuse|adopt',clause,re.I) and not re.search(r'不沿用|不使用|不采纳|不要|禁止|不得|not\b',clause,re.I))
    return False


def candidates(req,group,field):
    posts={a.post_id for a in group.ads if a.included}
    ads={a.id for a in group.ads if a.included}
    return [h for h in req.history if h.get('target_id') in ads and h.get('post_id') in posts
            and h.get('account_id')==req.account_id and h.get(field) not in (None,'')]


def choices(req,group,field):
    found=candidates(req,group,field)
    result=[]
    for name in dict.fromkeys(h.get('name','') for h in found):
        if not name:continue
        try:select(req,group,field,PREFIX+name)
        except ValueError:continue
        result.append(PREFIX+name)
    return result


def select(req,group,field,selection):
    if not selection.startswith(PREFIX):raise ValueError('请按问题选择具体历史批次')
    name=selection[len(PREFIX):].strip()
    found=[h for h in candidates(req,group,field) if h.get('name')==name]
    posts={a.post_id for a in group.ads if a.included}
    if not found or {h['post_id'] for h in found}!=posts:raise ValueError('所选历史批次未完整覆盖当前广告组的素材，请补充配置')
    value=found[0][field]
    if any(not equivalent(field,value,h[field],group.timezone) for h in found):raise ValueError('所选历史批次在当前广告组内包含冲突值，请明确具体配置')
    ref=history_reference(found[0],field)
    if ref is None:raise ValueError('历史快照缺少可核实文件和原始行')
    return value,ref
