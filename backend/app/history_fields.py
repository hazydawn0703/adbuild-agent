"""Choose a field snapshot within the actual group, never an arbitrary history row."""
from .provenance import history_reference,equivalent

PREFIX='沿用历史批次：'


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
