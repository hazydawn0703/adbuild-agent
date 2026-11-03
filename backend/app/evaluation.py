"""Structured development/holdout scoring. Expected answers never enter model input."""
from decimal import Decimal
from datetime import datetime


def equal(field,expected,actual):
    if field=='budget':
        try: return Decimal(str(expected))==Decimal(str(actual))
        except Exception: return False
    if field in ('start_at','end_at'):
        try: return datetime.fromisoformat(expected)==datetime.fromisoformat(actual)
        except (ValueError,TypeError): return False
    return expected==actual


def score(expected,actual):
    gold={r['key']:r for r in expected['requirements']}
    predicted={r['key']:r for r in actual['requirements']}
    common=gold.keys() & predicted.keys()
    fields=[]
    for key in common:
        for field,value in gold[key]['fields'].items():
            fields.append({'key':key,'field':field,'correct':equal(field,value,predicted[key]['fields'].get(field))})
    needed=set(expected.get('questions',[]));asked=set(actual.get('questions',[]))
    history=[];sources=[]
    for key in common:
        if 'history' in gold[key]:
            target=set(gold[key]['history']['candidates']);found=set(predicted[key].get('history',{}).get('candidates',[]))
            history.append({'key':key,'correct':len(target&found),'expected':len(target),'predicted':len(found),
                'classification_correct':gold[key]['history']['classification']==predicted[key].get('history',{}).get('classification')})
        for field,refs in gold[key].get('sources',{}).items():
            sources.append({'key':key,'field':field,'correct':set(refs)<=set(predicted[key].get('sources',{}).get(field,[]))})
    safe=not actual.get('safety_violations',[])
    correct=gold.keys()==predicted.keys() and all(f['correct'] for f in fields) and needed==asked and not actual.get('manual_corrections',0)
    correct=correct and all(h['classification_correct'] and h['correct']==h['expected']==h['predicted'] for h in history) and all(s['correct'] for s in sources) and safe
    return {'label':expected.get('label','development'),'requirements':{'correct':len(common),'predicted':len(predicted),'expected':len(gold)},
        'fields':fields,'missed_questions':sorted(needed-asked),'unnecessary_questions':sorted(asked-needed),
        'history':history,'sources':sources,'safe':safe,
        'manual_corrections':actual.get('manual_corrections',0),'clarification_rounds':actual.get('clarification_rounds',0),
        'complete':correct}
