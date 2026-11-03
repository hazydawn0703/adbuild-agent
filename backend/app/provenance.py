"""Validate that a proposal's value is supported, separately from human adoption."""
from decimal import Decimal,InvalidOperation
from datetime import datetime
import re
from .models import SourceRef
from .excel import normalize_value,objective,date_value

CRITICAL_FIELDS={'platform','name','account_id','post_id','post_url','budget','budget_mode','currency','start_at','end_at',
                 'timezone','audience','language','identity_id','cta','landing_url'}


def equivalent(field,left,right,tz=''):
    try:
        a=normalize_value(field,left,tz);b=normalize_value(field,right,tz)
        return a==b and a not in (None,'')
    except (ValueError,KeyError,TypeError,InvalidOperation):return False


def history_reference(h,field):
    if not h.get('source_file') or not h.get('source_sheet') or not h.get('source_row') or h.get(field) in (None,''):
        return None
    return SourceRef(kind='history',file_id=h['source_file'],sheet=h['source_sheet'],
                     cell=f"{field}:{h['source_row']}",raw_value=str(h[field]))


def _same_reference(a,b):
    ignored={'accepted_at','accepted_by'}
    return a.model_dump(exclude=ignored)==b.model_dump(exclude=ignored)


def match_history_reference(ref,field,value,history,tz=''):
    """The history rows here come from server-selected evidence, never model output."""
    ref=SourceRef.model_validate(ref)
    if ref.kind!='history':return None
    for item in history:
        expected=history_reference(item,field)
        if expected and _same_reference(ref,expected) and equivalent(field,value,item[field],tz):return item
    return None


def _budget_values(text):
    found=[]
    for clause in re.split(r'[，,；;\n]',text):
        anchor=re.search(r'预算|总额|合计(?:预算|金额)|\bbudget\b',clause,re.I)
        if not anchor:continue
        tail=clause[anchor.end():]
        assignment=list(re.finditer(r'改为|改成|设为|设置为|调至|调到|调整为|调整到|统一为|降到|升到|\bto\b|[:：=]',tail,re.I))
        if assignment:tail=tail[assignment[-1].end():]
        amount=re.search(r'(?<![0-9])([0-9]+(?:\.[0-9]+)?)(?![0-9])',tail[:35])
        if amount:found.append(Decimal(amount[1]))
    return found


def allocation_message(state,weights):
    from fractions import Fraction
    if not weights or any(w is None or not w.is_finite() or w<=0 for w in weights):return None
    expected=[Fraction(w)/sum(Fraction(v) for v in weights) for w in weights]
    for message in reversed(state.get('messages',[])):
        text=str(message.get('content',''))
        if message.get('role')!='user' or not re.search(r'分配|占比|比例|按表顺序|allocation',text,re.I):continue
        vectors=[re.split(r'\s*[:：]\s*',v) for v in re.findall(r'(?<![\d:：])\d+(?:\.\d+)?(?:\s*[:：]\s*\d+(?:\.\d+)?)+(?![\d:：])',text)]
        percentages=re.findall(r'(\d+(?:\.\d+)?)\s*%',text)
        if percentages:vectors.append(percentages)
        for vector in vectors:
            values=[Fraction(v) for v in vector]
            if len(values)==len(expected) and all(v>0 for v in values) and [v/sum(values) for v in values]==expected:return message
        # A newer allocation instruction cannot be replaced by an older one.
        return None
    return None


