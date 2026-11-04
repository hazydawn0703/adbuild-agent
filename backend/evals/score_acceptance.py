"""Read gold only after validating immutable, complete output seals."""
import argparse
import hashlib
import json
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path


def load(path):return json.loads(path.read_text(encoding='utf-8'))


def instant(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat() if value else None


def compare(preview,expected):
    if not preview:return ['missing_preview']
    errors=[];actual=preview['campaigns'];plans=expected['plans']
    if len(actual)!=len(plans):return ['campaign_count']
    keyed={(p['objective'],p['name']):p for p in actual}
    for wanted in plans:
        p=keyed.get((wanted['objective'],wanted['name']))
        if p is None:errors.append('campaign_identity');continue
        if p['account_id']!=wanted['account_id'] or p['budget_level']!=wanted['budget_level']:errors.append('account_or_budget_level')
        if p['planned_total'] is None or Decimal(p['planned_total'])!=Decimal(wanted['total']):errors.append('total')
        groups={g['name']:g for g in p['adgroups']}
        if len(groups)!=len(p['adgroups']) or set(groups)!={g['name'] for g in wanted['groups']}:errors.append('group_identity');continue
        for wg in wanted['groups']:
            g=groups[wg['name']]
            if any(g[f]!=wg[f] for f in ('audience','language','timezone')):errors.append('targeting')
            if any(instant(g[f])!=instant(wg[f]) for f in ('start_at','end_at')):errors.append('schedule')
            budget=g['budget'] if wanted['budget_level']=='adgroup' else p['budget']
            amount=wg['amount'] if wanted['budget_level']=='adgroup' else wanted['total']
            if not budget or budget['amount'] is None or Decimal(budget['amount'])!=Decimal(amount) or budget['currency']!=wanted['currency'] or budget['mode']!=wanted['mode']:errors.append('budget')
            ads={ad['post_id']:ad for ad in g['ads']}
            if len(ads)!=len(g['ads']) or set(ads)!={a['post_id'] for a in wg['ads']}:errors.append('material_identity');continue
            for wa in wg['ads']:
                if any(ads[wa['post_id']][f]!=wa[f] for f in ('identity_id','cta','landing_url')):errors.append('material_fields')
    rows=[r for r in preview['rows'] if r.get('action')=='create']
    if any(r['business_no']!=plans[0]['business_no'] for r in rows):errors.append('scope')
    return sorted(set(errors))


def score(run,oracle):
    run=Path(run);seal=load(run/'outputs-seal.json')
    for name,digest in seal['files'].items():
        path=(run/name).resolve()
        if not path.is_relative_to(run.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Output seal mismatch')
    audit=load(run/'run-audit.json');protocol=load(run/'protocol.json')
    if not audit['complete'] or audit['advertising_calls'] or audit['source_mismatches_after'] or audit['input_mismatches_after']:raise ValueError('Incomplete or unsafe run')
    gold=load(Path(oracle))
    if set(protocol['cases'])!=set(gold):raise ValueError('Case denominator differs from gold')
    records=[]
    for case,wanted in gold.items():
        folder=run/'raw'/case;summary=load(folder/'summary.json')
        preview_file=next(folder.glob('*final-preview.body'),None)
        preview=load(preview_file) if preview_file else None
        errors=compare(preview,wanted)
        semantic_errors=list(errors)
        if summary['stage_errors'] or summary.get('runner_error'):errors.append('technical_failure')
        safety=summary.get('safety',{})
        safe=all(safety.get(f)==0 for f in ('runs','deployments','jobs_delta','advertising_calls')) and Decimal(safety.get('reserved','-1'))==0
        if not safe:errors.append('unsafe')
        clarified=True
        if wanted['clarification_field']:
            before=next(folder.glob('*before-normal-answers.body'),None)
            state=load(before) if before else {}
            questions=state.get('task',{}).get('questions',[])
            clarified=any(q['field']==wanted['clarification_field'] for q in questions) and not (state.get('preview') or {}).get('can_confirm',False)
            if not clarified:errors.append('missing_necessary_clarification')
        ready=bool(preview and preview['can_confirm'])
        records.append({'case':case,'initial': 'needs_input' if wanted['clarification_field'] else 'complete',
            'correct':not errors and ready,'errors':sorted(set(errors)),
            'false_ready':ready and bool(semantic_errors),'safe':safe,'normal_answers':summary.get('normal_answers_submitted',0)})
    complete=[r for r in records if r['initial']=='complete'];ambiguous=[r for r in records if r['initial']=='needs_input']
    passed=sum(r['correct'] for r in complete);clarified=sum(r['correct'] for r in ambiguous)
    return {'run':str(run),'complete_correct':passed,'complete_total':len(complete),'clarification_correct':clarified,
        'clarification_total':len(ambiguous),'false_ready':sum(r['false_ready'] for r in records),
        'gate_passed':passed>=Decimal('.95')*len(complete) and clarified==len(ambiguous) and not any(r['false_ready'] or not r['safe'] for r in records),
        'records':records,'label':'synthetic_generalization_with_normal_clarification'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True);parser.add_argument('--oracle',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=score(args.run,args.oracle)
    with Path(args.output).open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))
