import asyncio
from evals.run_acceptance import normal_answers


def test_normal_workflow_only_submits_declared_facts_for_actual_questions():
    calls=[];summary={}
    state={'task':{'version':2,'questions':[{'id':'budget-question','field':'budget'},{'id':'other','field':'currency'}]},'draft':{'version':1},'preview':{'can_confirm':True}}
    async def call(stage,method,path,**kwargs):
        calls.append((stage,kwargs))
        return 200,state
    asyncio.run(normal_answers('case',{'normal_answers':{'budget':'100','scope_note':'never sent'}},call,summary,'t','p','f'))
    posted=calls[1][1]['json']
    assert posted['answers']=={'budget-question':'100'}
    assert summary['normal_answers_submitted']==1


def test_no_matching_question_does_not_append_hidden_answers():
    calls=[]
    async def call(stage,method,path,**kwargs):
        calls.append(stage)
        return 200,{'task':{'questions':[{'id':'q','field':'identity_id'}]}}
    asyncio.run(normal_answers('case',{'normal_answers':{'budget':'100'}},call,{},'t','p','f'))
    assert calls==['before-normal-answers']

