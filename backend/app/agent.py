from .usage import measured, record_usage, record_attempt
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from .models import MappingProposal, ChatDecision, TaskScope, Clarification, SourceRef
from .rules import business_now
from .settings import settings
from openai import APIError,APIConnectionError,APITimeoutError,APIStatusError

FLOW_VERSION='entity-graph-4'


class ModelServiceError(RuntimeError):
    """Public model availability facts; no provider body, request or credentials."""
    def __init__(self,category,*,retryable,upstream_status=None):
        self.category=category
        self.retryable=retryable
        self.upstream_status=upstream_status
        self.status_code=504 if category=='analysis_timeout' else 503 if retryable or category in ('configuration','insufficient_balance') else 502
        self.code='model_insufficient_balance' if category=='insufficient_balance' else 'analysis_timeout' if category=='analysis_timeout' else 'model_service_unavailable' if self.status_code==503 else 'model_service_rejected'
        if category=='analysis_timeout':
            self.safe_message='分析超时，输入和已完成进度已保留，请稍后恢复未完成分析。'
        elif category=='insufficient_balance':
            self.safe_message='模型服务余额不足，输入和已完成进度已保留；请联系管理员补充模型账户余额后继续。'
        elif retryable:
            self.safe_message='模型服务暂时不可用，输入和已完成进度已保留，请稍后继续分析。'
        else:
            self.safe_message='模型服务未能接受当前请求，任务已保留，请联系管理员检查模型配置后再继续。'
        super().__init__(self.safe_message)

    @classmethod
    def from_provider(cls,error):
        if isinstance(error,APITimeoutError):return cls('timeout',retryable=True)
        if isinstance(error,APIConnectionError):return cls('connection',retryable=True)
        status=getattr(error,'status_code',None)
        if isinstance(error,APIStatusError):
            retryable=status in (408,409,429) or status>=500
            category='insufficient_balance' if status==402 else 'rate_limit' if status==429 else 'upstream_server' if retryable else 'configuration' if status in (401,403,404) else 'request_rejected'
            return cls(category,retryable=retryable,upstream_status=status)
        return cls('invalid_response',retryable=False,upstream_status=status if isinstance(status,int) else None)

    def public_error(self):
        data={'status':'failed','code':self.code,'category':self.category,'message':self.safe_message,'retryable':self.retryable}
        if self.upstream_status is not None:data['upstream_status']=self.upstream_status
        return data


def llm(schema):
    cfg = settings()
    if not cfg.llm_api_key:
        raise ValueError("尚未配置平台模型 LLM_API_KEY")
    return ChatOpenAI(model=cfg.llm_model,api_key=cfg.llm_api_key,base_url=cfg.llm_base_url,
                      use_responses_api=False,timeout=60,max_retries=0,extra_body={"thinking":{"type":"disabled"}}).with_structured_output(schema,method="function_calling",include_raw=True)


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
        raw=None
        try:
            record_attempt()
            envelope=await asyncio.wait_for(llm(schema).ainvoke(messages),60)
            from .scope import redact_private
            raw=envelope['raw']
            record_usage(raw)
            logging.getLogger('adbuild.analysis').info('model_output %s',json.dumps(redact_private({'schema':schema.__name__,'attempt':attempt+1,'raw':raw.model_dump(mode='json'),'parsing_error':str(envelope['parsing_error']) if envelope['parsing_error'] else None}),ensure_ascii=False))
            result=schema.model_validate(envelope['parsed'])
            if validator: validator(result)
            logging.getLogger('adbuild.analysis').info('model_stage schema=%s prompt=%s duration=%.3f attempt=%d',schema.__name__,PROMPT_HASH,time.monotonic()-started,attempt+1)
            return result
        except APIError as error:
            # Availability failures are not malformed business proposals. Never spend
            # the schema-repair attempt retrying an unavailable provider implicitly.
            raise ModelServiceError.from_provider(error) from None
        except (ValidationError,OutputParserException,ProposalError) as error:
            if attempt: raise ModelServiceError('invalid_response',retryable=False) from None
            messages=[*messages,('system','结构校验失败，请修正字段结构，不补造业务值：'+str(error)+'；错误输出：'+json.dumps(redact_private(raw.model_dump(mode='json') if raw is not None else None),ensure_ascii=False))]


