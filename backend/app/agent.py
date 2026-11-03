import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from .models import MappingProposal, ChatDecision
from .settings import settings


def llm(schema):
    cfg = settings()
    if not cfg.llm_api_key:
        raise ValueError("尚未配置平台模型 LLM_API_KEY")
    return ChatOpenAI(model=cfg.llm_model,api_key=cfg.llm_api_key,base_url=cfg.llm_base_url,
                      use_responses_api=False,timeout=60,max_retries=0,extra_body={"thinking":{"type":"disabled"}}).with_structured_output(schema,method="function_calling")


async def propose_mapping(inspection,data=None,task=None,on_progress=None):
    from .prompts import MAPPING,COMMON
    from .scope import as_dict,redact_private
    from .excel import read_workbook,validate_mapping as validate_sources,read_region_page,search_rows
    from .models import RangeRequest
    from openpyxl.utils.cell import range_boundaries
    import re
    task_data=as_dict(task) or {}
    prior=task_data.get('analysis',{}).get('mapping',{}).get('proposal',{})
    read_state=dict(prior.get('read_state') or {})
    fingerprint=inspection.get('sha256','')
    if read_state and read_state.get('file_sha256')!=fingerprint:raise ValueError('补读状态不属于当前文件，请重新审核')
    read_state.setdefault('file_sha256',fingerprint)
    read_state.setdefault('pages',{})
    read_state.setdefault('evidence',[])
    for raw in prior.get('pending_ranges',[]):
        request=RangeRequest.model_validate(raw)
        read_state['pages'].setdefault(request.sheet+'!'+request.range,{'sheet':request.sheet,'range':request.range,'next_cursor':0,'complete':False,'reason':request.reason})
    wb=read_workbook(data) if data is not None else None
    if data is not None and 'search' not in read_state:
        terms=[]
        for selector in (task_data.get('scope') or {}).get('selectors',[]):
            if selector['field'] not in ('platform','objective','requirement_id'):terms.extend(selector.get('values',[]))
        for message in task_data.get('messages',[]):
            if message.get('role')=='user':terms.extend(re.findall(r'(?<![0-9A-Za-z_])[0-9]{8,30}(?![0-9A-Za-z_])',str(message.get('content',''))))
        terms=list(dict.fromkeys(str(v) for v in terms if len(str(v))>=2))[:30]
        read_state['search']=search_rows(data,terms,max_hits=20)
    payload=redact_private({'workbook':inspection,'task':{k:task_data[k] for k in ('scope','messages','context') if k in task_data},'matched_source_rows':read_state.get('search',{})})
    messages=[('system',COMMON+MAPPING+'若样本不足，返回requested_ranges。每轮最多4个区域；单页实际补读最多1000格，每次最多两轮，未读完返回可恢复游标。列与绑定不得重复；固定/表头来源必须给明确cell。'),('user',json.dumps(payload,ensure_ascii=False))]
    completed=[]
    if prior.get('mappings'):
        from .models import SheetMapping
        completed=[SheetMapping.model_validate(m) for m in prior['mappings']]
    def key(request):return request.sheet+'!'+request.range
    def pending():
        return [RangeRequest(sheet=v['sheet'],range=v['range'],reason=v.get('reason','继续已保存的未完成区域')) for v in read_state['pages'].values() if not v['complete']]
    def read_pages(requests):
        details=[]
        for request in requests:
            existing=read_state['pages'].get(key(request))
            if existing and existing['complete']:continue
            page=read_region_page(data,request.sheet,request.range,existing['next_cursor'] if existing else 0)
            read_state['pages'][key(request)]={k:page[k] for k in ('sheet','range','next_cursor','complete','coverage')}
            read_state['pages'][key(request)]['reason']=request.reason
            details.append(page)
        read_state['evidence'].extend(details)
        return details
    def persist_progress():
        if on_progress is not None:
            snapshot=MappingProposal(mappings=completed,explanation='区域读取进度已保存，分析尚未完成',complete=False,pending_ranges=pending(),read_state=redact_private(read_state))
            on_progress(snapshot)
    rounds=0
    persist_progress()
    resumable=pending()
    if resumable and data is not None:
        read_pages(resumable[:4]);rounds+=1;persist_progress()
    if read_state['evidence']:
        messages.append(('user',json.dumps(redact_private({'saved_evidence':read_state['evidence'],'pending_ranges':[r.model_dump() for r in pending()]}),ensure_ascii=False)))
    def validate_mapping(result):
        sheets={x['sheet']:x for x in inspection['sheets']}
        requested=list({key(r):r for r in [*result.requested_ranges,*result.pending_ranges]}.values())
        if len(requested)>4:raise ProposalError('一次最多补读4个区域')
        for request in requested:
            if request.sheet not in sheets:raise ProposalError('补读工作表不存在')
            try:left,top,right,bottom=range_boundaries(request.range)
            except (ValueError,TypeError):raise ProposalError('补读区域必须为明确单元格矩形') from None
            if any(x is None for x in (left,top,right,bottom)) or top<1 or left<1 or bottom<top or right<left or bottom>50000 or right>300:raise ProposalError('补读区域超出允许范围')
        for mapping in result.mappings:
            if mapping.sheet not in sheets or mapping.last_row>sheets[mapping.sheet]['rows']:raise ProposalError('映射范围超出实际内容')
            if mapping.defaults:raise ProposalError('模型不得自动设置项目默认值')
            if wb is not None:
                try:validate_sources(mapping,wb)
                except ValueError as error:raise ProposalError(str(error)) from None
    while True:
        if sum(len(text) for _,text in messages)>140000:
            result=MappingProposal(mappings=completed,explanation='相关证据超过本轮上下文预算，已保留补读证据和游标；请进一步明确需求子集后继续。',complete=False,pending_ranges=pending())
            result.read_state=redact_private(read_state)
            return result
        result=await structured(MappingProposal,messages,validator=validate_mapping)
        if result.mappings:completed=result.mappings
        requests=list({key(r):r for r in [*pending(),*result.requested_ranges,*result.pending_ranges]}.values())
        if not requests:
            result.pending_ranges=[]
            result.read_state=redact_private(read_state)
            return result
        if data is None or rounds>=2:
            for request in requests:
                read_state['pages'].setdefault(key(request),{'sheet':request.sheet,'range':request.range,'next_cursor':0,'complete':False,'reason':request.reason})
            result.complete=False;result.pending_ranges=requests;result.mappings=completed
            result.explanation+='；已保留补读证据与游标，区域尚未读全，请继续补读。'
            result.read_state=redact_private(read_state)
            return result
        details=read_pages(requests[:4]);rounds+=1;persist_progress()
        if not details:
            # Re-requesting an already-read region is not progress. Ask for a
            # different source or human clarification instead of silently retrying.
            result.complete=False;result.pending_ranges=[];result.mappings=completed
            result.explanation+='；重复请求已完整读取的区域，没有新的补读进展，请明确所缺字段。'
            result.read_state=redact_private(read_state)
            return result
        messages.append(('user',json.dumps(redact_private({'additional_evidence':details,'pending_ranges':[r.model_dump() for r in pending()]}),ensure_ascii=False)))


