"""Batched declared user facts; at most three batches for newly exposed dependencies."""
import argparse
import asyncio
import re
from uuid import uuid4
from evals.run_regression import main


def declared_answer(question,state,facts,entity_facts):
    targets=question.get('target_ids') or []
    for plan in (state.get('draft') or {}).get('requirements',[]):
        for gi,group in enumerate((plan.get('structure') or {}).get('groups',[]),1):
            nodes=[(group,None),*[(ad,ai) for ai,ad in enumerate(group.get('ads',[]),1)]]
            for node,ai in nodes:
                if targets!=[node['id']]:continue
                choices=[f['value'] for f in entity_facts if f['field']==question['field'] and
                    (f.get('group_index')==gi and f.get('material_index')==ai or f.get('all_groups') and ai is None)]
                if len(choices)==1:return choices[0]
                clock=facts.get(question['field']+'_time')
                if clock and question['field'] in ('start_at','end_at'):
                    raw=(node.get('sources',{}).get(question['field']) or {}).get('raw_value','')
                    day=re.search(r'\d{4}-\d{2}-\d{2}',raw)
                    if day and node.get('timezone'):
                        from app.excel import date_value
                        return date_value(day[0]+'T'+clock,node['timezone']).isoformat()
    return facts.get(question['field'])


async def normal_answers(case,setup,call,summary,task_id,project_id,file_id):
    facts=setup.get('normal_answers',{})
    entity_facts=setup.get('entity_answers',[])
    if not facts and not entity_facts:return
    _,state=await call('before-normal-answers','GET','/tasks/'+task_id)
    def request(state,action,**extra):
        return {'task_id':task_id,'request_id':str(uuid4()),'expected_version':state['task']['version'],
                'expected_draft_version':(state.get('draft') or {}).get('version'),'action':action,**extra}
    submitted=set()
    def answer_key(q):return q['field'],tuple(q.get('target_ids') or q.get('requirement_ids') or ['task'])
    def available(state):return [(q,value) for q in state['task']['questions'] if answer_key(q) not in submitted
        and isinstance(value:=declared_answer(q,state,facts,entity_facts),str) and value.strip()]
    for batch in range(3):
        entries=available(state)
        if not entries:return
        answers={q['id']:value for q,value in entries}
        status,_=await call('normal-user-answers','POST',f'/projects/{project_id}/chat',json=request(state,'answer',answers=answers))
        summary['normal_answers_submitted']=summary.get('normal_answers_submitted',0)+len(answers)
        summary['normal_answer_batches']=batch+1
        if status!=200:return
        submitted.update(answer_key(q) for q,_ in entries)
        _,state=await call('after-normal-answers','GET','/tasks/'+task_id)
        if (state.get('preview') or {}).get('can_confirm'):return
        if available(state):continue
        status,_=await call('analyze-after-normal-answers','POST',f'/projects/{project_id}/chat',json=request(state,'analyze'))
        if status!=200:return
        _,state=await call('after-normal-analysis','GET','/tasks/'+task_id)
        if not state.get('draft') and file_id and 'scope' in facts:
            status,mapping=await call('mapping-after-scope-answer','POST',f'/files/{file_id}/mapping/propose')
            if status!=200 or not mapping.get('mappings') or not mapping.get('complete',True):return
            status,_=await call('confirm-mapping-after-scope-answer','POST',f'/files/{file_id}/mapping/confirm',json={'mappings':mapping['mappings'],'expected_task_version':mapping['expected_task_version'],'request_id':str(uuid4())})
            if status!=200:return
            _,state=await call('before-post-mapping-analysis','GET','/tasks/'+task_id)
            status,_=await call('post-mapping-normal-analysis','POST',f'/projects/{project_id}/chat',json=request(state,'analyze'))
            if status!=200:return
        _,state=await call('normal-final-state','GET','/tasks/'+task_id)
        pending=state['task'].get('proposal')
        if pending and pending['status']=='pending':
            status,_=await call('accept-normal-proposal','POST',f'/projects/{project_id}/chat',json=request(state,'accept',proposal_id=pending['id']))
            if status!=200:return
        _,state=await call('normal-next-dependencies','GET','/tasks/'+task_id)
        if (state.get('preview') or {}).get('can_confirm'):return


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','output','freeze','commit','run-id','parent-run'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--env-file')
    asyncio.run(main(parser.parse_args(),after_analysis=normal_answers))