class ConversationState(TypedDict):
    messages: list[dict]
    draft: dict | None
    context: dict
    decision: dict
    documents: list[dict]
    scope: dict
    prepared: dict
    progress: dict
    scope_question: dict | None
    project_account_id: str
    history_records: list[dict]


class ProposalError(ValueError):
    pass


def scope_matches(state,scope):
    from types import SimpleNamespace
    from .scope import matches,as_dict
    scope=as_dict(scope) or {}
    rows=(state.get('draft') or {}).get('requirements',[])
    selected=[]
    for row in rows:
        req=SimpleNamespace(**row)
        if (scope.get('all_records') or scope.get('selectors')) and all(matches(req,s) for s in scope.get('selectors',[])) and not any(matches(req,s) for s in scope.get('exclude_selectors',[]) if not(row.get('structure') and s['field']=='post_id')):
            selected.append(row['id'])
    return set(selected)


def scope_required(state):
    import re
    current=state.get('scope') or {}
    latest=next((m for m in reversed(state.get('messages',[])) if m.get('role')=='user'),{})
    message=latest.get('content','')
    if message.startswith('本任务回答：'):
        try:answers=json.loads(message[len('本任务回答：'):])
        except (ValueError,TypeError):answers=[]
        if isinstance(answers,list) and any(isinstance(answer,dict) and answer.get('field')=='scope' for answer in answers):return True
    if current.get('status')=='resolved':
        if latest.get('id') in current.get('message_ids',[]) or message.startswith('本任务回答：'):return False
        query=re.search(r'(?:查|查询|查看|检索|搜索|找).{0,12}(?:历史|以前|上次|投放记录)|(?:历史|投放记录).{0,12}(?:查|查询|查看|检索|搜索)|\b(?:query|search|look\s*up)\b.{0,20}\bhistory\b',message,re.I)
        selection=re.search(r'重投|重新投放|新增(?:一条|广告|需求|计划|投放)|(?:本次|这次)(?:仅|只)?(?:搭建|投放)|排除|取消(?:投放|搭建|勾选)|\b(?:relaunch|exclude)\b',message,re.I)
        edit=re.search(r'修改|改为|改成|调整|设置|\b(?:change|update|set)\b',message,re.I)
        return bool(query or selection or current.get('action')=='history' and edit)
    rows=(state.get('draft') or {}).get('requirements',[])
    if rows:
        # Editing a known draft field does not select a new batch.
        if re.search(r'修改|改为|改成|调整|设置|\b(?:change|update|set)\b',message,re.I) and not re.search(r'搭建|新增|重投|纳入|勾选|排除|取消|\b(?:select|exclude|include)\b',message,re.I):return False
    return True


def validate_selection(state,decision):
    """A selection is a grounded action, never a claim that the user typed a boolean."""
    import re
    from .scope import as_dict,validate_scope
    rows={r['id']:r for r in (state.get('draft') or {}).get('requirements',[])}
    scope=as_dict(state.get('scope'))
    scope=scope or {}
    desired=None
    if scope.get('status')=='resolved' and scope.get('action') in ('new','repost','exclude'):
        try:validate_scope(scope,state.get('messages',[]))
        except ValueError as error:raise ProposalError(str(error)) from None
        matched=scope_matches(state,scope)
        desired=matched if scope['action']!='exclude' else {r['id'] for r in rows.values() if r.get('included',True)}-matched
        if any(r.get('structure') for r in rows.values()):
            from .scope import select_requirements
            from .models import Requirement
            desired={r.id for r in select_requirements([Requirement.model_validate(r) for r in rows.values()],scope,state.get('messages',[])) if r.included}
    kept=[]
    for edit in decision.edits:
        if 'included' in edit.values and edit.requirement_id in rows:
            value=edit.values['included'];row=rows[edit.requirement_id]
            if not isinstance(value,bool):raise ProposalError('参与本批必须是明确选择动作')
            if value==row.get('included',True):
                edit.values.pop('included');edit.sources.pop('included',None)
            else:
                message=None
                if desired is not None and value==(edit.requirement_id in desired):
                    message=next((m for m in reversed(state.get('messages',[])) if m.get('role')=='user' and m.get('id') in scope.get('message_ids',[])),None)
                if message is None:
                    latest=next((m for m in reversed(state.get('messages',[])) if m.get('role')=='user'),{})
                    text=latest.get('content','')
                    explicit=any(v and re.search(r'(?<![0-9A-Za-z_])'+re.escape(str(v))+r'(?![0-9A-Za-z_])',text) for v in (row.get('business_no'),row.get('post_id'),row['id']))
                    if explicit and re.search(r'排除|不投|取消|不搭建|不加热|勾选|纳入|选中|重投|恢复|仅.*搭建|只.*搭建|\b(?:exclude|include|select)\b',text,re.I):message=latest
                if not message or not message.get('id'):raise ProposalError('参与范围改变缺少已核实范围或明确选择消息，字段修改不能扩大范围')
                edit.sources['included']=SourceRef(file_id='',sheet='',cell='',kind='confirmed',message_id=message['id'],raw_value=str(value).lower())
        if edit.values:kept.append(edit)
    decision.edits=kept


