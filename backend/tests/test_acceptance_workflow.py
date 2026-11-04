import asyncio
from evals.run_acceptance import normal_answers
from evals.run_acceptance import declared_answer
from evals.prepare_campaign_answers import permitted_facts


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


def test_entity_answers_use_actual_target_and_declared_ordinal_only():
    facts,entities=permitted_facts({'post_ids_by_group':{'G1':['100','200'],'G2':['300']}})
    state={'draft':{'requirements':[{'structure':{'groups':[{'id':'g1','ads':[{'id':'a1'},{'id':'a2'}]},{'id':'g2','ads':[{'id':'a3'}]}]}}]}}
    assert declared_answer({'field':'post_id','target_ids':['a2']},state,facts,entities)=='200'
    assert declared_answer({'field':'post_id','target_ids':['a3']},state,facts,entities)=='300'
    assert declared_answer({'field':'post_id','target_ids':['a1','a2']},state,facts,entities) is None
    assert declared_answer({'field':'identity_id','target_ids':['a2']},state,facts,entities) is None


def test_new_budget_dependency_gets_second_batch_but_same_field_owner_is_never_reanswered():
    calls=[];summary={};answered=0
    def state():
        q={'id':'level','field':'budget_level','target_ids':['p']} if answered==0 else {'id':'budget','field':'budget','target_ids':['g']}
        return {'task':{'version':answered+1,'questions':[q]},'draft':{'version':answered+1,'requirements':[
            {'id':'p','structure':{'groups':[{'id':'g','ads':[]}]}}]},'preview':{'can_confirm':False}}
    async def call(stage,method,path,**kwargs):
        nonlocal answered
        calls.append((stage,kwargs.get('json')))
        if method=='POST' and kwargs['json']['action']=='answer':answered+=1
        return 200,state()
    asyncio.run(normal_answers('C',{'normal_answers':{'budget_level':'adgroup'},
        'entity_answers':[{'field':'budget','value':'100','all_groups':True}]},call,summary,'t','p','f'))
    assert summary['normal_answer_batches']==2 and summary['normal_answers_submitted']==2
    assert answered==2
