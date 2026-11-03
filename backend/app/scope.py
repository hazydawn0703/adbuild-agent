"""Task selection is a server-checked set, independent of column mappings and edits."""
import re
from urllib.parse import urlsplit


def as_dict(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def redact_private(value):
    """Only presence of a private material credential may enter model context."""
    sensitive=re.compile(r'^(?:authorization_code|access_token|refresh_token|api_key|client_secret|password)$',re.I)
    secrets=set()
    def collect(item):
        if isinstance(item,dict):
            for key,val in item.items():
                if sensitive.fullmatch(key) and isinstance(val,str) and val: secrets.add(val)
                collect(val)
        elif isinstance(item,list):
            for val in item:collect(val)
    collect(value)
    def sanitize(item):
        if isinstance(item,dict):
            result={}
            for key,val in item.items():
                if sensitive.fullmatch(key):
                    result['authorization_present' if key=='authorization_code' else key+'_present']=bool(val)
                else: result[key]=sanitize(val)
            return result
        if isinstance(item,list):return [sanitize(v) for v in item]
        if isinstance(item,str):
            for secret in sorted(secrets,key=len,reverse=True):item=item.replace(secret,'[私密凭证已隐藏]')
        return item
    return sanitize(value)


def validate_scope(scope, messages):
    scope = as_dict(scope)
    if not scope or scope.get('status') != 'resolved':
        return
    users = {m['id']: m for m in messages if m.get('role') == 'user' and m.get('id')}
    refs = scope.get('message_ids', [])
    if not refs or any(ref not in users for ref in refs):
        raise ValueError('需求范围必须引用当前任务的用户消息')
    text = '\n'.join(users[ref]['content'] for ref in refs)
    selectors = scope.get('selectors', [])
    if not selectors and not scope.get('all_records'):
        raise ValueError('需求范围尚未明确，不能默认纳入全部记录')
    if scope.get('all_records') and not re.search(r'全部|所有|整[批份张表]|all\s+(?:rows|records|items)', text, re.I):
        raise ValueError('全部纳入缺少用户明确依据')
    for group in (selectors, scope.get('exclude_selectors', [])):
        fields = [s['field'] for s in group]
        if len(fields) != len(set(fields)):
            raise ValueError('同一范围字段应合并候选值，不能使用冲突条件')
        for selector in group:
            if not selector['values']:
                raise ValueError('范围条件缺少候选值')
            for value in selector['values']:
                if selector['field']=='name' and any(s['field'] in ('business_no','post_id','requirement_id') for s in group):
                    if not re.search(r'(?:名称|计划名|标题|campaign\s*name|named)[^\n，,；;]{0,40}'+re.escape(value),text,re.I):
                        raise ValueError('已有业务身份条件时，额外名称筛选必须有明确名称依据，不能将负责人加入筛选')
                if selector['field'] == 'objective':
                    from .excel import objective
                    parts=re.split(r'[，,；;\n/]|和|以及|及|\band\b',text,flags=re.I)
                    if objective(value) is None or not any(objective(value)==objective(p) for p in parts):
                        raise ValueError('目标范围没有用户消息依据')
                elif selector['field'] == 'platform':
                    if value.lower() != 'tiktok' or not re.search(r'tik\s*tok|\bTT\b', text, re.I):
                        raise ValueError('平台范围没有用户消息依据')
                elif not re.search(r'(?<![0-9A-Za-z_])' + re.escape(value) + r'(?![0-9A-Za-z_])', text, re.I):
                    raise ValueError('范围候选值没有用户消息依据')


def matches(req, selector):
    field = selector['field']
    if field=='requirement_id':
        actual=str(req.id).strip().lower()
    elif field == 'platform':
        explicit=str(getattr(req,'platform','') or '').strip().casefold().replace(' ','')
        if explicit:
            actual='tiktok' if explicit in ('tiktok','tt') else explicit
        else:
            host = urlsplit(req.post_url).hostname or ''
            actual = 'tiktok' if host == 'tiktok.com' or host.endswith('.tiktok.com') else ''
    else:
        actual = str(getattr(req, field, '') or '').strip().lower()
    values = [str(v).strip().lower() for v in selector['values']]
    return actual in values


def select_requirements(requirements, scope, messages):
    """Retain candidate evidence but never include records from an unresolved scope."""
    validate_scope(scope, messages)
    scope = as_dict(scope) or {}
    resolved = scope.get('status') == 'resolved' and scope.get('action') in ('new', 'repost')
    result = []
    for original in requirements:
        req = original.model_copy(deep=True)
        selected = resolved and (scope.get('all_records', False) or bool(scope.get('selectors')))
        selected = selected and all(matches(req, s) for s in scope.get('selectors', []))
        selected = selected and not any(matches(req, s) for s in scope.get('exclude_selectors', []))
        req.included = bool(selected)
        result.append(req)
    return result


def compact_state(state):
    """Pass selected records and relevant history, retaining explicit coverage metadata."""
    draft = state.get('draft')
    rows = (draft or {}).get('requirements', [])
    selected = [r for r in rows if r.get('included', True)]
    # An explicitly mentioned unselected record remains editable without including all candidates.
    messages = state.get('messages', [])
    instructions='\n'.join(m.get('content','') for m in messages if m.get('role')=='user')
    mentioned = [r for r in rows if any(v and re.search(r'(?<![0-9A-Za-z_])'+re.escape(str(v))+r'(?![0-9A-Za-z_])',instructions) for v in (r.get('id'), r.get('business_no'), r.get('post_id')))]
    scope=state.get('scope') or {}
    from types import SimpleNamespace
    matching=[r for r in rows if scope.get('status')=='resolved' and scope.get('selectors') and all(matches(SimpleNamespace(**r),s) for s in scope['selectors'])]
    wanted = {r['id'] for r in selected + mentioned + matching}
    relevant = []
    for r in rows:
        if r['id'] not in wanted:
            continue
        row = {k: v for k, v in r.items() if k != 'history'}
        history = r.get('history', [])
        row['history'] = [{k: v for k, v in h.items() if k not in ('raw',)} for h in history[:8]]
        row['history_coverage'] = {'returned': min(8, len(history)), 'total': len(history), 'complete': len(history) <= 8}
        relevant.append(row)
    documents = []
    for d in state.get('documents', []):
        inspection = d.get('inspection') or {}
        documents.append({'id': d.get('id'), 'name': d.get('name'), 'mapping': d.get('mapping'),
                          'sheets': [{'sheet': s['sheet'], 'rows': s.get('rows'), 'cols': s.get('cols'),
                                      'regions': [{'range': r.get('range'), 'nonempty_rows': r.get('nonempty_rows'),
                                          'header_candidates':r.get('header_candidates',[])[:2],
                                          'sample':r.get('sample',[])[:2],
                                          'evidence_coverage':{'headers_returned':min(2,len(r.get('header_candidates',[]))), 'headers_total':len(r.get('header_candidates',[])),
                                                               'samples_returned':min(2,len(r.get('sample',[]))), 'samples_total':len(r.get('sample',[]))}}
                                                  for r in s.get('regions', [])]}
                                     for s in inspection.get('sheets', [])]})
    # Never silently omit user instructions. A long conversation is an explicit resumable limit.
    # Search the entire server-side candidate set before choosing the bounded discovery catalog.
    catalog_rows=[r for r in rows if r['id'] in wanted]
    catalog_ids={r['id'] for r in catalog_rows}
    catalog_rows.extend(r for r in rows if r['id'] not in catalog_ids)
    catalog=[{k:r.get(k) for k in ('id','business_no','post_id','objective','name','platform','included')} for r in catalog_rows[:max(100,len(wanted))]]
    payload = {'messages': messages, 'draft': {**draft, 'requirements': relevant} if draft else None,
               'context': state.get('context', {}), 'scope': state.get('scope'), 'documents': documents,
               'candidate_catalog':catalog,'catalog_coverage':{'returned':len(catalog),'total':len(rows),'complete':len(catalog)==len(rows),'scope_search_complete':True,'scope_matches':len(matching)},
               'coverage': {'candidate_records': len(rows), 'selected_records': len(selected),
                            'provided_records': len(relevant), 'excluded_records': len(rows) - len(relevant)}}
    history_records=state.get('history_records',[])
    payload['history_available']=len(history_records)
    if scope.get('action')=='history':
        from .history_query import lookup_history
        payload['history_query']=lookup_history(history_records,scope,rows)
    return redact_private(payload)
