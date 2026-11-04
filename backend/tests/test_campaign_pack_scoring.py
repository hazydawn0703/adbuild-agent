import hashlib
import json
from evals.score_campaign_pack import score
from test_acceptance_scoring import fixture


def test_normal_lane_requires_initial_clarification_then_correct_final_plan(tmp_path):
    run=tmp_path/'run';run.mkdir();refs=tmp_path/'references';refs.mkdir()
    preview,_=fixture();plan=preview['campaigns'][0]
    preview.update(can_confirm=True)
    preview['rows'][0]['campaign']=plan
    reference={'campaigns':[{'name':plan['name'],'business_no':'B1','account_id':plan['account_id'],'objective':plan['objective'],
        'budget':{**plan['budget'],'level':'campaign'},'groups':[{**g,'budget_amount':None} for g in plan['adgroups']]}]}
    def write(name,value):
        path=run/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')
    write('protocol.json',{'cases':['C1']});write('freeze.json',{'commit':'fixed'})
    write('run-audit.json',{'complete':True,'source_mismatches_after':[],'input_mismatches_after':[],'advertising_calls':0})
    write('raw/C1/summary.json',{'stage_errors':[],'safety':dict.fromkeys(['runs','deployments','reserved','jobs_delta','advertising_calls'],0)})
    write('raw/C1/01-before-normal-answers.body',{'task':{'questions':[{'field':'budget'}]},'preview':{'can_confirm':False}})
    write('raw/C1/02-sealed-task-state.body',{'task':{'questions':[]}})
    write('raw/C1/03-final-preview.body',preview)
    (refs/'C1.json').write_text(json.dumps({'expected':{'required_initial_questions':['budget'],'initial_decision':'clarify',
        'final_result_for_grading':reference}}),encoding='utf-8')
    def seal():write('outputs-seal.json',{'files':{p.relative_to(run).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file() and p.name!='outputs-seal.json'}})
    seal()
    assert score(run,refs,True)['results']=={'correct_complete':1}
    assert score(run,refs)['results']=={'incorrect_or_incomplete_clarification':1}
    write('raw/C1/01-before-normal-answers.body',{'task':{'questions':[]},'preview':{'can_confirm':True}});seal()
    assert score(run,refs,True)['results']=={'incorrect_or_incomplete_clarification':1}
