"""Post-seal semantic scoring of the supplied 111-case campaign pack.

Reads reference JSON as data. No reference code or document instructions execute.
The model driver never imports this module or reads these reference files.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from evals.score_regression import verify_seal
from evals.score_acceptance import instant


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def amount(value):return str(Decimal(str(value)).normalize()) if value is not None else None


def normalized_plan(plan,business,reference=False,flexible_rounding=False):
    budget=plan['budget'] or {}
    schedule=plan.get('schedule',{})
    groups=[];group_amounts=[]
    for group in plan['groups'] if reference else plan['adgroups']:
        group_budget=group.get('budget_amount') if reference else (group.get('budget') or {}).get('amount')
        group_amounts.append(amount(group_budget))
        normalized={'audience':group['audience'],'language':group['language'],
            'start':instant(group.get('start_at',schedule.get('start_at'))),
            'end':instant(group.get('end_at',schedule.get('end_at'))),
            'timezone':group.get('timezone',schedule.get('timezone')),
            'budget':None if flexible_rounding else amount(group_budget),
            'ads':sorted([{field:ad.get(field,'') for field in ('post_id','post_url','identity_id','cta','landing_url')}
                          for ad in group['ads']],key=lambda a:json.dumps(a,sort_keys=True))}
        groups.append(normalized)
    return {'name':plan['name'],'business_no':business,'account':plan['account_id'],'objective':plan['objective'],
        'level':budget.get('level') if reference else plan['budget_level'],
        'total':amount(budget.get('amount') if reference else plan['planned_total']),
        'currency':budget.get('currency') if reference else (budget or next((g['budget'] for g in plan['adgroups'] if g.get('budget')),{})).get('currency'),
        'mode':budget.get('mode') if reference else (budget or next((g['budget'] for g in plan['adgroups'] if g.get('budget')),{})).get('mode'),
        'groups':sorted(groups,key=lambda g:json.dumps(g,sort_keys=True)),
        'rounding_amounts':sorted(group_amounts,key=str) if flexible_rounding else None}


def compare(preview,reference,flexible_rounding=False):
    if not preview:return ['missing_preview']
    expected=[normalized_plan(p,p['business_no'],True,flexible_rounding) for p in reference['campaigns']]
    actual=[normalized_plan(r['campaign'],r['business_no'],False,flexible_rounding) for r in preview['rows'] if r.get('action')=='create']
    if len(expected)!=len(actual):return ['campaign_count_or_scope']
    identity=lambda p:(p['business_no'],p['account'],p['objective'],p['name'])
    expected.sort(key=identity);actual.sort(key=identity)
    errors=[]
    for want,got in zip(expected,actual):
        errors.extend(field for field in want if want[field]!=got[field])
    return sorted(set(errors))


def score(run,references,normal=False):
    run=Path(run);verify_seal(run)
    audit=load(run/'run-audit.json');protocol=load(run/'protocol.json')
    if not audit['complete'] or audit['source_mismatches_after'] or audit['input_mismatches_after'] or audit['advertising_calls']:
        raise ValueError('Run is incomplete or changed')
    records=[]
    aliases={'budget_level':{'structure'},'history_batch':{'scope','included','audience'},'post_url':{'post_url','post_id'}}
    for case in protocol['cases']:
        oracle=load(Path(references)/(case+'.json'));expected=oracle['expected'];folder=run/'raw'/case
        summary=load(folder/'summary.json')
        def body(suffix):
            paths=list(folder.glob('*'+suffix+'.body'));return load(paths[-1]) if paths else {}
        state=body('sealed-task-state');preview=body('final-preview')
        ready=bool(preview.get('can_confirm'));technical=bool(summary['stage_errors'] or summary.get('runner_error'))
        initial_state=body('before-normal-answers') if normal else state
        required=expected['required_initial_questions'];questions=initial_state.get('task',{}).get('questions',[])
        observed={q['field'] for q in questions}
        missing=[field for field in required if not (aliases.get(field,{field})&observed)]
        final=expected['final_result_for_grading'];initial=expected['initial_decision']
        errors=compare(preview,final,bool(expected.get('rounding_alternatives'))) if final else []
        false_ready=ready and (final is None or bool(errors) or initial=='clarify' and not normal)
        if normal and initial=='clarify':
            initially_blocked=not (initial_state.get('preview') or {}).get('can_confirm',False)
            result='correct_complete' if ready and not errors and not technical and not missing and initially_blocked else 'wrong_confirmable' if false_ready else 'incorrect_or_incomplete_clarification'
        elif initial=='clarify':result='correct_clarification' if not ready and not missing and not technical else 'incorrect_or_incomplete_clarification'
        elif final is None:result='correct_block' if not ready and not technical else 'technical_failure' if technical else 'wrong_confirmable'
        else:result='correct_complete' if ready and not errors and not technical else 'false_block' if not ready and not technical else 'technical_failure' if technical else 'wrong_confirmable'
        safety=summary.get('safety',{})
        safe=all(Decimal(str(safety.get(key,-1)))==0 for key in ('runs','deployments','reserved','jobs_delta','advertising_calls'))
        records.append({'case':case,'expected':initial,'result':result,'can_confirm':ready,'semantic_errors':errors,
            'false_ready':false_ready,'technical_errors':summary['stage_errors'],'required_questions':required,
            'missing_required_questions':missing,'observed_questions':sorted(observed),'safe':safe})
    return {'commit':load(run/'freeze.json')['commit'],'cases':len(records),'expected_classes':dict(Counter(r['expected'] for r in records)),
        'results':dict(Counter(r['result'] for r in records)),'false_ready':sum(r['false_ready'] for r in records),
        'safety_failures':sum(not r['safe'] for r in records),'label':'supplied_pack_normal_interaction_regression' if normal else 'supplied_pack_single_attempt_regression',
        'limits':['Only predeclared permitted answers supplied.' if normal else 'No normal clarification answers supplied in this lane.','Sources are covered by deterministic verifier tests; semantic comparison alone does not independently certify every source.','Business clock is frozen as recorded; original schedules are not rewritten.'],
        'records':records}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('run','references','output'):parser.add_argument('--'+field,required=True)
    parser.add_argument('--normal',action='store_true')
    args=parser.parse_args();result=score(args.run,args.references,args.normal)
    with Path(args.output).open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('records','limits')},ensure_ascii=False))