def validate_new_candidates(state,decision):
    import re
    from .excel import normalize_value
    rows=(state.get('draft') or {}).get('requirements',[])
    text='\n'.join(m.get('content','') for m in state.get('messages',[]) if m.get('role')=='user')
    explicit_batch=bool(re.search(r'新批次|另[起开做投].{0,4}一批|再投|重投|重新投放|\b(?:new\s+batch|relaunch|rerun)\b',text,re.I))
    for candidate in decision.additions:
        values=candidate.values
        post=values.get('post_id')
        if not post and values.get('post_url'):
            match=re.search(r'/video/(\d+)',str(values['post_url']))
            if match:post=match[1]
        goal=normalize_value('objective',values.get('objective')) if values.get('objective') else None
        account=values.get('account_id') or state.get('project_account_id')
        existing=[r for r in rows if post and str(r.get('post_id'))==str(post) and r.get('objective')==goal and (not account or not r.get('account_id') or str(r['account_id'])==str(account))]
        if existing:
            has_schedule=bool(values.get('start_at') and values.get('end_at'))
            from .excel import date_value
            changed_schedule=has_schedule and all(all(r.get(f) for f in ('start_at','end_at')) and
                any(date_value(values[f],values.get('timezone',''))!=date_value(r[f],r.get('timezone','')) for f in ('start_at','end_at')) for r in existing)
            if not explicit_batch or not changed_schedule:
                raise ProposalError('当前草稿已有同账户、帖子和目标的需求；请改用现有需求的 edits/选择或澄清，不能重复 additions。新批次须明确提出并给出不同排期')