def message_supports(field,value,text,tz=''):
    """Accept explicit, equivalent inputs; unresolved interpretations need a question."""
    if value in (None,''):return False
    target=normalize_value(field,value,tz)
    if field=='budget':return target in _budget_values(text)
    if field=='objective':
        return any(objective(part)==target for part in re.split(r'[，,；;\n/]|和|以及|及|\band\b',text,flags=re.I))
    if field=='budget_mode':
        return bool(re.search(r'日预算|每日预算|(?<![A-Za-z0-9_])daily(?![A-Za-z0-9_])',text,re.I)) if target=='daily' else bool(re.search(r'总预算|终身预算|(?<![A-Za-z0-9_])(?:lifetime|total)(?![A-Za-z0-9_])',text,re.I))
    if field=='language':
        aliases={'en':r'英语|英文|(?<![A-Za-z0-9_])(?:english|en)(?![A-Za-z0-9_])','all':r'全部语言|不限语言|语言不限|(?<![A-Za-z0-9_])all(?![A-Za-z0-9_])'}
        return bool(re.search(aliases.get(target,re.escape(str(target))),text,re.I))
    if field in ('start_at','end_at'):
        # Relative dates require a separately resolved and confirmed absolute timestamp.
        timestamps=re.findall(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?',text)
        return any(equivalent(field,value,stamp,tz) for stamp in timestamps)
    if field=='timezone':
        zones=re.findall(r'(?:UTC|GMT)(?:[+-]\d{1,2}(?::\d{2})?)?|\b[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?\b',text,re.I)
        return any(equivalent(field,value,zone) for zone in zones)
    if field=='post_id':
        if re.search(r'/video/'+re.escape(str(target))+r'(?![0-9])',text):return True
        return bool(re.search(r'(?:帖子|视频|post|video)[^\n，,；;]{0,30}(?<![0-9])'+re.escape(str(target))+r'(?![0-9])',text,re.I))
    if field=='account_id':
        return bool(re.search(r'(?:账户|账号|advertiser|account)[^\n，,；;]{0,30}(?<![0-9])'+re.escape(str(target))+r'(?![0-9])',text,re.I))
    # URLs, audience names, CTA and identity codes must occur as complete values.
    return bool(re.search(r'(?<![0-9A-Za-z_])'+re.escape(str(target))+r'(?![0-9A-Za-z_])',text,re.I))


def _context_supports(state,row,field,value):
    context=state.get('context',{})
    if context.get('account_ids') and row.get('account_id') not in context['account_ids']:return False
    if context.get('objectives') and row.get('objective') not in context['objectives']:return False
    default=context.get('defaults',{}).get(field)
    return equivalent(field,value,default,row.get('timezone',''))


def supported_source(state,row,field,value,offered=None):
    """Return the verified provenance without converting a suggestion into user fact."""
    tz=row.get('timezone','');previous=row.get('sources',{}).get(field)
    previous=SourceRef.model_validate(previous) if previous else None
    if offered:
        ref=SourceRef.model_validate(offered)
        if ref.kind=='project' and field=='account_id' and equivalent(field,value,state.get('project_account_id')):
            return SourceRef(file_id='',sheet='',cell='',raw_value=str(value),kind='project')
        if ref.kind=='excel':
            document=next((d for d in state.get('documents',[]) if str(d['id'])==ref.file_id),None)
            if document:
                return verify_excel_source(document,field,value,ref,tz)
        if ref.kind=='history':
            if match_history_reference(ref,field,value,row.get('history',[]),tz):return ref
            raise ValueError(f'{field} 的历史来源或值不支持本提案')
        if previous and _same_reference(ref,previous) and equivalent(field,value,row.get(field),tz):return previous
        if ref.kind=='confirmed' and not ref.file_id and ref.message_id:
            message=next((m for m in state.get('messages',[]) if m.get('id')==ref.message_id and m.get('role')=='user'),None)
            if message and message_supports(field,value,str(message.get('content','')),tz):return ref.model_copy(update={'raw_value':str(value)})
        if ref.kind=='confirmed' and ref.file_id:
            raise ValueError(f'{field} 的消息来源必须kind=confirmed、file_id为空、message_id为真实用户消息ID；不要伪造文件ID')
        if ref.kind=='template' and not ref.file_id and ref.context_version==state.get('context',{}).get('version') and _context_supports(state,row,field,value):return ref.model_copy(update={'raw_value':str(value)})
        raise ValueError(f'{field} 的新来源尚未经过服务端核实')
    for message in reversed(state.get('messages',[])):
        if message.get('role')=='user' and message_supports(field,value,str(message.get('content','')),tz):
            return SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=str(value),message_id=message.get('id'))
        if message.get('role')=='user' and field=='budget' and _budget_values(str(message.get('content',''))):
            # A newer explicit budget correction supersedes an older amount.
            break
    if previous and equivalent(field,value,row.get(field),tz):return previous
    if _context_supports(state,row,field,value):
        return SourceRef(kind='template',file_id='',sheet='',cell='',raw_value=str(value),context_version=state.get('context',{}).get('version'))
    raise ValueError(f'{field} 缺少支持该值的用户输入、项目默认或字段证据，请先澄清')


