"""Normal product workflow with one batch of predeclared user facts, never gold."""
import argparse
import asyncio
from uuid import uuid4
from evals.run_regression import main


async def normal_answers(case,setup,call,summary,task_id,project_id,file_id):
    facts=setup.get('normal_answers',{})
    if not facts:return
    _,state=await call('before-normal-answers','GET','/tasks/'+task_id)
    answers={q['id']:facts[q['field']] for q in state['task']['questions'] if q['field'] in facts}
    if not answers:return
    def request(state,action,**extra):
        return {'task_id':task_id,'request_id':str(uuid4()),'expected_version':state['task']['version'],
                'expected_draft_version':(state.get('draft') or {}).get('version'),'action':action,**extra}
    status,_=await call('normal-user-answers','POST',f'/projects/{project_id}/chat',json=request(state,'answer',answers=answers))
    summary['normal_answers_submitted']=len(answers)
    if status!=200:return
    if 'scope' not in facts:return
    _,state=await call('after-normal-answers','GET','/tasks/'+task_id)
    status,_=await call('analyze-after-normal-answers','POST',f'/projects/{project_id}/chat',json=request(state,'analyze'))
    if status!=200:return
    _,state=await call('after-normal-analysis','GET','/tasks/'+task_id)
    if not state.get('draft') and file_id:
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
        await call('accept-normal-proposal','POST',f'/projects/{project_id}/chat',json=request(state,'accept',proposal_id=pending['id']))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','output','freeze','commit','run-id','parent-run'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--env-file')
    asyncio.run(main(parser.parse_args(),after_analysis=normal_answers))