def validate_decision(state,decision):
    import re
    from .service import EDITABLE
    rows=(state['draft'] or {}).get('requirements',[])
    ids={r['id'] for r in rows}
    from .scope import validate_scope,as_dict
    active_scope=as_dict(state.get('scope'))
    if active_scope and active_scope.get('action')=='history' and (decision.edits or decision.expansions or decision.additions):
        raise ProposalError('历史查询为只读动作，不能编辑、拆分或新增需求；请明确建立搭建任务后再修改')
    if active_scope is not None:
        try: validate_scope(active_scope,state['messages'])
        except ValueError as error: raise ProposalError(str(error)) from None
    for question in decision.questions:
        if question.scope=='task':
            if question.requirement_ids: raise ProposalError('任务级问题不能引用需求行')
        elif not state.get('draft') or not question.requirement_ids or not set(question.requirement_ids)<=ids:
            raise ProposalError('需求级澄清必须关联当前草稿的有效需求')
        if question.target_ids:
            from .entities import find_entity
            from .models import Requirement
            targets=set()
            for row in rows:
                if row['id'] not in question.requirement_ids:continue
                req=Requirement.model_validate(row)
                for target in question.target_ids:
                    try:find_entity(req,target)
                    except ValueError:continue
                    targets.add(target)
            if targets!=set(question.target_ids):raise ProposalError('问题引用了不属于当前计划的实体')
    validate_selection(state,decision)
    try:validate_new_candidates(state,decision)
    except ValueError as error:raise ProposalError(str(error)) from None
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
                addition.sources[field]=supported_source(state,values,field,normalized,addition.sources.get(field))
                addition.values[field]=normalized
        except ValueError as error:raise ProposalError('新增候选证据无效：'+str(error)) from None
        if addition.structure:
            from .provenance import validate_candidate_structure
            try:addition.structure=validate_candidate_structure(state,addition.values,addition.sources,addition.structure)
            except ValueError as error:raise ProposalError(str(error)) from None
    for edit in decision.edits:
        if edit.requirement_id not in ids or set(edit.values)-EDITABLE:
            raise ProposalError('提案包含未知需求或不允许的字段')
        if edit.target_id and edit.target_id!=edit.requirement_id:
            from .entities import find_entity,GROUP_FIELDS,MATERIAL_FIELDS
            from .models import Requirement,GroupSpec
            try:node=find_entity(Requirement.model_validate(next(r for r in rows if r['id']==edit.requirement_id)),edit.target_id)
            except ValueError as error:raise ProposalError(str(error)) from None
            if set(edit.values)-(GROUP_FIELDS if isinstance(node,GroupSpec) else MATERIAL_FIELDS):raise ProposalError('修改字段不属于该实体')
        if restricted and explicit and edit.requirement_id not in explicit:
            raise ProposalError('提案超出用户明确限定的需求范围，不得为其他需求生成任何修改')
        if 'timezone' in edit.values and any(f in edit.values for f in ('start_at','end_at')) and not re.search(r'时区|timezone|UTC|GMT',message,re.I):
            raise ProposalError('日期自带偏移只用于该日期，不得据此修改独立的投放时区字段')
    from .provenance import validate_proposal_evidence
    try: validate_proposal_evidence(state,decision)
    except ValueError as error: raise ProposalError(str(error)) from None