def verify_excel_source(document,field,value,ref,tz=''):
    """Document metadata must come from the project-scoped server query."""
    from hashlib import sha256
    from .storage import target
    from .excel import read_workbook,sensitive_cells,_anchor,_transform,clean
    from datetime import date
    data=target(document['storage_path']).read_bytes()
    if sha256(data).hexdigest()!=document['sha256']:raise ValueError('字段来源文件指纹已变化')
    if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*',ref.cell):raise ValueError('字段来源单元格无效')
    wb=read_workbook(data)
    if ref.sheet not in wb.sheetnames:raise ValueError('字段来源工作表不存在')
    sheet=wb[ref.sheet];cell=_anchor(sheet,sheet[ref.cell])
    if cell.row>50000 or cell.column>300 or cell.coordinate in sensitive_cells(sheet):raise ValueError('字段来源不可使用')
    raw=cell.hyperlink.target if cell.hyperlink and field in ('post_url','landing_url') else cell.value
    if raw is None or cell.data_type=='e':raise ValueError('字段来源为空或为错误值')
    if field in ('account_id','post_id','identity_id') and isinstance(raw,(int,float)) and abs(raw)>=10**15:raise ValueError('长 ID 数值精度未核实')
    if field in ('start_at','end_at') and isinstance(raw,date) and (not isinstance(raw,datetime) or not re.search(r'[hHsS]',re.sub(r'"[^"]*"','',cell.number_format))):raise ValueError('日期来源缺少时刻精度')
    actual=_transform(raw,ref.transform)
    # The parser already derives these two fields from a verified TikTok URL.
    # Reapply the same controlled derivation to the server-read cell value.
    if ref.transform=='value' and field in ('post_id','platform'):
        from .excel import post_id_from_url
        post=post_id_from_url(actual)
        if post:actual=post if field=='post_id' else 'TikTok'
    if not equivalent(field,value,actual,tz):raise ValueError(f'{field} 字段值与原始单元格 {ref.sheet}!{cell.coordinate} 不一致')
    return ref.model_copy(update={'raw_value':clean(raw),'cell':cell.coordinate})


def validate_proposal_evidence(state,decision):
    rows={r['id']:r for r in (state.get('draft') or {}).get('requirements',[])}
    for edit in decision.edits:
        row=rows.get(edit.requirement_id)
        if not row:continue # Scope validation supplies the more specific error.
        if edit.target_id and edit.target_id!=edit.requirement_id:
            from .entities import evidence_values
            from .models import Requirement
            row=evidence_values(Requirement.model_validate(row),edit.target_id)
        for field,value in edit.values.items():
            if field in CRITICAL_FIELDS or field=='objective':
                edit.sources[field]=supported_source(state,row,field,value,edit.sources.get(field))
            if field=='structure' and value:
                from .models import StructureSpec
                structure=StructureSpec.model_validate(value)
                existing=row.get('structure') or {}
                weighted=[g for g in structure.groups if g.budget and g.budget.allocation_ratio is not None]
                ratio_message=None
                if weighted:
                    if len(weighted)!=len(structure.groups):raise ValueError('预算分配权重必须覆盖全部广告组')
                    prior_order=[g['id'] for g in existing.get('groups',[])]
                    by_id={g.id:g for g in weighted}
                    if set(prior_order)!=set(by_id):raise ValueError('比例分配需要先核实全部广告组身份')
                    ratio_message=allocation_message(state,[by_id[key].budget.allocation_ratio for key in prior_order])
                    if ratio_message is None:raise ValueError('广告组预算比例缺少明确用户依据')
                for group in structure.groups:
                    old=next((g for g in existing.get('groups',[]) if g['id']==group.id),{})
                    if group.name and group.name!=old.get('name'):
                        group.sources['name']=supported_source(state,old,'name',group.name,group.sources.get('name'))
                    child={**row,**{key:old[key] for key in ('audience','language') if old.get(key) not in (None,'')},'sources':{**row.get('sources',{}),**old.get('sources',{})}}
                    if group.budget:
                        for target,new_value in (('budget',group.budget.amount),('budget_mode',group.budget.mode),('currency',group.budget.currency)):
                            if new_value not in (None,''):
                                child[target]=(old.get('budget') or {}).get({'budget':'amount','budget_mode':'mode','currency':'currency'}[target])
                                group.sources[target]=supported_source(state,child,target,new_value,group.sources.get(target))
                        if group.budget.allocation_ratio is not None:
                            group.sources['allocation_ratio']=SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=str(group.budget.allocation_ratio),message_id=ratio_message['id'])
                    for target in ('audience','language','start_at','end_at','timezone'):
                        new_value=getattr(group,target)
                        if new_value not in (None,''):group.sources[target]=supported_source(state,child,target,new_value,group.sources.get(target))
                    for ad in group.ads:
                        old_ad=next((a for a in old.get('ads',[]) if a['id']==ad.id),{})
                        material={**row,**old_ad,'sources':{**row.get('sources',{}),**old_ad.get('sources',{})}}
                        for target in ('post_id','post_url','identity_id','cta','landing_url'):
                            new_value=getattr(ad,target)
                            if new_value:ad.sources[target]=supported_source(state,material,target,new_value,ad.sources.get(target))
                        if ad.authorization_code:
                            if not any(ad.authorization_code in str(m.get('content','')) for m in state.get('messages',[]) if m.get('role')=='user'):
                                raise ValueError('素材授权码缺少用户提供的依据')
                from .models import Requirement
                from .structure import validate_structure
                candidate=Requirement.model_validate({**row,**edit.values,'structure':structure.model_dump(mode='json')})
                issues=validate_structure(candidate)
                if issues:raise ValueError('structure 必须保留完整实体配置，不能以省略字段清空已有节点：'+str([i.model_dump(mode='json') for i in issues]))
                edit.values['structure']=structure.model_dump(mode='json')
                edit.sources['structure']=SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value='服务端核实的完整三层结构',
                    derived_from=sorted({f'{node.id}:{key}:{ref.kind}:{ref.file_id}:{ref.sheet}:{ref.cell}:{ref.message_id or ""}:{ref.context_version or ""}'
                        for node in [*structure.groups,*[a for g in structure.groups for a in g.ads]] for key,ref in node.sources.items()}))


