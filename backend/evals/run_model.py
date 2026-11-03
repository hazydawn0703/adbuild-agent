import asyncio,json,time
from pathlib import Path
from langgraph.checkpoint.memory import InMemorySaver
from app.agent import conversation_graph
from app.prompts import PROMPT_HASH
from app.settings import settings
from app.evaluation import equal
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
args=parser.parse_args()
CASES=[('仅将业务编号101的预算改为40','budget','40'),('仅将业务编号101的语言改为英语','language','en'),('仅排除业务编号101','included',False),('仅将业务编号101的币种改为USD','currency','USD'),('仅将业务编号101的预算类型改为总预算','budget_mode','lifetime'),('仅将业务编号101的预算类型改为日预算','budget_mode','daily'),('仅将业务编号101的名称改为Launch','name','Launch'),('仅将业务编号101的受众改为HQ','audience','HQ'),('仅将业务编号101的行动号召改为LEARN_MORE','cta','LEARN_MORE'),('仅将业务编号101落地页改为https://example.com','landing_url','https://example.com'),('仅将业务编号101的时区改为UTC','timezone','UTC'),('仅将业务编号101的身份ID改为identity1','identity_id','identity1'),('仅将业务编号101的预算改为49.5','budget','49.5'),('仅将业务编号101的预算改为100','budget','100'),('仅将业务编号101的语言改为en','language','en'),('仅将业务编号101的开始时间改为2027-02-01T10:00:00+00:00','start_at','2027-02-01T10:00:00+00:00'),('仅将业务编号101的结束时间改为2027-02-04T10:00:00+00:00','end_at','2027-02-04T10:00:00+00:00'),('仅将业务编号101的预算改为25','budget','25'),('仅将业务编号101的名称改为Test','name','Test'),('仅将业务编号101的语言改为English','language','en')]
async def main():
    output=[];sem=asyncio.Semaphore(3)
    async def run(goal,i,repeat):
        async with sem:
            text,field,expected=CASES[i]
            draft={'id':'d','version':1,'requirements':[{'id':'r1','business_no':'101','objective':goal,'budget':'50'},{'id':'r2','business_no':'102','objective':goal,'budget':'50'}]}
            try:
                graph=conversation_graph(InMemorySaver())
                result=await graph.ainvoke({'messages':[{'role':'user','content':text}],'draft':draft,'context':{}},{'configurable':{'thread_id':f'{goal}:{i}:{repeat}'}})
                edits=result['decision']['edits']
                actual=edits[0]['values'].get(field) if len(edits)==1 and edits[0]['requirement_id']=='r1' else None
                if field=='language': actual={'英语':'en','英文':'en','english':'en'}.get(str(actual).lower(),actual)
                correct=len(edits)==1 and set(edits[0]['values'])=={field} and equal(field,expected,actual)
                record={'goal':goal,'case':i,'repeat':repeat,'correct':correct,'decision':result['decision']}
            except Exception as e:record={'goal':goal,'case':i,'repeat':repeat,'correct':False,'error_type':type(e).__name__}
            output.append(record)
            Path(args.output).write_text(json.dumps({'label':'development','prompt_hash':PROMPT_HASH,'model':settings().llm_model,'results':output},ensure_ascii=False))
            print(json.dumps({'completed':len(output),'correct':sum(r['correct'] for r in output)}),flush=True)
    await asyncio.gather(*(run(g,i,r) for g in ['followers','video_views','traffic'] for i in range(20) for r in range(3)))
asyncio.run(main())