def conversation_graph(checkpointer):
    from .prompts import COMMON, INTENT, HISTORY, CLARIFY, SCOPE
    def waiting_decision(state,scope):
        import re
        text='\n'.join(m.get('content','') for m in state.get('messages',[]) if m.get('role')=='user')
        material=any(s.field=='post_id' for s in scope.selectors) or re.search(r'https?://[^\s]*tiktok\.com/[^\s]*/video/\d+|(?:帖子(?:\s*ID)?|视频\s*ID|post(?:\s*id)?)\s*[:：=#]?\s*\d+',text,re.I)
        if scope.status=='resolved' and scope.action in ('new','repost') and not state.get('draft') and not state.get('documents') and not material:
            return ChatDecision(answer='已记录本次需求范围。请上传需求Excel和ExportAds，或提供明确的TikTok帖子及目标后继续整理方案。').model_dump(mode='json')
        return None
    def prepare(state):
        from .scope import compact_state
        scope=TaskScope.model_validate(state.get('scope') or {}).model_dump(mode='json')
        prepared=compact_state({**state,'scope':scope})
        prepared['business_time']=business_now().isoformat()
        return {'scope':scope,'prepared':prepared,'scope_question':None,'progress':{'phase':'evidence','coverage':prepared['coverage']}}
    async def resolve_scope(state):
        from .scope import compact_state,validate_scope
        if not scope_required(state):
            waiting=waiting_decision(state,TaskScope.model_validate(state.get('scope') or {}))
            if waiting:return {'decision':waiting,'progress':{**state.get('progress',{}),'phase':'awaiting_input'}}
            return {'progress':{**state.get('progress',{}),'phase':'scope_skipped'}}
        payload=state['prepared']
        def validate(scope):
            try:validate_scope(scope,state.get('messages',[]))
            except ValueError as error:raise ProposalError(str(error)) from None
            rows=(state.get('draft') or {}).get('requirements',[])
            if scope.status=='resolved' and rows and scope.action in ('new','repost','exclude') and not scope_matches(state,scope):
                import re
                text='\n'.join(m.get('content','') for m in state.get('messages',[]) if m.get('role')=='user')
                explicit_new_post=scope.action in ('new','repost') and any(s.field=='post_id' for s in scope.selectors) and re.search(r'新增|新加|再加|重投|重新投放|\b(?:new|relaunch)\b',text,re.I)
                if not explicit_new_post:
                    raise ProposalError('明确的范围在当前全部候选中没有匹配；请检查字段身份，无法确定时返回 unresolved 并说明需要的信息')
        try:
            result=await structured(TaskScope,[('system',COMMON+SCOPE),('user',json.dumps(payload,ensure_ascii=False))],validator=validate)
        except ProposalError as error:
            result=TaskScope(reason=str(error))
        output={'scope':result.model_dump(mode='json'),'progress':{**state.get('progress',{}),'phase':'scope_resolved' if result.status=='resolved' else 'scope_pending'}}
        if result.status!='resolved':
            question=Clarification(id='scope',field='scope',scope='task',question=result.reason or '请明确本次选择的业务编号、帖子或需求集合，其他资料可以继续保留为参考。')
            output['scope_question']=question.model_dump(mode='json')
            output['decision']=ChatDecision(answer=question.question,questions=[question]).model_dump(mode='json')
        else:
            output['prepared']=compact_state({**state,'scope':result.model_dump(mode='json')})
            waiting=waiting_decision(state,result)
            if waiting:
                output['decision']=waiting
                output['progress']['phase']='awaiting_input'
        return output
    async def understand(state):
        if state.get('documents') and not state.get('draft'):
            return {'decision':ChatDecision(answer='已记录需求范围，请先审核文件映射，完整读取候选后继续分析。').model_dump(mode='json'),'progress':{'phase':'awaiting_mapping','coverage':state['prepared']['coverage']}}
        payload=json.dumps(state['prepared'],ensure_ascii=False)
        if len(payload)>150000: raise ValueError('相关需求与消息超过单轮分析范围；已保留任务，请明确子集后继续，尚未完成全部分析')
        from .models import CandidateDecision,DraftDecision
        scope=state.get('scope') or {}
        new_material=scope.get('action') in ('new','repost') and any(s['field']=='post_id' for s in scope.get('selectors',[])) and not scope_matches(state,scope)
        schema=DraftDecision if state.get('draft') and not new_material else CandidateDecision
        def compile_output(output):
            data=output.model_dump();allocations=data.pop('allocations',[])
            decision=ChatDecision.model_validate(data)
            if allocations:
                from .models import BudgetAllocation
                from .provenance import allocation_edits
                allocation_ids={a['requirement_id'] for a in allocations}
                if any(e.requirement_id in allocation_ids and {'budget','structure'} & set(e.values) for e in decision.edits):
                    raise ProposalError('预算分配已包含总额和组预算，不要对同一计划重复提交budget或structure修改')
                try:decision.edits.extend(allocation_edits(state,[BudgetAllocation.model_validate(a) for a in allocations]))
                except ValueError as error:raise ProposalError(str(error)) from None
            validate_decision(state,decision)
            return decision
        def validate_output(output):
            compile_output(output)
        output=await structured(schema,[('system',COMMON+INTENT+HISTORY+CLARIFY),('user',payload)],validator=validate_output)
        decision=compile_output(output)
        return {'decision':decision.model_dump(mode='json'),'progress':{'phase':'validated','coverage':state['prepared']['coverage']}}
    def validate(state):
        validate_decision(state,ChatDecision.model_validate(state['decision']))
        return {}
    builder=StateGraph(ConversationState)
    builder.add_node('prepare_evidence',measured('prepare_evidence',prepare))
    builder.add_node('resolve_scope',measured('resolve_scope',resolve_scope))
    builder.add_node('understand',measured('understand',understand))
    builder.add_node('validate',measured('validate',validate))
    builder.add_edge(START,'prepare_evidence')
    builder.add_edge('prepare_evidence','resolve_scope')
    builder.add_conditional_edges('resolve_scope',lambda state:'validate' if state.get('scope_question') or state.get('progress',{}).get('phase')=='awaiting_input' else 'understand')
    builder.add_edge('understand','validate');builder.add_edge('validate',END)
    return builder.compile(checkpointer=checkpointer)