def allocation_edits(state,allocations):
    """A budget action changes only verified budgets, never the entity graph."""
    from .models import Requirement,ChatEdit,BudgetSpec
    result=[];seen=set()
    for allocation in allocations:
        if allocation.requirement_id in seen:raise ValueError('同一计划不能重复分配预算')
        seen.add(allocation.requirement_id)
        raw=next((r for r in (state.get('draft') or {}).get('requirements',[]) if r['id']==allocation.requirement_id),None)
        if not raw:raise ValueError('预算分配包含未知计划')
        req=Requirement.model_validate(raw)
        if not req.structure:raise ValueError('预算分配需要先核实三层结构')
        order=[g.id for g in req.structure.groups]
        if set(allocation.group_weights)!=set(order):raise ValueError('预算权重必须且只能覆盖本计划全部广告组')
        message=allocation_message(state,[allocation.group_weights[key] for key in order])
        if not message or message['id']!=allocation.message_id:raise ValueError('预算分配比例与用户消息不一致')
        source=supported_source(state,raw,'budget',allocation.total)
        from .structure import allocate_budget
        allocate_budget(allocation.total,[allocation.group_weights[key] for key in order])
        structure=req.structure.model_copy(deep=True);structure.budget_level='adgroup'
        for group in structure.groups:
            group.budget=BudgetSpec(allocation_ratio=allocation.group_weights[group.id],currency=req.currency,mode=req.budget_mode)
            group.sources['allocation_ratio']=SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value=str(allocation.group_weights[group.id]),message_id=message['id'])
        result.append(ChatEdit(requirement_id=req.id,values={'budget':str(allocation.total),'structure':structure.model_dump(mode='json')},sources={'budget':source}))
    return result


def validate_candidate_structure(state,values,sources,structure):
    from .models import Requirement,ChatDecision,ChatEdit
    if structure is None:return None
    text='\n'.join(str(m.get('content','')) for m in state.get('messages',[]) if m.get('role')=='user')
    for group in structure.groups:
        explicit_name=bool(group.name and message_supports('name',group.name,text))
        explicit_single=len(structure.groups)==1 and bool(re.search(r'(?:一个|1\s*个|单个)\s*广告组',text))
        if not explicit_name and not explicit_single:raise ValueError('新增广告组的身份或数量缺少用户依据')
    row=Requirement(id='candidate',**values,sources=sources)
    decision=ChatDecision(answer='',edits=[ChatEdit(requirement_id=row.id,values={'structure':structure.model_dump(mode='json')})])
    validate_proposal_evidence({**state,'draft':{'requirements':[row.model_dump(mode='json')]}},decision)
    return structure.model_validate(decision.edits[0].values['structure'])


def verified_history_file(conn,project_id,ref,field,value,tz=''):
    """Re-read immutable project-owned export bytes before adopting historical data."""
    from .storage import target
    from .excel import parse_export
    ref=SourceRef.model_validate(ref)
    source=conn.execute('select id,kind,storage_path,sha256 from files where id=%s and project_id=%s',(ref.file_id,project_id)).fetchone()
    if not source or source['kind']!='export':raise ValueError('历史字段来源不属于当前项目的 ExportAds')
    data=target(source['storage_path']).read_bytes()
    from hashlib import sha256
    if sha256(data).hexdigest()!=source['sha256']:raise ValueError('历史来源文件指纹已变化')
    rows=parse_export(data)
    # Advertiser binding is server-owned import metadata, not a workbook cell.
    # It cannot be invented as a field reference to an export lacking the value.
    if not match_history_reference(ref,field,value,[{**r,'source_file':ref.file_id} for r in rows],tz):
        raise ValueError('历史字段原始行、原始值或规范化值与提案不一致')
    return ref