async def structured(schema,messages,validator=None):
    import asyncio
    from pydantic import ValidationError
    from langchain_core.exceptions import OutputParserException
    import logging,time
    from .prompts import PROMPT_HASH
    for attempt in range(2):
        started=time.monotonic()
        try:
            result=await asyncio.wait_for(llm(schema).ainvoke(messages),60)
            result=schema.model_validate(result)
            if validator: validator(result)
            logging.getLogger('adbuild.analysis').info('model_stage schema=%s prompt=%s duration=%.3f attempt=%d',schema.__name__,PROMPT_HASH,time.monotonic()-started,attempt+1)
            return result
        except (ValidationError,OutputParserException,ProposalError) as error:
            if attempt: raise
            messages=[*messages,('system','结构校验失败，请修正字段结构，不补造业务值：'+str(error))]


class ConversationState(TypedDict):
    messages: list[dict]
    draft: dict | None
    context: dict
    decision: dict
    documents: list[dict]
    scope: dict
    prepared: dict
    progress: dict


class ProposalError(ValueError):
    pass


def validate_decision(state,decision):
    import re
    from .service import EDITABLE
    rows=(state['draft'] or {}).get('requirements',[])
    ids={r['id'] for r in rows}
    from .scope import validate_scope
    if decision.scope is not None:
        try: validate_scope(decision.scope,state['messages'])
        except ValueError as error: raise ProposalError(str(error)) from None
    for question in decision.questions:
        if question.scope=='task':
            if question.requirement_ids: raise ProposalError('任务级问题不能引用需求行')
        elif not state.get('draft') or not question.requirement_ids or not set(question.requirement_ids)<=ids:
            raise ProposalError('需求级澄清必须关联当前草稿的有效需求')
    message=next((m['content'] for m in reversed(state['messages']) if m['role']=='user'),'')
    instruction_context='\n'.join(m['content'] for m in state['messages'] if m.get('role')=='user')
    explicit={r['id'] for r in rows if any(v and re.search(r'(?<![0-9A-Za-z_])'+re.escape(str(v))+r'(?![0-9A-Za-z_])',message) for v in (r.get('business_no'),r['id']))}
    restricted=bool(re.search(r'仅|只',message)) and not re.search(r'其余|其他|其它|全部|所有',message)
    if decision.expansions and not re.search(r'同时|分别|各|新增目标|拆|两种|多目标|\bboth\b|\balso\b|\bmultiple\b',instruction_context,re.I):
        raise ProposalError('目标拆分需要用户明确要求，不能自动扩大需求')
    for expansion in decision.expansions:
        base=next((r for r in rows if r['id']==expansion.base_requirement_id),None)
        if not base: raise ProposalError('目标拆分包含未知需求')
        if restricted and explicit and expansion.base_requirement_id not in explicit:
            raise ProposalError('目标拆分超出明确需求范围')
        from .excel import objective
        parts=re.split(r'[，,；;\n/]|和|以及|及|\band\b',instruction_context,flags=re.I)
        if not any(objective(part)==expansion.objective for part in parts):
            raise ProposalError('拆分目标缺少用户消息依据')
        from .provenance import supported_source
        try:supported_source(state,base,'budget',expansion.budget)
        except ValueError as error:raise ProposalError('拆分预算缺少用户消息或原始需求依据：'+str(error)) from None
    for addition in decision.additions:
        from .excel import normalize_value
        from .provenance import supported_source
        values=addition.values
        if not (values.get('post_id') or values.get('post_url')) or not values.get('objective'):
            raise ProposalError('新增候选必须有明确帖子或链接和目标，其他未知字段应澄清')
        try:
            for field,value in list(values.items()):
                if value in (None,''):continue
                normalized=normalize_value(field,value,values.get('timezone',''))
                addition.sources[field]=supported_source(state,{},field,normalized,addition.sources.get(field))
                addition.values[field]=normalized
        except ValueError as error:raise ProposalError('新增候选证据无效：'+str(error)) from None
    for edit in decision.edits:
        if edit.requirement_id not in ids or set(edit.values)-EDITABLE:
            raise ProposalError('提案包含未知需求或不允许的字段')
        if restricted and explicit and edit.requirement_id not in explicit:
            raise ProposalError('提案超出用户明确限定的需求范围，不得为其他需求生成任何修改')
        if 'included' in edit.values and not re.search(r'排除|不投|不要投|取消|不搭建|不加热|勾选|纳入|选中|重投|恢复',message):
            raise ProposalError('未要求改变需求是否参与本次投放；未修改其他条目不等于取消其投放')
        if 'timezone' in edit.values and any(f in edit.values for f in ('start_at','end_at')) and not re.search(r'时区|timezone|UTC|GMT',message,re.I):
            raise ProposalError('日期自带偏移只用于该日期，不得据此修改独立的投放时区字段')
    from .provenance import validate_proposal_evidence
    try: validate_proposal_evidence(state,decision)
    except ValueError as error: raise ProposalError(str(error)) from None


