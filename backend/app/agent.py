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


async def propose_mapping(inspection,data=None,task=None):
    from .prompts import MAPPING, COMMON
    from openpyxl.utils.cell import range_boundaries
    from .scope import as_dict,redact_private
    task_data=as_dict(task) or {}
    payload=redact_private({'workbook':inspection,'task':{k:task_data[k] for k in ('scope','messages','context') if k in task_data}})
    messages=[('system',COMMON+MAPPING+'若样本不足，返回 requested_ranges 请求明确工作表和区域，最多两轮、每轮最多4个区域，每个区域最多1000格。'),('user',json.dumps(payload,ensure_ascii=False))]
    def validate_mapping(result):
        sheets={x['sheet']:x for x in inspection['sheets']}
        if len(result.requested_ranges)>4: raise ProposalError('一次最多补读4个区域')
        for request in result.requested_ranges:
            if request.sheet not in sheets: raise ProposalError('补读工作表不存在')
            try: left,top,right,bottom=range_boundaries(request.range)
            except (ValueError,TypeError): raise ProposalError('补读区域必须为明确的单元格矩形') from None
            if any(x is None for x in (left,top,right,bottom)) or top<1 or left<1 or bottom<top or right<left or bottom>50000 or right>300 or (bottom-top+1)*(right-left+1)>1000:
                raise ProposalError('补读范围最多1000格，请按列或行窗口分页')
        for mapping in result.mappings:
            if mapping.sheet not in sheets or mapping.last_row>sheets[mapping.sheet]['rows']:
                raise ProposalError('映射范围超出实际内容')
            if mapping.defaults: raise ProposalError('模型不得自动设置项目默认值')
    completed=[]
    for round_index in range(3):
        result=await structured(MappingProposal,messages,validator=validate_mapping)
        if result.mappings: completed=result.mappings
        if not result.requested_ranges: break
        if data is None or round_index==2:
            result.complete=False
            result.pending_ranges=result.requested_ranges
            result.mappings=completed
            result.explanation+='；区域信息尚未读全，请继续补读或审核未完成区域。'
            return result
        from .excel import read_region
        details=[{'sheet':r.sheet,'range':r.range,'cells':read_region(data,r.sheet,r.range)} for r in result.requested_ranges]
        messages.append(('user',json.dumps({'additional_evidence':details},ensure_ascii=False)))
    # Historical rows may be explicitly selected for a new batch. Region role alone is not scope.
    return result


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
    explicit={r['id'] for r in rows if any(v and re.search(r'(?<![0-9A-Za-z_])'+re.escape(str(v))+r'(?![0-9A-Za-z_])',message) for v in (r.get('business_no'),r['id']))}
    restricted=bool(re.search(r'仅|只',message)) and not re.search(r'其余|其他|其它|全部|所有',message)
    if decision.expansions and not re.search(r'同时|分别|各|新增目标|拆|两种|多目标|\bboth\b|\balso\b|\bmultiple\b',message,re.I):
        raise ProposalError('目标拆分需要用户明确要求，不能自动扩大需求')
    for expansion in decision.expansions:
        base=next((r for r in rows if r['id']==expansion.base_requirement_id),None)
        if not base: raise ProposalError('目标拆分包含未知需求')
        if restricted and explicit and expansion.base_requirement_id not in explicit:
            raise ProposalError('目标拆分超出明确需求范围')
        from .excel import objective
        parts=re.split(r'[，,；;\n/]|和|以及|及|\band\b',message,flags=re.I)
        if not any(objective(part)==expansion.objective for part in parts):
            raise ProposalError('拆分目标缺少用户消息依据')
        from decimal import Decimal
        amounts=[Decimal(v) for v in re.findall(r'(?<!\d)\d+(?:\.\d+)?(?!\d)',message)]
        if expansion.budget not in amounts and str(expansion.budget)!=str(base.get('budget')):
            raise ProposalError('拆分预算缺少用户消息或原始需求依据')
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
