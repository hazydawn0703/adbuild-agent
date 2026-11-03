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
    return {'label':expected.get('label','development'),'requirements':{'correct':len(common),'predicted':len(predicted),'expected':len(gold)},
        'fields':fields,'missed_questions':sorted(needed-asked),'unnecessary_questions':sorted(asked-needed),
        'manual_corrections':actual.get('manual_corrections',0),'clarification_rounds':actual.get('clarification_rounds',0),
        'complete':gold.keys()==predicted.keys() and all(f['correct'] for f in fields) and not needed-asked and not actual.get('manual_corrections',0)}