def conversation_graph(checkpointer):
    from .prompts import COMMON, INTENT, HISTORY, CLARIFY
    def prepare(state):
        from .scope import compact_state
        prepared=compact_state(state)
        return {'prepared':prepared,'progress':{'phase':'evidence','coverage':prepared['coverage']}}
    async def understand(state):
        payload=json.dumps(state['prepared'],ensure_ascii=False)
        if len(payload)>150000: raise ValueError('相关需求与消息超过单轮分析范围；已保留任务，请明确子集后继续，尚未完成全部分析')
        decision=await structured(ChatDecision,[('system',COMMON+INTENT+HISTORY+CLARIFY),('user',payload)],validator=lambda d:validate_decision(state,d))
        return {'decision':decision.model_dump(mode='json'),'progress':{'phase':'validated','coverage':state['prepared']['coverage']}}
    def validate(state):
        validate_decision(state,ChatDecision.model_validate(state['decision']))
        return {}
    builder=StateGraph(ConversationState)
    builder.add_node('prepare_evidence',prepare)
    builder.add_node('understand',understand)
    builder.add_node('validate',validate)
    builder.add_edge(START,'prepare_evidence');builder.add_edge('prepare_evidence','understand');builder.add_edge('understand','validate');builder.add_edge('validate',END)
    return builder.compile(checkpointer=checkpointer)
