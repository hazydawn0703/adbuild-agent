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


async def propose_mapping(inspection,data=None):
    from .prompts import MAPPING, COMMON
    from .excel import rebind_mapping
    messages=[('system',COMMON+MAPPING+'若样本不足，返回 requested_ranges 请求明确工作表和区域，最多两轮，每个区域最多1000格。'),('user',json.dumps(inspection,ensure_ascii=False))]
    for round_index in range(3):
        result=await structured(MappingProposal,messages)
        if not result.requested_ranges: break
        if data is None or round_index==2: raise ValueError('需要更多单元格信息，请人工审核区域')
        if len(result.requested_ranges)>4: raise ValueError('一次最多补读4个区域')
        from .excel import read_region
        details=[{'sheet':r.sheet,'range':r.range,'cells':read_region(data,r.sheet,r.range)} for r in result.requested_ranges]
        messages.append(('user',json.dumps({'additional_evidence':details},ensure_ascii=False)))
    sheets={x['sheet']:x for x in inspection['sheets']}
    for mapping in result.mappings:
        if mapping.sheet not in sheets or mapping.last_row>sheets[mapping.sheet]['rows']:
            raise ValueError('映射范围超出实际内容')
        if mapping.defaults: raise ValueError('模型不得自动设置项目默认值')
    excluded={r.sheet for r in result.regions if r.role in ('history','statistics','account') and not any(x.sheet==r.sheet and x.role=='requirements' for x in result.regions)}
    if any(m.sheet in excluded for m in result.mappings): raise ValueError('映射包含已明确排除的区域')
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


class ProposalError(ValueError):
    pass


def validate_decision(state,decision):
    import re
    from .service import EDITABLE
    rows=(state['draft'] or {}).get('requirements',[])
    ids={r['id'] for r in rows}
    message=next((m['content'] for m in reversed(state['messages']) if m['role']=='user'),'')
    explicit={r['id'] for r in rows if any(v and re.search(r'(?<![0-9A-Za-z_])'+re.escape(str(v))+r'(?![0-9A-Za-z_])',message) for v in (r.get('business_no'),r['id']))}
    restricted=bool(re.search(r'仅|只',message)) and not re.search(r'其余|其他|其它|全部|所有',message)
    for edit in decision.edits:
        if edit.requirement_id not in ids or set(edit.values)-EDITABLE:
            raise ProposalError('提案包含未知需求或不允许的字段')
        if restricted and explicit and edit.requirement_id not in explicit:
            raise ProposalError('提案超出用户明确限定的需求范围，不得为其他需求生成任何修改')
        if 'included' in edit.values and not re.search(r'排除|不投|不要投|取消|不搭建|不加热|勾选|纳入|选中|重投|恢复',message):
            raise ProposalError('未要求改变需求是否参与本次投放；未修改其他条目不等于取消其投放')
        if 'timezone' in edit.values and any(f in edit.values for f in ('start_at','end_at')) and not re.search(r'时区|timezone|UTC|GMT',message,re.I):
            raise ProposalError('日期自带偏移只用于该日期，不得据此修改独立的投放时区字段')


def conversation_graph(checkpointer):
    from .prompts import COMMON, INTENT, HISTORY, CLARIFY
    async def understand(state):
        payload=json.dumps({'messages':state['messages'],'draft':state['draft'],'context':state.get('context',{}),'documents':state.get('documents',[])},ensure_ascii=False)
        if len(payload)>150000: raise ValueError('本任务内容超过单轮分析范围，请明确需求子集后处理')
        decision=await structured(ChatDecision,[('system',COMMON+INTENT+HISTORY+CLARIFY),('user',payload)],validator=lambda d:validate_decision(state,d))
        return {'decision':decision.model_dump(mode='json')}
    def validate(state):
        validate_decision(state,ChatDecision.model_validate(state['decision']))
        return {}
    builder=StateGraph(ConversationState)
    builder.add_node('understand',understand)
    builder.add_node('validate',validate)
    builder.add_edge(START,'understand');builder.add_edge('understand','validate');builder.add_edge('validate',END)
    return builder.compile(checkpointer=checkpointer)
